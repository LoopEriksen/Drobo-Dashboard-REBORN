"""
Share writes on a real Drobo: permissions, create, delete.

shareedit.py builds and validates a modified share config but, by
deliberate design, cannot reach the network. This module is the
counterpart that does: it performs the change SURGICALLY on the device's
own live XML and sends it through config_cmd.set_config_raw.

Three writes live here,
each mirroring a capture of the original Dashboard:

  * set_share_user_access / revoke_share_user -- one user's access code
    on one share (captured 2026-08-05, VERIFIED on hardware 2026-08-12).
  * create_share -- a new share appended to the config (captured
    2026-08-25: the Dashboard creating a throwaway share named
    ZZTESTZZ).
  * delete_share -- a share removed from the config, with the
    Dashboard's <ShareDeleteConfirm>Y</ShareDeleteConfirm> appended
    (same 2026-08-25 capture, deleting that throwaway). DESTRUCTIVE:
    everything stored in the share is expected to be lost.

The create and delete shapes are CONFIRMED for the captured case -- one
Dashboard, one device, one day -- not a general spec, and they are
mirrored byte-for-byte in structure with only the share name (and, for
create, the volume name) substituted.

WHY SURGICAL, NOT DICT ROUND-TRIP
---------------------------------
eCmdSetConfig replaces the ENTIRE DRIShareConfig. Send a document
missing a share and that share is gone; send one with a mangled field
and the device stores the mangle. shareedit works on a parsed dict, and
a dict cannot promise to reserialize to what the device sent -- it drops
attributes, collapses <UserList/>, reorders nothing but guarantees
nothing. So this does not reserialize a dict. It takes the device's
actual XML element, changes the one <ShareUserAccess> text node that
must change, and sends everything else back exactly as it arrived. Every
share, ConfigVersionBasis, UserList and formatting the device chose are
preserved because they are never touched.

WHAT ACCESS CODES ARE ALLOWED
-----------------------------
The same rule as shareedit: `0` (most likely no access -- NOT confirmed,
and written anyway because no access is the safe direction to be wrong
in), any code whose meaning is CONFIRMED (shares.CONFIRMED_ACCESS_CODES
-- currently only `1`, read/write), or a code already live on this
device. An unconfirmed code is a guess and is refused. See shareedit's
module docstring for why 1 being confirmed is what makes unlocking an
all-zero device possible.

STAKES
------
This is the highest-consequence write in the SDK: it changes access
control on somebody's only copy of their data. It reads first, changes
one node, asserts no share was lost, sends, and (by default) reads back
to confirm. It should be run against real hardware only deliberately,
and confirmed against a capture of the original Dashboard doing the same
edit before it is trusted blindly.

THE DEVICE'S WORD IS NOT THE ARBITER; ITS STATE IS
--------------------------------------------------
Two failures measured on hardware point the same way. A write can be
ACCEPTED and not applied (spin-down 0, 2026-08-12), and a write can be
applied and never acknowledged -- 2026-08-25/26, a share create the 5N
stored after closing the socket without answering. Every function here
therefore decides its outcome from a read-back, and hands config_cmd
that same read-back to arbitrate an unanswered write (see
config_cmd._send_section_document). No function here ever re-sends:
retrying a write that already applied is the harm.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from xml.etree import ElementTree

from . import config_cmd
from .shares import CONFIRMED_ACCESS_CODES
from .shareedit import NO_ACCESS_CODE, ShareEditError

_SHARES_SECTION = "shares"


class ShareWriteOutcomeUnknownError(ShareEditError):
    """
    The write went out and its outcome cannot be established, because
    the read-back that would establish it could not be completed
    (transport failure, timeout). Two routes reach here, and both mean
    the same thing to a caller:

      * the device ACCEPTED the write and the confirming read-back
        failed;
      * the device never acknowledged the write at all (it closed the
        connection) and the ARBITRATING read-back failed too --
        config_cmd's ConfigWriteOutcomeUnknownError, translated into
        this module's vocabulary.

    Distinct from ShareEditError proper because the two demand opposite
    responses: a refused write may be retried; a write of unknown
    outcome must NOT be blindly retried -- it may well have applied, and
    the right move is to re-read first.
    """


class _NoChangeNeeded(Exception):
    """Internal signal: the row already holds the requested code;
    skip the send.
    """


def _resolve_share(share_config: ElementTree.Element,
                   share_name: str) -> ElementTree.Element:
    """
    The one <Share> that `share_name` refers to, or raise.

    An exact-case name match wins outright. Failing that, the name must
    fold (case-insensitively) to exactly one share. Two shares differing
    only by case cannot be disambiguated by a case-insensitive request,
    and taking the first hit edits and verifies the WRONG share while
    reporting success -- the 2026-08-12 audit's confirmed high finding.
    Same rule as user rows.

    Two shares with the identical name are refused for the same reason,
    with a message of their own. Until 2026-11-02 they reached the
    case-fold message below, which says none of them matched exactly,
    when every one of them did.
    """
    shares = _share_elements(share_config)
    exact_matches = [s for s in shares
                     if _child_text(s, "ShareName") == share_name]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ShareEditError(
            f"{len(exact_matches)} shares are named exactly {share_name!r}; "
            f"ambiguous -- refusing rather than editing the first one and "
            f"hoping.")
    case_folded_matches = [
        s for s in shares
        if _child_text(s, "ShareName").lower() == share_name.lower()]
    if len(case_folded_matches) == 1:
        return case_folded_matches[0]
    existing_names = [n for n in (_child_text(s, "ShareName") for s in shares)
                      if n]
    if not case_folded_matches:
        raise ShareEditError(
            f"no share named {share_name!r} on this device "
            f"(have: {existing_names})")
    folded_names = [_child_text(s, "ShareName") for s in case_folded_matches]
    raise ShareEditError(
        f"{len(case_folded_matches)} shares fold to {share_name!r} "
        f"({folded_names}) and none matches exactly. Ambiguous -- refusing "
        f"rather than editing the first one and hoping. Give the exact name.")


def _assert_well_formed(share: ElementTree.Element, share_name: str) -> None:
    """Refuse a share whose structure this module cannot
    edit unambiguously.
    """
    name_elements = share.findall("ShareName")
    if len(name_elements) != 1:
        raise ShareEditError(
            f"share {share_name!r} carries {len(name_elements)} <ShareName> "
            f"elements; refusing a share whose identity is not a single "
            f"element.")
    users_containers = share.findall("ShareUsers")
    if len(users_containers) > 1:
        raise ShareEditError(
            f"share {share_name!r} carries {len(users_containers)} "
            f"<ShareUsers> containers; refusing -- a new user could land in "
            f"either.")


def _share_elements(
        share_config: ElementTree.Element) -> list[ElementTree.Element]:
    """Every <Share> element under <Shares>,
    whatever nesting the device used.
    """
    shares_container = share_config.find("Shares")
    if shares_container is None:
        return []
    return shares_container.findall("Share")


def _allowed_codes(share_config: ElementTree.Element) -> set[str]:
    """0, every confirmed code,
    and every code already present on the device.
    """
    allowed_codes = {NO_ACCESS_CODE} | set(CONFIRMED_ACCESS_CODES)
    for access_element in share_config.iter("ShareUserAccess"):
        code = (access_element.text or "").strip()
        if code:
            allowed_codes.add(code)
    return allowed_codes


def _share_names(share_config: ElementTree.Element) -> list[str]:
    """
    The stripped name of every <Share> under <Shares>, in document
    order. Order matters: the edits compare this list before and after
    to prove that only the intended share moved.
    """
    return [_child_text(s, "ShareName")
            for s in _share_elements(share_config)]


def _normalized_access_request(share_name: str, username: str,
                               access_code: str) -> tuple[str, str, str]:
    """
    Strip the three inputs of an access edit and refuse what no edit
    could use. Checked before the device is read, so a typo costs no
    round trip and never reaches the write path.
    """
    share_name = (share_name or "").strip()
    username = (username or "").strip()
    access_code = str(access_code).strip()
    if not share_name:
        raise ShareEditError("no share name given")
    if not username:
        raise ShareEditError("no username given")
    if not access_code.isdigit():
        raise ShareEditError(f"access code {access_code!r} is not a number")
    return share_name, username, access_code


def _refuse_unconfirmed_code(share_config: ElementTree.Element,
                             access_code: str) -> None:
    """
    Refuse an access code that is neither confirmed nor already live on
    this device: an unconfirmed code is a guess, and a guess must not
    be written to somebody's permissions.
    """
    allowed_codes = _allowed_codes(share_config)
    if access_code in allowed_codes:
        return
    raise ShareEditError(
        f"access code {access_code!r} is neither confirmed nor "
        f"already on this device (allowed: {sorted(allowed_codes)}). "
        "This does not invent access codes -- an unconfirmed code is "
        "a guess and must not be written to someone's permissions.")


def _users_container(target_share: ElementTree.Element) -> ElementTree.Element:
    """
    The share's <ShareUsers> container, added empty when the share has
    none yet, so a first user always has somewhere to land.
    """
    users_container = target_share.find("ShareUsers")
    if users_container is None:
        users_container = ElementTree.SubElement(target_share,
                                                 "ShareUsers")
    return users_container


def _find_single_user_row(users_container: ElementTree.Element,
                          share_name: str,
                          username: str) -> ElementTree.Element | None:
    """
    The one <ShareUser> row folding to `username`, None when the user
    is not on this share yet, or raise when several rows fold to it.

    More than one entry folding to the same name is REFUSED, not
    reconciled. Changing the first and leaving the rest is how a revoke
    silently half-succeeds: set one "Everyone" row to 0 while a second
    still grants read/write, then report success. Which of two
    colliding rows is authoritative -- or whether the device even
    treats "Everyone" and "everyone" as one user -- is a guess, and a
    guess must not decide access on somebody's only copy of their data.
    Refuse and let a human resolve it.
    """
    matching_users = [
        u for u in users_container.findall("ShareUser")
        if _child_text(u, "ShareUsername").lower() == username.lower()]
    if len(matching_users) > 1:
        raise ShareEditError(
            f"share {share_name!r} has {len(matching_users)} user entries "
            f"folding to {username!r}. Duplicate or case-colliding rows "
            "are ambiguous to reconcile safely -- changing one and "
            "leaving the others would silently leave the wrong access in "
            "place. Resolve the duplicates on the device first.")
    if not matching_users:
        return None
    return matching_users[0]


def _set_row_access(user_row: ElementTree.Element, share_name: str,
                    username: str, access_code: str) -> None:
    """
    Change an existing user's code -- the pure single-node edit.

    A row carrying two <ShareUserAccess> children is refused, because
    changing one would leave the other on the wire. Raises
    _NoChangeNeeded when the row already holds `access_code`.
    """
    access_elements = user_row.findall("ShareUserAccess")
    if len(access_elements) > 1:
        raise ShareEditError(
            f"user {username!r} on {share_name!r} carries "
            f"{len(access_elements)} <ShareUserAccess> elements; "
            "changing one would leave the other on the wire. Refusing "
            "a row this module cannot edit unambiguously.")
    access_element = access_elements[0] if access_elements else None
    if access_element is None:
        access_element = ElementTree.SubElement(user_row, "ShareUserAccess")
    # A no-op write is all risk and no change: it re-sends the entire
    # share config (TOCTOU window, whole-document replace) to set a
    # value that is already set. Signal it and let the caller skip the
    # send entirely.
    if (access_element.text or "").strip() == access_code:
        raise _NoChangeNeeded()
    access_element.text = access_code


def _add_user_row(users_container: ElementTree.Element, username: str,
                  access_code: str) -> None:
    """Add a <ShareUser> row for a user who is not on this share yet,
    and nothing else.
    """
    new_user = ElementTree.SubElement(users_container, "ShareUser")
    ElementTree.SubElement(new_user, "ShareUsername").text = username
    ElementTree.SubElement(new_user, "ShareUserAccess").text = access_code


def _set_or_add_user_access(users_container: ElementTree.Element,
                            share_name: str, username: str,
                            access_code: str) -> None:
    """
    Change an existing user's code, or add the user if they are not on
    this share yet. Duplicate rows are refused first (see
    _find_single_user_row).
    """
    user_row = _find_single_user_row(users_container, share_name, username)
    if user_row is None:
        _add_user_row(users_container, username, access_code)
        return
    _set_row_access(user_row, share_name, username, access_code)


def _assert_share_names_unchanged(names_before: list[str],
                                  names_after: list[str]) -> None:
    """
    Belt and braces: surgical editing cannot drop a share, but assert
    it, because the cost of being wrong here is a share vanishing from
    a device that may hold the only copy of somebody's data.
    """
    if names_before == names_after:
        return
    raise ShareEditError(
        "the edit changed the set of shares -- refusing. This should "
        "be impossible with a surgical edit and means "
        "something is wrong.")


def _confirm_by_fresh_read(write_summary: dict,
                           reads_back: Callable[[], bool],
                           accepted_write: str, likely_outcome: str,
                           not_applied: str) -> dict:
    """
    Settle an ACCEPTED write by reading the device back, and return
    `write_summary` with "verified" filled in.

    RESULT_ACCEPTED means received, not applied, so only the read-back
    can say the write took. A transport failure during that read must
    not be reported as if the write failed -- that is the opposite of
    the truth, and it would tempt a caller to retry a write that
    already took -- so it raises ShareWriteOutcomeUnknownError, naming
    the situation exactly: accepted, unverified. A read-back that
    answers and disagrees raises ShareEditError.

    Each write passes its own wording, because the messages differ per
    write: `accepted_write` and `likely_outcome` complete the
    unverified message, and `not_applied` completes the "received, not
    applied" one. The full texts, for anyone searching for a message
    they saw:

      "<accepted_write>, but the read-back to confirm it could not be
      completed (<ExceptionType>: <exc>). <likely_outcome> Re-read the
      share config before deciding anything -- and do NOT simply retry
      the write."

      "the device accepted the write but <not_applied>. Received, not
      applied."

    Only OSError and config_cmd.ConfigError from the read-back become
    ShareWriteOutcomeUnknownError; anything else the read-back raises
    propagates as it is.
    """
    try:
        write_summary["verified"] = reads_back()
    except (OSError, config_cmd.ConfigError) as exc:
        write_summary["verified"] = None
        raise ShareWriteOutcomeUnknownError(
            f"{accepted_write}, but the read-back to confirm it could not "
            f"be completed ({type(exc).__name__}: {exc}). {likely_outcome} "
            "Re-read the share config before deciding anything -- and do "
            "NOT simply retry the write.") from exc
    if not write_summary["verified"]:
        raise ShareEditError(
            f"the device accepted the write but {not_applied}. "
            "Received, not applied.")
    return write_summary


def set_share_user_access(host: str, esa_id: str, share_name: str,
                          username: str, access_code: str,
                          port: int = config_cmd.DEFAULT_PORT,
                          timeout: float = 10.0, verify: bool = True) -> dict:
    """
    Set one user's access on one share, changing nothing else,
    and send it.

    `access_code` must be "0", a CONFIRMED code, or a code already on
    the device (see the module docstring). If `username` already has an
    entry on the share, only its <ShareUserAccess> text changes -- a
    single-node edit. If not, a new <ShareUser> is added to that share
    and nothing else moves. The edit is surgical because the device
    replaces the whole share config with whatever it is sent (see WHY
    SURGICAL in the module docstring), and a write that would change
    nothing is skipped, because re-sending the whole config to set a
    value already set is all risk and no change.

    `verify` (default True) re-reads the share config afterward and
    confirms the user's access is now `access_code`; RESULT_ACCEPTED
    means received, not applied. Returns config_cmd.set_config_raw's
    summary plus verify info -- including "acknowledged", which is False
    when the device never answered and the read-back is what established
    the change (see set_config_raw). `timeout` is in seconds.

    Raises ShareEditError for a disallowed code, an unknown share, or a
    lost share (which surgical editing should make impossible, but is
    asserted); ShareWriteOutcomeUnknownError when the outcome cannot be
    established; config_cmd.ConfigWriteError propagates for a refused or
    unreachable device.
    """
    share_name, username, access_code = _normalized_access_request(
        share_name, username, access_code)

    def edit_share_config(share_config: ElementTree.Element) -> None:
        _refuse_unconfirmed_code(share_config, access_code)
        names_before = _share_names(share_config)

        # Resolve the target share the same way user rows are resolved:
        # an exact-case match wins; otherwise the name must fold to
        # exactly ONE share. Two shares differing only by case ("Photos"
        # and "photos") are ambiguous, and taking the first hit -- which
        # this did until the 2026-08-12 audit -- edits the wrong share,
        # verifies the wrong share, and reports success. Whether the
        # firmware even permits two such names is unmeasured; the code
        # must not assume it cannot happen.
        target_share = _resolve_share(share_config, share_name)

        # A malformed row -- two <ShareName>s, or a ShareUser carrying
        # two <ShareUserAccess> children -- would be half-edited on the
        # wire. Refuse anything that is not exactly the shape we know
        # how to edit.
        _assert_well_formed(target_share, share_name)

        users_container = _users_container(target_share)
        _set_or_add_user_access(users_container, share_name, username,
                                access_code)
        _assert_share_names_unchanged(names_before,
                                      _share_names(share_config))

    def access_reads_back() -> bool:
        return _access_reads_back_as(host, esa_id, share_name, username,
                                     access_code, port, timeout)

    # The arbiter is passed even when verify is False. `verify` chooses
    # whether to pay for a confirming read on the normal path; it is not
    # permission to report a write of unknown outcome as a failure.
    try:
        write_summary = config_cmd.set_config_raw(
            host, esa_id, _SHARES_SECTION, edit_share_config, port=port,
            timeout=timeout, readback=access_reads_back)
    except _NoChangeNeeded:
        # The row already held access_code. Nothing was sent -- and that
        # is the correct outcome, not a failure. acknowledged is None
        # because there was no write for the device to acknowledge.
        return {"section": _SHARES_SECTION, "unchanged": True,
                "verified": True, "acknowledged": None,
                "note": f"{username!r} on {share_name!r} already has access "
                        f"{access_code}; nothing was written."}
    except config_cmd.ConfigWriteOutcomeUnknownError as exc:
        raise ShareWriteOutcomeUnknownError(
            f"the write setting {username!r} to access {access_code} on "
            f"{share_name!r} WAS sent, but neither the device's "
            "acknowledgement nor a read-back could settle whether it applied "
            f"({exc}). The access MAY now be changed. Re-read the share "
            "config before deciding anything -- and do NOT simply "
            "retry the write."
        ) from exc

    # Stop here without a fresh read in two cases: config_cmd already
    # settled this write by reading the device back (its verdict IS the
    # verification -- reading again only invites a second answer), or
    # the caller asked for no verification at all.
    if config_cmd.decided_by_readback(write_summary) or not verify:
        return write_summary

    return _confirm_by_fresh_read(
        write_summary, access_reads_back,
        accepted_write=(f"the device ACCEPTED the write for {username!r} "
                        f"on {share_name!r} (access {access_code})"),
        likely_outcome="The change most likely applied.",
        not_applied=(f"{username!r} on {share_name!r} does not read back "
                     f"as access {access_code}"))


def revoke_share_user(host: str, esa_id: str, share_name: str,
                      username: str, port: int = config_cmd.DEFAULT_PORT,
                      timeout: float = 10.0, verify: bool = True) -> dict:
    """
    Take a user's access away by setting code 0.
    Reversible: set it back.

    Code 0 most likely means no access, but that meaning is NOT
    confirmed (shares.CONFIRMED_ACCESS_CODES holds only 1); every share
    seen with it was unreachable from Windows, and no access is the safe
    way to be wrong.

    Does NOT delete the user entry -- setting code 0 can be undone, but
    a deleted row loses whatever code it held, and this module would
    rather keep a reversible change than a tidy one.
    """
    return set_share_user_access(host, esa_id, share_name, username,
                                 NO_ACCESS_CODE, port=port, timeout=timeout,
                                 verify=verify)


# The compact single-line <Share> node the original Dashboard sent when
# it created a share (captured 2026-08-25). Substituting the share and
# volume names is the ONLY liberty taken. Note what the captured node
# does and does not carry: a <VolumeName> (existing shares' stored nodes
# have none), NO <ShareState>, an empty OPEN-CLOSE
# <ShareMaxTMSizeGB></ShareMaxTMSizeGB> pair (which is why create_share
# splices a string -- ElementTree can only emit <ShareMaxTMSizeGB />),
# and an empty <ShareUsers/>: a new share starts with no access for
# anyone.
_NEW_SHARE_NODE = ("<Share><ShareName>{name}</ShareName>"
                   "<VolumeName>{volume}</VolumeName>"
                   "<TimeMachineEnabled>0</TimeMachineEnabled>"
                   "<ShareMaxTMSizeGB></ShareMaxTMSizeGB>"
                   "<ShareUsers/></Share>")

# What this project accepts as a NEW share's name. Deliberately narrower
# than the device may allow: every observed share name fits it, and
# nothing in it ever needs XML escaping -- the captured node carries the
# name as plain text, and an escaped name has never been observed on the
# wire.
_SHARE_NAME_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z _-]{0,26}$")
_SHARE_NAME_RULE = ("1-27 characters: letters, digits, spaces, underscores or "
                    "hyphens, starting with a letter or digit")


def _derive_volume_name(share_config: ElementTree.Element) -> str:
    """
    The volume a new share should live on, from the device's own data.

    The captured create node names its volume (Volume1 on the captured
    unit), but the read config only mentions a volume where a stored
    share happens to carry one -- the captured unit's existing shares
    carry none. So: every distinct <VolumeName> in the freshly-read
    config is a candidate. Exactly one and it is used; zero or several
    and this refuses, because picking one would be a guess written to
    the device. The caller passes `volume` explicitly instead, from data
    it trusts.
    """
    volume_names = sorted({text for text in
                           ((v.text or "").strip()
                            for v in share_config.iter("VolumeName"))
                           if text})
    if len(volume_names) == 1:
        return volume_names[0]
    if not volume_names:
        raise ShareEditError(
            "the device's share config names no volume, so there is nothing "
            "to derive the new share's <VolumeName> from. Pass volume= "
            "explicitly -- this will not guess.")
    raise ShareEditError(
        f"the device's share config names {len(volume_names)} volumes "
        f"({volume_names}); which one the new share should live on is "
        f"ambiguous. Pass volume= explicitly -- this will not guess.")


def _validated_new_share_name(share_name: str) -> str:
    """
    `share_name` exactly as given, or raise.

    A name with surrounding whitespace is refused rather than silently
    renamed: every stored-name comparison in this module strips, so a
    name that does not survive a strip could never be verified after
    writing. The pattern is deliberately narrower than the device may
    allow (see _SHARE_NAME_PATTERN).
    """
    share_name = share_name or ""
    if share_name != share_name.strip():
        raise ShareEditError(
            f"invalid share name {share_name!r}: surrounding whitespace is "
            f"refused rather than silently stripped -- every stored-name "
            f"comparison in this module strips, so such a name could never "
            f"be verified after writing.")
    if not _SHARE_NAME_PATTERN.match(share_name):
        raise ShareEditError(
            f"invalid share name {share_name!r}: must be {_SHARE_NAME_RULE}. "
            f"This rule is deliberately narrower than the device may allow.")
    return share_name


def _validated_volume_text(volume: str | None) -> str | None:
    """
    The stripped volume name, None when none was given, or raise for a
    name carrying an XML-special character: the captured node carries
    the volume as plain text, and what the device's parser does with an
    escaped one has never been observed.
    """
    volume = (volume or "").strip() or None
    if volume is not None and any(ch in volume for ch in "&<>"):
        raise ShareEditError(
            f"volume name {volume!r} contains an XML-special character. The "
            f"captured create node carries the volume as plain text, and an "
            f"escaped volume name has never been observed on the wire -- "
            f"refusing rather than guessing what the device's parser accepts.")
    return volume


def _stripped_name_texts(element: ElementTree.Element) -> list[str]:
    """The stripped text of every <ShareName> anywhere under `element`,
    in document order.
    """
    return [(e.text or "").strip() for e in element.iter("ShareName")]


def _refuse_case_clash(names_before: list[str], share_name: str) -> None:
    """
    Refuse a new name that case-folds onto an existing share: this
    module could never address such a pair apart again (see
    _resolve_share).
    """
    clashing_names = [n for n in names_before
                      if n.lower() == share_name.lower()]
    if not clashing_names:
        return
    raise ShareEditError(
        f"a share named {clashing_names[0]!r} already exists on this "
        f"device, and share names differing only by case could never "
        f"be addressed apart afterwards -- refusing the duplicate.")


def _splice_new_share_node(share_config: ElementTree.Element,
                           share_name: str, volume_name: str) -> str:
    """
    The document to send: the read reserialized, with the new node
    spliced in just before </Shares>.

    Every existing node is exactly as ElementTree emits it -- the form
    the 2026-08-12 hardware verification proved the device accepts --
    and the new node is spliced in as a string, because its captured
    byte shape (_NEW_SHARE_NODE) is outside what ElementTree can
    serialize. Names cannot carry markup (_SHARE_NAME_PATTERN) and
    serialized text escapes every '<', so the one literal </Shares> is
    the container's close tag.
    """
    document = ElementTree.tostring(share_config, encoding="unicode")
    if document.count("</Shares>") != 1:
        raise ShareEditError(
            "the share config does not contain exactly one </Shares> "
            "close tag to insert before -- an empty or missing <Shares> "
            "container is a shape no capture has shown a share appended "
            "to. Refusing rather than guessing where the node goes.")
    new_share_node = _NEW_SHARE_NODE.format(name=share_name,
                                            volume=volume_name)
    return document.replace("</Shares>", new_share_node + "</Shares>")


def _assert_splice_added_only(document_to_send: str,
                              names_before: list[str],
                              share_name: str) -> None:
    """
    Belt and braces: reparse what will be sent and require exactly the
    one new name, in last place, with every existing name untouched.
    """
    names_after = _stripped_name_texts(
        ElementTree.fromstring(document_to_send))
    if names_after == names_before + [share_name]:
        return
    raise ShareEditError(
        "the spliced document does not read back as the existing "
        "shares plus the new one -- refusing. This should be "
        "impossible and means something is wrong.")


def create_share(host: str, esa_id: str, share_name: str,
                 volume: str | None = None,
                 port: int = config_cmd.DEFAULT_PORT, timeout: float = 10.0,
                 verify: bool = True) -> dict:
    """
    Create one share, changing nothing else, and send it.

    Mirrors the 2026-08-25 capture of the original Dashboard creating a
    share: the whole DRIShareConfig is resent with every existing node
    preserved and ONE compact single-line <Share> node appended
    immediately before </Shares> (_NEW_SHARE_NODE is its exact shape).
    The new share starts with an empty <ShareUsers/> -- no access for
    anyone until set_share_user_access grants it.

    `share_name` is validated exactly as given: it must fit
    _SHARE_NAME_PATTERN, and a name with surrounding whitespace is
    refused rather than silently renamed -- every stored-name comparison
    in this module strips, so a name that does not survive a strip could
    never be verified after writing. A name that case-folds onto an
    existing share is refused: this module could never address such a
    pair apart again (see _resolve_share).

    `volume` defaults to the device's own single volume name when the
    read config yields exactly one; with zero or several candidates it
    must be given explicitly (see _derive_volume_name).

    `verify` (default True) re-reads the share config afterward and
    confirms the share is now listed. `timeout` is in seconds.

    THE UNACKNOWLEDGED CASE, MEASURED ON HARDWARE
    ---------------------------------------------
    2026-08-25/26: a create was sent to a real 5N, the device closed the
    socket without a result frame, and the share WAS created. So this
    write in particular must never treat a missing acknowledgement as a
    failure. It passes config_cmd a read-back arbiter; when the device
    goes silent the returned summary carries "acknowledged": False with
    "verified": True, which reads as "the share exists, on the
    read-back's evidence -- the device never said so itself". Callers
    must surface that difference rather than flatten it into plain
    success, and must NEVER retry on it.

    Raises ShareEditError for an invalid or duplicate name or an
    underivable volume; ShareWriteOutcomeUnknownError when the outcome
    cannot be established -- whether the write was ACCEPTED and the
    read-back failed, or the write was never acknowledged and the
    read-back failed too; config_cmd.ConfigWriteError for a refused or
    unreachable device, or for a write the read-back shows did not
    apply.
    """
    share_name = _validated_new_share_name(share_name)
    volume = _validated_volume_text(volume)

    def build_document_with_new_share(
            share_config: ElementTree.Element) -> str:
        names_before = _stripped_name_texts(share_config)
        _refuse_case_clash(names_before, share_name)
        chosen_volume = (volume if volume is not None
                         else _derive_volume_name(share_config))
        document_to_send = _splice_new_share_node(share_config, share_name,
                                                  chosen_volume)
        _assert_splice_added_only(document_to_send, names_before,
                                  share_name)
        return document_to_send

    def share_now_listed() -> bool:
        return share_name in _read_back_share_names(host, esa_id, port,
                                                    timeout)

    # MEASURED 2026-08-25/26: this is the write the device took and then
    # hung up on without answering. `readback` is what stops that being
    # reported as a failure -- see config_cmd._send_section_document.
    # Passed regardless of `verify`.
    try:
        write_summary = config_cmd.set_config_raw(
            host, esa_id, _SHARES_SECTION, build_document_with_new_share,
            port=port, timeout=timeout, readback=share_now_listed)
    except config_cmd.ConfigWriteOutcomeUnknownError as exc:
        raise ShareWriteOutcomeUnknownError(
            f"the write creating share {share_name!r} WAS sent, but "
            "neither the device's acknowledgement nor a read-back could "
            f"settle whether it applied ({exc}). The share MAY now exist. "
            "Re-read the share config before deciding anything -- "
            "and do NOT simply retry the create.") from exc

    # Stop here without a fresh read in two cases: config_cmd already
    # settled this write by reading the device back (its verdict IS the
    # verification -- reading again only invites a second answer), or
    # the caller asked for no verification at all.
    if config_cmd.decided_by_readback(write_summary) or not verify:
        return write_summary

    # Same accepted-versus-verified split as set_share_user_access: a
    # transport failure during the read-back must not read as a failed
    # write.
    return _confirm_by_fresh_read(
        write_summary, share_now_listed,
        accepted_write=(f"the device ACCEPTED the write creating share "
                        f"{share_name!r}"),
        likely_outcome="The share was most likely created.",
        not_applied=f"share {share_name!r} does not read back as present")


def _refuse_existing_delete_confirm(share_config: ElementTree.Element) -> None:
    """
    Refuse a read config that already carries a <ShareDeleteConfirm>:
    no capture explains that shape, and appending a second one would
    send something nobody has seen the device accept.
    """
    if next(share_config.iter("ShareDeleteConfirm"), None) is None:
        return
    raise ShareEditError(
        "the read config already carries a <ShareDeleteConfirm> "
        "element. The 2026-08-12 hardware check showed the read's "
        "top-level children are exactly ConfigVersionBasis, UserList "
        "and Shares, so this is a shape no capture explains -- "
        "refusing rather than appending a second one.")


def _require_shares_last(
        share_config: ElementTree.Element) -> ElementTree.Element:
    """
    The <Shares> container, which must be the LAST child of the read
    config.

    _resolve_share found the target under this container; the confirm
    element is appended to the config, so it lands where the capture
    shows it -- after </Shares>, before </DRIShareConfig> -- only when
    <Shares> is last, and nowhere else.
    """
    shares_container = share_config.find("Shares")
    if list(share_config)[-1] is not shares_container:
        raise ShareEditError(
            "<Shares> is not the last element of the read config, so the "
            "appended <ShareDeleteConfirm> would not land where the "
            "capture shows it. Refusing an unobserved placement.")
    return shares_container


def _remove_share_exactly(share_config: ElementTree.Element,
                          shares_container: ElementTree.Element,
                          target_share: ElementTree.Element,
                          actual_name: str) -> None:
    """
    Remove `target_share` and prove that nothing else moved: the names
    afterwards must be the names before, minus that one.
    """
    names_before = _share_names(share_config)
    shares_container.remove(target_share)
    names_after = _share_names(share_config)
    expected_names = list(names_before)
    expected_names.remove(actual_name)
    if names_after != expected_names:
        raise ShareEditError(
            "removing the share changed more than that one node -- "
            "refusing. This should be impossible with a surgical edit and "
            "means something is wrong.")


def delete_share(host: str, esa_id: str, share_name: str,
                 port: int = config_cmd.DEFAULT_PORT, timeout: float = 10.0,
                 verify: bool = True) -> dict:
    """
    Delete one share, changing nothing else, and send it. DESTRUCTIVE:
    everything stored in the share is expected to be lost. Any
    typed-name confirmation gate belongs to the caller; by the time this
    runs, the decision is made.

    Mirrors the 2026-08-25 capture of the original Dashboard deleting a
    share: the whole DRIShareConfig is resent with the target's <Share>
    node simply absent, plus ONE new element appended after </Shares>
    and before </DRIShareConfig>:
    <ShareDeleteConfirm>Y</ShareDeleteConfirm>.

    The target is resolved exactly as permission edits resolve it -- an
    exact-case match wins, otherwise the name must case-fold to exactly
    one share (_resolve_share). `verify` (default True) re-reads the
    share config afterward and confirms the share is gone. `timeout` is
    in seconds.

    Like create_share, this passes config_cmd a read-back arbiter, so a
    delete the device takes and then hangs up on is reported as
    done-but-unacknowledged ("acknowledged": False, "verified": True)
    rather than as a failure. That distinction matters most here: a
    caller told a destructive write failed may reach for the delete
    again, having already lost the data once.

    Raises ShareEditError for an unknown or ambiguous name;
    ShareWriteOutcomeUnknownError when the outcome cannot be
    established; config_cmd.ConfigWriteError for a refused or
    unreachable device, or for a delete the read-back shows did not
    happen.
    """
    share_name = (share_name or "").strip()
    if not share_name:
        raise ShareEditError("no share name given")

    resolved_target: dict[str, str] = {}

    def remove_target_share(share_config: ElementTree.Element) -> None:
        target_share = _resolve_share(share_config, share_name)
        actual_name = _child_text(target_share, "ShareName")
        resolved_target["name"] = actual_name
        _refuse_existing_delete_confirm(share_config)
        shares_container = _require_shares_last(share_config)
        _remove_share_exactly(share_config, shares_container, target_share,
                              actual_name)
        ElementTree.SubElement(share_config, "ShareDeleteConfirm").text = "Y"

    def share_now_gone() -> bool:
        # Called only after remove_target_share() has run, so
        # resolved_target["name"] is populated: the arbiter fires on
        # the reply to a frame remove_target_share() helped build.
        return resolved_target["name"] not in _read_back_share_names(
            host, esa_id, port, timeout)

    try:
        write_summary = config_cmd.set_config_raw(
            host, esa_id, _SHARES_SECTION, remove_target_share, port=port,
            timeout=timeout, readback=share_now_gone)
    except config_cmd.ConfigWriteOutcomeUnknownError as exc:
        raise ShareWriteOutcomeUnknownError(
            f"the delete of share {share_name!r} WAS sent, but neither the "
            "device's acknowledgement nor a read-back could settle whether "
            f"it applied ({exc}). The share MAY already be gone -- and with "
            "it whatever it held. Re-read the share config before deciding "
            "anything -- and do NOT simply retry the delete.") from exc

    target_name = resolved_target["name"]
    # Stop here without a fresh read in two cases: config_cmd already
    # settled this write by reading the device back (its verdict IS the
    # verification -- reading again only invites a second answer), or
    # the caller asked for no verification at all.
    if config_cmd.decided_by_readback(write_summary) or not verify:
        return write_summary

    return _confirm_by_fresh_read(
        write_summary, share_now_gone,
        accepted_write=(f"the device ACCEPTED the delete of share "
                        f"{target_name!r}"),
        likely_outcome="The share was most likely deleted.",
        not_applied=f"share {target_name!r} still reads back as present")


def _child_text(element: ElementTree.Element, tag: str) -> str:
    """
    The stripped text of `element`'s first `tag` child, or "" when
    there is none. Stripped because the device's own XML may carry
    whitespace that no name comparison should see.
    """
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _read_back_share_names(host: str, esa_id: str, port: int,
                           timeout: float) -> list[str]:
    """Re-read the share config
    and return the share names it now lists.
    """
    from .shares import summarize
    shares_section = config_cmd.get_config(host, esa_id, _SHARES_SECTION,
                                           port=port, timeout=timeout)
    return [s["name"] for s in summarize(shares_section, host)]


def _resolve_share_summary(shares: list[dict],
                           share_name: str) -> dict | None:
    """
    The summarized share `share_name` refers to, or None when it cannot
    be told apart.

    Resolved exactly as the edit resolved it: exact-case first, then a
    UNIQUE case-insensitive fold. If two shares fold to the name and
    neither is an exact match, verification cannot know which one the
    edit touched -- so it must fail rather than confirm whichever comes
    first.
    """
    exact_matches = [s for s in shares if s["name"] == share_name]
    if len(exact_matches) == 1:
        return exact_matches[0]
    case_folded_matches = [s for s in shares
                           if s["name"].lower() == share_name.lower()]
    if len(case_folded_matches) != 1:
        return None
    return case_folded_matches[0]


def _access_reads_back_as(host: str, esa_id: str, share_name: str,
                          username: str, access_code: str, port: int,
                          timeout: float) -> bool:
    """
    Re-read the share config and confirm the user's access is now what
    we set.

    Requires EVERY entry folding to `username` on the share to equal
    access_code, not just the first. A residual duplicate row that still
    grants the old access must fail verification -- reporting success
    while wrong access survives is exactly the failure this read-back
    exists to catch.
    """
    from .shares import summarize
    shares_section = config_cmd.get_config(host, esa_id, _SHARES_SECTION,
                                           port=port, timeout=timeout)
    shares = summarize(shares_section, host)

    share = _resolve_share_summary(shares, share_name)
    if share is None:
        return False

    matching_users = [u for u in share["users"]
                      if u["name"].lower() == username.lower()]
    if not matching_users:
        return False
    return all(u["access_code"] == access_code for u in matching_users)
