"""
Building a modified share configuration -- WITHOUT the ability to send it.

WHY THIS MODULE CANNOT TALK TO A DROBO
--------------------------------------
There is no per-share write command. The 103-command table has
`eCmdGetSharesConfig` to read, but the only way to *change* a share is
`eCmdSetConfig` (id 31), which takes an entire `<DRINASConfig>` document and
replaces what is there.

That is a blunt instrument pointed at somebody's only copy of their data. Send
a document missing a share and the share is gone. Send one missing the network
block and the Drobo may not come back on the network -- at which point the only
remedy is physical access and a factory reset, which loses the pack.

So this module builds the document and nothing else. It opens no socket and
imports nothing that can. Getting the construction provably right is a separate
problem from deciding to transmit it, and doing them in that order means the
risky decision is made once, deliberately, with working code already in hand
rather than as part of the same change.

THE INVARIANTS
--------------
Every function here is read-modify-write over a config the device just gave us,
and each enforces:

  * Only `DRIShareConfig` is ever touched. Network and admin blocks are carried
    through byte-identical.
  * No share may vanish as a side effect. Removing one is its own explicit
    call, and even then only one at a time.
  * The result is diffed against the input, and a change that touches anything
    unexpected raises rather than returning a document nobody reviewed.

WHAT IS KNOWN, AND WHAT IS NOT
------------------------------
`ShareUserAccess` is an integer. What the values mean is not in any Drobo
binary -- only Samba's own libraries mention `read list` / `write list`, so the
mapping is computed at runtime and cannot be recovered statically. So the
meanings are established one at a time, from captures of the original Dashboard:

  * `0` -- most likely NO ACCESS: every share observed with 0 was
    unreachable from Windows. NOT confirmed -- the maintainer could not verify
    that a mounted share was actually openable, so shares.ACCESS_LEVELS
    keeps it hedged. It is still writable, as "no access" is the safe
    direction to be wrong in.
  * `1` -- READ/WRITE. A 2026-08-05 capture of Drobo Dashboard 3.5.0 set a
    share the maintainer states is Read/Write and it carried code 1. Confirmed;
    see shares.CONFIRMED_ACCESS_CODES, the single source of truth this
    module reads.
  * `2` -- unconfirmed. Never observed on the wire, meaning unknown.

The rule this module follows: it will set `0`, any code whose meaning is
CONFIRMED (currently just `1`), or a code that ALREADY appears somewhere on
this device. It will NOT invent a code whose meaning is unknown -- offering a
guessed "read-only" as if it were a fact about someone's permissions is exactly
the mistake this refuses to make.

That `1` is now confirmed is what lets this actually FIX a locked share: a
device whose shares are all `0` has no non-zero code to copy, so before 1 was
confirmed the only reachable write was "set to no access". Now "grant
read/write" is a real, non-guessed option.
"""
from __future__ import annotations

import copy

from .shares import CONFIRMED_ACCESS_CODES

#: No access. Every share observed with `0` was unreachable. Kept as a named
#: constant because it is the one code this module will set unconditionally.
NO_ACCESS_CODE = "0"


class ShareEditError(Exception):
    """The requested change is unsafe or does not make sense."""


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


def _share_entries(block: dict) -> list:
    """The share list, always as a list even when the device sent one dict."""
    entries = (block.get("Shares") or {}).get("Share")
    if entries is None:
        return []
    return entries if isinstance(entries, list) else [entries]


def share_names(config: dict) -> list[str]:
    """Every share name in the document, in the order the device gave them."""
    return [(s.get("ShareName") or "").strip()
            for s in _share_entries(_share_block(config))
            if (s.get("ShareName") or "").strip()]


def _user_entries(share: dict) -> list:
    entries = (share.get("ShareUsers") or {}).get("ShareUser")
    if entries is None:
        return []
    return entries if isinstance(entries, list) else [entries]


def _assert_only_shares_changed(before: dict, after: dict) -> None:
    """
    Refuse to return a document that altered anything outside DRIShareConfig.

    This is the check that makes the whole module safe to trust. The write
    command replaces everything, so a bug that silently dropped the network
    block would take the Drobo off the network -- and would look, from the
    caller's side, exactly like a successful edit.
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
    """No share may disappear except the one explicitly being removed."""
    lost_share_names = set(share_names(before)) - set(share_names(after))
    if allow_removed is not None:
        lost_share_names.discard(allow_removed)
    if lost_share_names:
        raise ShareEditError(
            f"the edit would remove share(s) {sorted(lost_share_names)} -- "
            f"refusing. Removing a share is its own deliberate action.")


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

    Returns a new document. The input is never mutated.
    """
    edited_config = copy.deepcopy(config)
    share_block = _share_block(edited_config)

    # Allowed = no-access, plus every confirmed-meaning code, plus every code
    # already live on this device. The confirmed set is what makes "grant
    # read/write to a share that is currently locked to nobody" possible on a
    # device with no non-zero code to copy.
    allowed_codes = {NO_ACCESS_CODE} | set(CONFIRMED_ACCESS_CODES)
    for share in _share_entries(_share_block(config)):
        for user in _user_entries(share):
            code = str(user.get("ShareUserAccess", "")).strip()
            if code:
                allowed_codes.add(code)
    if str(access_code) not in allowed_codes:
        raise ShareEditError(
            f"access code {access_code!r} is neither confirmed nor already on "
            f"this device (allowed: {sorted(allowed_codes)}). This project "
            f"does not invent access codes -- what an unconfirmed code means "
            f"is unknown, and a guess must not be written to someone's "
            f"permissions.")

    target_share = None
    for share in _share_entries(share_block):
        if (share.get("ShareName") or "").strip() == share_name:
            target_share = share
            break
    if target_share is None:
        raise ShareEditError(f"no share named {share_name!r} on this device")

    users = _user_entries(target_share)
    for user in users:
        if ((user.get("ShareUsername") or "").strip().lower()
                == username.strip().lower()):
            user["ShareUserAccess"] = str(access_code)
            break
    else:
        users.append({"ShareUsername": username.strip(),
                      "ShareUserAccess": str(access_code)})

    # Normalise back to the device's own shape: one user is a dict, several a
    # list. Writing a list where the device expects a dict is exactly the kind
    # of malformed document that makes eCmdSetConfig dangerous.
    target_share["ShareUsers"] = {
        "ShareUser": users[0] if len(users) == 1 else users}

    _assert_only_shares_changed(config, edited_config)
    _assert_no_share_lost(config, edited_config)
    return edited_config


def revoke_user(config: dict, share_name: str, username: str) -> dict:
    """
    Take a user's access away from a share, by setting code 0.

    Deliberately does NOT delete the user entry. Setting "no access" is
    reversible by setting it back; deleting the row loses whatever code was
    there, and since we cannot interpret those codes we could not restore it
    faithfully afterwards.
    """
    return set_user_access(config, share_name, username, NO_ACCESS_CODE)


def describe_change(before: dict, after: dict) -> list[str]:
    """
    A plain-English list of what actually differs, for a confirmation prompt.

    Written for someone to READ before agreeing to a write, so it names shares
    and users rather than XML paths, and reports the raw access codes without
    pretending to know what they mean.
    """
    def access_by_share_and_user(config):
        access_by_key = {}
        for share in _share_entries(_share_block(config)):
            name = (share.get("ShareName") or "").strip()
            for user in _user_entries(share):
                access_by_key[(name,
                               (user.get("ShareUsername") or "").strip())] = \
                    str(user.get("ShareUserAccess", "")).strip()
        return access_by_key

    access_before = access_by_share_and_user(before)
    access_after = access_by_share_and_user(after)
    lines = []
    for key in sorted(set(access_before) | set(access_after)):
        share, user = key
        old_code, new_code = access_before.get(key), access_after.get(key)
        if old_code == new_code:
            continue
        if old_code is None:
            lines.append(f"{user} gains access to {share} (code {new_code})")
        elif new_code is None:
            lines.append(f"{user} is removed from {share}")
        elif new_code == NO_ACCESS_CODE:
            lines.append(f"{user} loses access to {share}")
        elif old_code == NO_ACCESS_CODE:
            lines.append(
                f"{user} is given access to {share} (code {new_code})")
        else:
            lines.append(
                f"{user} on {share}: access code {old_code} -> {new_code}")
    return lines
