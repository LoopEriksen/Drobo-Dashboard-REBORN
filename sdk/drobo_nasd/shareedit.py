"""
Building a modified share configuration --
WITHOUT the ability to send it.

WHY THIS MODULE CANNOT TALK TO A DROBO
--------------------------------------
There is no per-share write command. The 103-command table has
`eCmdGetSharesConfig` to read, but the only way to *change* a share is
`eCmdSetConfig` (id 31), which takes an entire `<DRINASConfig>` document
and replaces what is there.

That is a blunt instrument pointed at somebody's only copy of their
data. Send a document missing a share and the share is gone. Send one
missing the network block and the Drobo may not come back on the network
-- at which point the only remedy is physical access and a factory
reset, which loses the pack.

So this module builds the document and nothing else. It opens no socket
and imports nothing that can. Getting the construction provably right is
a separate problem from deciding to transmit it, and doing them in that
order means the risky decision is made once, deliberately, with working
code already in hand rather than as part of the same change.

THE INVARIANTS
--------------
Every function here is read-modify-write over a config the device just
gave us, and each enforces:

  * Only `DRIShareConfig` is ever touched. Network and admin blocks are
    carried through byte-identical.
  * No share may vanish as a side effect. Removing one is its own
    explicit call, and even then only one at a time.
  * The result is diffed against the input, and a change that touches
    anything unexpected raises rather than returning a document nobody
    reviewed.

WHAT IS KNOWN, AND WHAT IS NOT
------------------------------
`ShareUserAccess` is an integer. What the values mean is not in any
Drobo binary -- only Samba's own libraries mention `read list` / `write
list`, so the mapping is computed at runtime and cannot be recovered
statically. So the meanings are established one at a time, from captures
of the original Dashboard:

  * `0` -- most likely NO ACCESS: every share observed with 0 was
    unreachable from Windows. NOT confirmed -- the maintainer could not
    verify that a mounted share was actually openable, so
    shares.ACCESS_LEVELS keeps it hedged. It is still writable, as "no
    access" is the safe direction to be wrong in.
  * `1` -- READ/WRITE. A 2026-08-05 capture of Drobo Dashboard 3.5.0 set
    a share the maintainer states is Read/Write and it carried code 1.
    Confirmed; see shares.CONFIRMED_ACCESS_CODES, the single source of
    truth this module reads.
  * `2` -- unconfirmed. Never observed on the wire, meaning unknown.

The rule this module follows: it will set `0`, any code whose meaning is
CONFIRMED (currently just `1`), or a code that ALREADY appears somewhere
on this device. It will NOT invent a code whose meaning is unknown --
offering a guessed "read-only" as if it were a fact about someone's
permissions is exactly the mistake this refuses to make.

That `1` is now confirmed is what lets this actually FIX a locked share:
a device whose shares are all `0` has no non-zero code to copy, so
before 1 was confirmed the only reachable write was "set to no access".
Now "grant read/write" is a real, non-guessed option.
"""
from __future__ import annotations

import copy

from .shares import CONFIRMED_ACCESS_CODES

#: No access. Every share observed with `0` was unreachable. Kept as a
#: named constant because it is the one code this module will set
#: unconditionally.
NO_ACCESS_CODE = "0"


class ShareEditError(Exception):
    """
    The requested change is unsafe or does not make sense, or a write
    was shown not to have been applied.

    One class for every such answer, so a caller can show the message
    as it stands: each one says why. Catching it does NOT mean nothing
    was sent. The edits here raise it before any send, but sharewrite
    also raises it after an accepted send whose read-back does not
    match ("Received, not applied"), and its subclass
    ShareWriteOutcomeUnknownError means the write WAS sent. Never take
    it as "safe to resend".
    """


def _share_block(config: dict) -> dict:
    """Dig out DRIShareConfig, whatever wrapper depth it arrived in."""
    if not isinstance(config, dict):
        raise ShareEditError("configuration is not a document")
    if "DRIShareConfig" in config:
        return config["DRIShareConfig"] or {}
    inner = config.get("DRINASConfig")
    if isinstance(inner, dict) and "DRIShareConfig" in inner:
        return inner["DRIShareConfig"] or {}
    raise ShareEditError("no DRIShareConfig in this document")


def _share_entries(share_block: dict) -> list:
    """
    The share list, always as a list even when the device sent one dict.
    """
    entries = (share_block.get("Shares") or {}).get("Share")
    if entries is None:
        return []
    return entries if isinstance(entries, list) else [entries]


def share_names(config: dict) -> list[str]:
    """
    Every share name in the document, in the order the device gave them.

    A list in the device's order rather than a set because it is
    public and callers (the tests among them) rely on that order;
    _assert_no_share_lost, its only caller here, compares them as sets.
    """
    return [(s.get("ShareName") or "").strip()
            for s in _share_entries(_share_block(config))
            if (s.get("ShareName") or "").strip()]


def _user_entries(share: dict) -> list:
    """
    The share's user rows, always as a list: the device sends a single
    user as a dict and several as a list, and every caller here wants
    to walk them the same way.
    """
    entries = (share.get("ShareUsers") or {}).get("ShareUser")
    if entries is None:
        return []
    return entries if isinstance(entries, list) else [entries]


def _assert_only_shares_changed(before: dict, after: dict) -> None:
    """
    Refuse to return a document
    that altered anything outside DRIShareConfig.

    This is the check that makes the whole module safe to trust. The
    write command replaces everything, so a bug that silently dropped
    the network block would take the Drobo off the network -- and would
    look, from the caller's side, exactly like a successful edit.
    """
    before_outside_shares = copy.deepcopy(before)
    after_outside_shares = copy.deepcopy(after)
    for document in (before_outside_shares, after_outside_shares):
        inner = document.get("DRINASConfig")
        if isinstance(inner, dict):
            inner.pop("DRIShareConfig", None)
        document.pop("DRIShareConfig", None)
    if before_outside_shares != after_outside_shares:
        raise ShareEditError(
            "the edit changed something outside DRIShareConfig -- refusing. "
            "This is the check that stops a share edit taking the Drobo off "
            "the network.")


def _assert_no_share_lost(before: dict, after: dict,
                          allow_removed: str | None = None) -> None:
    """
    No share may disappear except the one explicitly being removed.
    """
    lost_share_names = set(share_names(before)) - set(share_names(after))
    if allow_removed is not None:
        lost_share_names.discard(allow_removed)
    if lost_share_names:
        raise ShareEditError(
            f"the edit would remove share(s) {sorted(lost_share_names)} -- "
            f"refusing. Removing a share is its own deliberate action.")


def _allowed_access_codes(config: dict) -> set[str]:
    """
    No-access, plus every confirmed-meaning code, plus every code
    already live on this device. The confirmed set is what makes "grant
    read/write to a share that is currently locked to nobody" possible
    on a device with no non-zero code to copy.
    """
    allowed_codes = {NO_ACCESS_CODE} | set(CONFIRMED_ACCESS_CODES)
    live_codes = (str(user.get("ShareUserAccess", "")).strip()
                  for share in _share_entries(_share_block(config))
                  for user in _user_entries(share))
    allowed_codes.update(code for code in live_codes if code)
    return allowed_codes


def _find_share_entry(share_block: dict, share_name: str) -> dict:
    """The one share whose stripped name is exactly `share_name`,
    or raise.

    Two shares with that exact name are refused, as
    sharewrite._resolve_share refuses them: editing the first and
    leaving the other is a change to whichever share the device happened
    to list first. Until 2026-11-02 this took the first.
    """
    matching_shares = [share for share in _share_entries(share_block)
                       if (share.get("ShareName") or "").strip()
                       == share_name]
    if len(matching_shares) > 1:
        raise ShareEditError(
            f"{len(matching_shares)} shares are named exactly "
            f"{share_name!r}; ambiguous -- refusing rather than editing the "
            f"first one and hoping.")
    if not matching_shares:
        raise ShareEditError(f"no share named {share_name!r} on this device")
    return matching_shares[0]


def _set_or_append_user(users: list, username: str,
                        access_code: str) -> None:
    """
    Change the one row folding to `username`, or append a new row when
    there is none.

    Several rows folding to the same name are refused, whichever of
    them matches exactly, as sharewrite._find_single_user_row refuses
    them: changing one and leaving the others is how a revoke half
    succeeds. Until 2026-11-02 this changed the first.
    """
    folded_username = username.strip().lower()
    matching_users = [user for user in users
                      if (user.get("ShareUsername") or "").strip().lower()
                      == folded_username]
    if len(matching_users) > 1:
        raise ShareEditError(
            f"{len(matching_users)} user entries fold to {username!r}; "
            f"ambiguous -- refusing rather than changing one and leaving "
            f"the others. Resolve the duplicates on the device first.")
    if matching_users:
        matching_users[0]["ShareUserAccess"] = str(access_code)
        return
    users.append({"ShareUsername": username.strip(),
                  "ShareUserAccess": str(access_code)})


def _set_or_add_user_entry(target_share: dict, username: str,
                           access_code: str) -> None:
    """Set the user's code on `target_share`, adding the user if needed,
    and store the rows back in the device's own shape.
    """
    users = _user_entries(target_share)
    _set_or_append_user(users, username, access_code)

    # Normalise back to the device's own shape: one user is a dict,
    # several a list. Writing a list where the device expects a dict is
    # exactly the kind of malformed document that makes eCmdSetConfig
    # dangerous.
    target_share["ShareUsers"] = {
        "ShareUser": users[0] if len(users) == 1 else users}


def set_user_access(config: dict, share_name: str, username: str,
                    access_code: str) -> dict:
    """
    Set one user's access on one share, and change nothing else.

    `access_code` must be `"0"` (no access), a code whose meaning is
    CONFIRMED (shares.CONFIRMED_ACCESS_CODES -- currently `"1"`,
    read/write), or a code that ALREADY appears somewhere in this
    document. A code that is none of these is refused: what an
    unconfirmed code means is a guess, and this will not write a guess
    to someone's permissions. See the module docstring.

    Returns a new document. The input is never mutated, so the caller
    still holds the original to compare against and to describe the
    change from.
    """
    edited_config = copy.deepcopy(config)
    share_block = _share_block(edited_config)

    allowed_codes = _allowed_access_codes(config)
    if str(access_code) not in allowed_codes:
        raise ShareEditError(
            f"access code {access_code!r} is neither confirmed nor already on "
            f"this device (allowed: {sorted(allowed_codes)}). This project "
            f"does not invent access codes -- what an unconfirmed code means "
            f"is unknown, and a guess must not be written to someone's "
            f"permissions.")

    target_share = _find_share_entry(share_block, share_name)
    _set_or_add_user_entry(target_share, username, access_code)

    _assert_only_shares_changed(config, edited_config)
    _assert_no_share_lost(config, edited_config)
    return edited_config


def revoke_user(config: dict, share_name: str, username: str) -> dict:
    """
    Take a user's access away from a share, by setting code 0.

    Deliberately does NOT delete the user entry. Setting "no access" is
    reversible by setting it back; deleting the row loses whatever code
    was there, and since we cannot interpret those codes we could not
    restore it faithfully afterwards.
    """
    return set_user_access(config, share_name, username, NO_ACCESS_CODE)


def _access_by_share_and_user(config: dict) -> dict:
    """
    Every user row's access code, keyed by (share name, user name), so
    two documents can be compared row by row whatever order the device
    listed them in.
    """
    access_by_key = {}
    for share in _share_entries(_share_block(config)):
        share_name = (share.get("ShareName") or "").strip()
        for user in _user_entries(share):
            access_code = str(user.get("ShareUserAccess", "")).strip()
            user_name = (user.get("ShareUsername") or "").strip()
            access_by_key[(share_name, user_name)] = access_code
    return access_by_key


def describe_change(before: dict, after: dict) -> list[str]:
    """
    A plain-English list of what actually differs,
    for a confirmation prompt.

    Written for someone to READ before agreeing to a write, so it names
    shares and users rather than XML paths, and reports the raw access
    codes without pretending to know what they mean.
    """
    access_before = _access_by_share_and_user(before)
    access_after = _access_by_share_and_user(after)
    change_descriptions = []
    for key in sorted(set(access_before) | set(access_after)):
        share_name, user_name = key
        old_code, new_code = access_before.get(key), access_after.get(key)
        if old_code == new_code:
            continue
        change_descriptions.append(_describe_one_change(
            share_name, user_name, old_code, new_code))
    return change_descriptions


def _describe_one_change(share_name: str, user_name: str,
                         old_code: str | None,
                         new_code: str | None) -> str:
    """One line of describe_change, for a row whose code differs."""
    if old_code is None:
        return f"{user_name} gains access to {share_name} (code {new_code})"
    if new_code is None:
        return f"{user_name} is removed from {share_name}"
    if new_code == NO_ACCESS_CODE:
        return f"{user_name} loses access to {share_name}"
    if old_code == NO_ACCESS_CODE:
        return (f"{user_name} is given access to {share_name} "
                f"(code {new_code})")
    return (f"{user_name} on {share_name}: access code {old_code} -> "
            f"{new_code}")
