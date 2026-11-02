"""
eCmdGetDroboAppsStatus -- what's installed on the Drobo itself.

DroboApps are packages that run ON the NAS: Python, rsync, Plex, an SSH
server. This is how people extended these boxes, and it is the one place
the device tells you about software rather than storage.

Measured on the live 5N (firmware 4.3.1): answers on a plain login, no
crypto handshake, returning a real list with names, versions,
descriptions, whether each is stopped, and whether it has a web
interface.

Worth knowing: **DroboPix is a DroboApp, so where it is installed it
answers here.** That is the photo-upload service Phase 4 of this project
sets out to replace. Whether it still functions with its vendor servers
gone is a separate question -- but the app, its version and its web
interface are all discoverable from here, which is a better starting
point for Phase 4 than guessing.

Like eCmdGetSysInfo, the reply nests its payload as an entity-escaped
XML document inside <ResultDetails>, so it is parsed with the same
unwrapping helper rather than by looking for child elements that are not
there.
"""
from __future__ import annotations

import socket
from xml.etree import ElementTree

from . import commands as _commands
from .config_cmd import (
    DEFAULT_PORT,
    TYPE_COMMAND,
    TYPE_LOGIN,
    TYPE_LOGIN_OK,
    TYPE_RESULT,
    _build_frame,
    _login_payload,
    _read_frame,
    # A pure predicate over a write summary -- no socket, nothing to
    # gate -- so unlike set_config it is imported at module level with
    # the rest.
    decided_by_readback,
)
from .sysinfo import SysInfoError, _inner_document

APPS_STATUS_COMMAND_NAME = "eCmdGetDroboAppsStatus"


def _child_text(element, tag: str) -> str:
    """
    The stripped text of `element`'s first `tag` child, or "" when
    there is none, so a missing field and an empty one read the same.
    """
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _parse_reply_root(payload: bytes):
    """
    The reply's root element, or None for an empty reply.

    Every reader here starts the same way, and an empty reply is not an
    error (each parser decides what "nothing said" means), whereas XML
    that does not parse is: it raises SysInfoError.
    """
    text = payload.rstrip(b"\x00").rstrip()
    if not text:
        return None
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise SysInfoError(f"malformed result XML: {exc}") from exc


def _unwrap_to(details, tag: str):
    """
    The `tag` element a reply carries, whether it arrived as an
    entity-escaped document inside <ResultDetails> or as plain child
    elements. It looks at the element itself and one level below it,
    no deeper. Falls back to that element when no `tag` is there, so
    the caller's field lookups simply come back empty.
    """
    inner_document = _inner_document(details)
    element = inner_document if inner_document is not None else details
    if element.tag == tag:
        return element
    # `is not None`, never `or`: an Element with no children is falsy,
    # so `or` would quietly fall back to the parent.
    tagged_node = element.find(tag)
    return tagged_node if tagged_node is not None else element


def _app_record(app_element, name: str) -> dict:
    """One installed app, as the record every caller reads."""
    # "Stopped" is the device's own polarity, and it is inverted
    # relative to how anyone would say it out loud. Flip it here so
    # every caller above this line deals in "running", which is what
    # a person reads.
    stopped = _child_text(app_element, "Stopped")
    return {
        "name": name,
        "version": _child_text(app_element, "Version"),
        "description": _child_text(app_element, "Description"),
        "running": stopped == "0",
        "status": _child_text(app_element, "ActiveStatus"),
        "has_web_ui": bool(_child_text(app_element, "WebUI")),
    }


def parse_droboapps(payload: bytes) -> dict:
    """
    Turn a DroboApps reply into {'sdk_version', 'apps': [...]}.

    Separate from the socket work so it is testable with no device
    present. An app with no name is skipped -- a nameless entry is not
    something a person can act on, and showing a blank row is worse than
    showing nothing.
    """
    root = _parse_reply_root(payload)
    if root is None:
        return {"sdk_version": "", "apps": []}

    details = root.find("ResultDetails")
    if details is None:
        raise SysInfoError("result carried no <ResultDetails>")
    apps_element = _unwrap_to(details, "DroboApps")

    apps = []
    for app_element in apps_element.findall("App"):
        name = _child_text(app_element, "Name")
        if not name:
            continue
        apps.append(_app_record(app_element, name))

    apps.sort(key=lambda a: a["name"].lower())
    return {"sdk_version": _child_text(apps_element, "SDKVersion"),
            "apps": apps}


def parse_dimming(payload: bytes) -> int | None:
    """
    LED brightness, from eCmdGetDimming.

    Unlike every other command here, this one does NOT nest a document:
    it puts a bare number straight into <ResultDetails>. The live 5N
    returned `59`. What the scale is -- percent, or 0-255, or Drobo's
    own steps -- is not documented anywhere in the firmware table, so
    this returns the raw integer and lets the caller present it without
    inventing a unit.

    Returns None rather than 0 when there is no reading. Zero is a
    legitimate brightness ("lights off"), so conflating the two would be
    a real bug.
    """
    root = _parse_reply_root(payload)
    if root is None:
        return None
    details = root.find("ResultDetails")
    if details is None:
        return None
    dimming_text = (details.text or "").strip()
    if not dimming_text:
        return None
    try:
        brightness = int(dimming_text)
    except ValueError:
        return None
    return brightness if brightness >= 0 else None


def parse_demo_mode(payload: bytes) -> dict:
    """
    eCmdGetDemoModeInfo -- whether the unit is in shop-display mode.

    Answered on the live 5N with `FirmwareInfo > DemoMode, ScaleFactor`.
    Demo mode is what a retailer switched on so a Drobo on a shelf would
    show plausible fake capacity instead of five empty bays; ScaleFactor
    is the multiplier it invented capacity with.

    Genuinely obscure, and surfaced anyway for one reason: **a unit left
    in demo mode reports capacity that is not real.** Every number this
    project displays would be a lie and nothing else would say so. On a
    normal unit this reads disabled and nobody ever thinks about it
    again -- but it costs ten lines to rule out, and the alternative is
    a dashboard that cannot tell a fabricated array from a real one.

    Returns {"demo_mode": bool|None, "scale_factor": int|None}. None
    means the device did not say, which is NOT the same as "off" -- see
    the caller, which only warns on an explicit yes.
    """
    demo_mode_info: dict = {"demo_mode": None, "scale_factor": None}
    root = _parse_reply_root(payload)
    if root is None:
        return demo_mode_info

    details = root.find("ResultDetails")
    if details is None:
        return demo_mode_info
    firmware_info_element = _unwrap_to(details, "FirmwareInfo")

    demo_mode_text = _child_text(firmware_info_element, "DemoMode")
    # The device sends 0/1. Anything else is left as unknown rather than
    # coerced -- a truthy string would otherwise read as "demo mode on"
    # and put a false warning on a perfectly ordinary array.
    if demo_mode_text in ("0", "1"):
        demo_mode_info["demo_mode"] = demo_mode_text == "1"
    scale_factor_text = _child_text(firmware_info_element, "ScaleFactor")
    if scale_factor_text:
        try:
            demo_mode_info["scale_factor"] = int(scale_factor_text)
        except ValueError:
            pass
    return demo_mode_info


def _query_reply_body(host: str, esa_id: str, command_name: str,
                      port: int = DEFAULT_PORT,
                      timeout: float = 8.0) -> bytes:
    """
    Send one query and hand back its raw reply body.

    Shared by the small reads that need no bespoke socket dance of their
    own (dimming, demo mode, the installed-app list). The guard and the
    login are identical either way, so duplicating them per command
    would just be more places for the do-not-send check to be
    forgotten.
    """
    if _commands.is_dangerous(command_name):
        raise SysInfoError(f"{command_name} is on the do-not-send list")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise SysInfoError(f"{command_name} has no confirmed command id")

    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><Params></Params>"
        f"<ESAID>{esa_id}</ESAID></TMCmd>").encode() + b"\x00"

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        frame_type, _ = _read_frame(sock)
        if frame_type != TYPE_LOGIN_OK:
            raise SysInfoError(f"login refused (type 0x{frame_type:02x})")
        sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        frame_type, body = _read_frame(sock)
        if frame_type != TYPE_RESULT:
            raise SysInfoError(f"unexpected reply type 0x{frame_type:02x}")
        return body
    finally:
        sock.close()


def get_dimming(host: str, esa_id: str, port: int = DEFAULT_PORT,
                timeout: float = 8.0) -> int | None:
    """
    LED brightness as the device reports it. Read-only; see
    parse_dimming. It is also set_dimming's read-back, which is why it
    returns the raw number rather than anything interpreted.
    """
    return parse_dimming(
        _query_reply_body(host, esa_id, "eCmdGetDimming", port, timeout))


# The fields the <General> block is known to carry, in the order the
# device sends them. CONFIRMED from the 2026-08-06 capture (two writes:
# FeatureStates/SpindownDelay/Dim = 5/0/59 and 7/14/59). The ORDER is
# part of the contract -- the serializer below emits exactly these,
# exactly so.
_GENERAL_FIELDS = ("FeatureStates", "SpindownDelay", "Dim")

# A sanity band, NOT a device limit -- the firmware documents no scale
# for Dim (see parse_dimming). The capture showed 1 and 59; get_dimming
# returns 59. If the true scale is 0-255 this still admits every real
# value, and it turns a fat-fingered 5900 into a refusal instead of a
# write. 0 is allowed: "lights off" is a legitimate brightness.
_DIMMING_MIN, _DIMMING_MAX = 0, 255

# SpindownDelay's band. Even looser and even less certain: the capture
# showed 0 (disabled) and 14, and the units are unconfirmed -- minutes
# and a dropdown index are both consistent. 0..1440 (a day, if minutes)
# is a generous typo guard that constrains nothing real. 0 is "never
# spin down".
_SPINDOWN_MIN, _SPINDOWN_MAX = 0, 1440


class GeneralWriteError(SysInfoError):
    """A write to the <General> config block could not be done safely,
    or the
    device refused it. Base for the specific setters below so a caller
    can catch the whole family or a single field's own errors."""


class DimmingWriteError(GeneralWriteError):
    """
    LED brightness could not be set safely, or the device refused it.
    Its own class so a caller can tell which setting failed.
    """


class SpindownWriteError(GeneralWriteError):
    """
    The spin-down delay could not be set safely, or the device refused
    it. Its own class so a caller can tell which setting failed.
    """


def _extract_general_block(before: dict) -> dict:
    """
    Pull the <General> field dict out of whatever wrapper depth it
    arrived in.

    The real 5N wraps it: {'DRINASConfig': {'General': {...}}} (measured
    on firmware 4.3.1, 2026-08-10). It may also arrive as {'General':
    {...}} or as the fields directly. All three are accepted; anything
    else is refused, because writing a block built from a shape we did
    not recognise is exactly the guessing this path exists to avoid.

    An earlier version only looked for a TOP-LEVEL 'General' key and so
    did not see the DRINASConfig wrapper the device actually sends --
    offline fixtures used the flat shape and hid it until the first real
    read. That is why the read-before-write happens against hardware
    before anything is trusted.
    """
    general_block = before
    if (isinstance(general_block, dict) and "DRINASConfig" in general_block
            and isinstance(general_block["DRINASConfig"], dict)):
        general_block = general_block["DRINASConfig"]
    if (isinstance(general_block, dict) and "General" in general_block
            and isinstance(general_block["General"], dict)):
        general_block = general_block["General"]
    if (isinstance(general_block, dict)
            and set(general_block) & set(_GENERAL_FIELDS)):
        return general_block
    received_shape = (sorted(general_block)
                      if isinstance(general_block, dict)
                      else type(general_block).__name__)
    raise GeneralWriteError(
        "the device's General config did not parse into the expected "
        "fields; refusing to write a block built from an unrecognised "
        f"shape (got keys {received_shape})")


def _make_general_block_builder(field_name=None, new_value=None, *,
                                changes: dict | None = None):
    """
    Build a set_config build_section_document that changes ONLY the
    named field(s) in the <General> block and echoes every other sibling
    back exactly as read.

    Call as _make_general_block_builder("Dim", 5) for one field, or as
    _make_general_block_builder(
        changes={"FeatureStates": 5, "SpindownDelay": 0})
    for several that must move together. Two coupled fields exist for a
    reason: disabling spin-down is BOTH clearing the FeatureStates bit
    AND zeroing the delay -- the device coerces a lone delay of 0 back
    to a default while the bit still says "enabled" (measured on
    hardware 2026-08-12).

    Shared by set_dimming and set_spindown because the safety is
    identical and must not drift: it refuses if the block carries a
    field beyond the three confirmed (would drop it), if it is missing
    one (would write a block unlike what was read), or if any ECHOED
    sibling is not a scalar string (would emit a Python repr). The field
    being SET is exempt from the scalar check -- its old shape does not
    matter, it is overwritten.
    """
    new_values_by_field = dict(changes) if changes else {field_name: new_value}
    unsupported_fields = set(new_values_by_field) - set(_GENERAL_FIELDS)
    if unsupported_fields:
        raise GeneralWriteError(
            f"cannot set unknown General field(s) "
            f"{sorted(unsupported_fields)}")

    def build_section_document(before: dict) -> str:
        general_block = _extract_general_block(before)
        _refuse_unknown_or_missing_fields(general_block)
        _refuse_non_scalar_siblings(general_block, new_values_by_field)
        return _serialize_general_block(general_block, new_values_by_field)

    return build_section_document


def _refuse_unknown_or_missing_fields(general_block: dict) -> None:
    """
    Refuse a block whose fields are not exactly the three confirmed: an
    extra field would be dropped by the write, and a missing one would
    write a block unlike what was read.
    """
    unrecognised_fields = set(general_block) - set(_GENERAL_FIELDS)
    if unrecognised_fields:
        raise GeneralWriteError(
            f"the General block has fields this build does not know how "
            f"to preserve ({sorted(unrecognised_fields)}); refusing to "
            f"write rather than risk dropping them")
    missing_fields = set(_GENERAL_FIELDS) - set(general_block)
    if missing_fields:
        raise GeneralWriteError(
            f"the General block is missing expected fields "
            f"({sorted(missing_fields)}); "
            f"refusing to write a block that does not match what was read")


def _refuse_non_scalar_siblings(general_block: dict,
                                new_values_by_field: dict) -> None:
    """
    Refuse an ECHOED field that is not a scalar string: it would be
    written as a Python repr. The fields being SET are exempt, because
    they are overwritten and their prior shape is irrelevant.
    """
    for field_tag in _GENERAL_FIELDS:
        if field_tag in new_values_by_field:
            continue  # overwritten below; its prior shape is irrelevant
        if not isinstance(general_block[field_tag], str):
            raise GeneralWriteError(
                f"General field <{field_tag}> came back as "
                f"{type(general_block[field_tag]).__name__}, "
                f"not a scalar; refusing to serialize a value we cannot "
                f"round-trip faithfully")


def _serialize_general_block(general_block: dict,
                             new_values_by_field: dict) -> str:
    """
    The <General> document to send: every field in _GENERAL_FIELDS
    order, each echoed as read unless it is one being set.
    """
    merged_fields = dict(general_block)
    for field_tag, value in new_values_by_field.items():
        merged_fields[field_tag] = str(value)
    field_elements = "".join(
        f"<{field_tag}>{merged_fields[field_tag]}</{field_tag}>"
        for field_tag in _GENERAL_FIELDS)
    return f"<General>{field_elements}</General>"


def _validate_brightness(brightness: int) -> None:
    """
    Refuse anything but a whole number inside the sanity band, before
    anything is read or sent. A bool is refused although it is an int,
    because True reaching the device as brightness 1 is a mistake, not
    an intention.
    """
    if isinstance(brightness, bool) or not isinstance(brightness, int):
        raise DimmingWriteError(
            f"brightness must be a whole number, "
            f"not {type(brightness).__name__}")
    if not (_DIMMING_MIN <= brightness <= _DIMMING_MAX):
        raise DimmingWriteError(
            f"brightness {brightness} is outside the "
            f"{_DIMMING_MIN}-{_DIMMING_MAX} sanity "
            f"band; the scale is unconfirmed, so a value beyond it is far "
            f"more likely a mistake than an intention")


def _confirming_read(write_summary: dict, setting_description: str,
                     read_setting):
    """
    The value `read_setting()` returns: the read that confirms a write
    the device ACCEPTED. ConfigWriteOutcomeUnknownError when that read
    cannot be completed.

    By the time this runs the device has answered RESULT_ACCEPTED, so
    the write most likely applied, and a read that then fails (a
    refused connection, a garbled reply, a block that does not parse)
    says nothing about it. Until 2026-11-02 the read's own error
    escaped, and the agent answered two of them wrongly: an OSError got
    a 502 "could not reach the command port", as though nothing had
    been sent, and a GeneralWriteError (no General block in the reply,
    or a FeatureStates that is not a number) got a 400 "refusing",
    which blames the request. Both invite a retry of a write that has
    probably landed. (A reply too garbled to read already came out as
    a plain ConfigError, which the agent answers "unsettled".)
    sharewrite reports the same case as outcome-unknown; this is that
    answer, in the type the agent already files as unsettled.
    "verified" is set to None, as sharewrite sets it, for a caller that
    holds on to the summary.
    """
    from .config_cmd import ConfigError, ConfigWriteOutcomeUnknownError
    try:
        return read_setting()
    except (OSError, ConfigError, SysInfoError) as exc:
        write_summary["verified"] = None
        raise ConfigWriteOutcomeUnknownError(
            f"the device ACCEPTED the {setting_description} write, but the "
            f"read-back to confirm it could not be completed "
            f"({type(exc).__name__}: {exc}). It most likely applied. "
            "Re-read before deciding anything -- do NOT simply "
            "retry.") from exc


def set_dimming(host: str, esa_id: str, brightness: int,
                port: int = DEFAULT_PORT, timeout: float = 8.0,
                verify: bool = True) -> dict:
    """
    Set the front-panel LED brightness. The first configuration WRITE
    this project sends -- and it is a genuine read-modify-write, not a
    blind poke.

    WHY THIS IS A SAFE WRITE
    ------------------------
    It qualifies under the same four tests that admitted Identify
    (commands.SAFE_WRITES): it touches no file and no disk pack, affects
    no availability, and it is REVERSIBLE -- read the current value
    first, and you can set it straight back. That read-back is not
    optional dressing; it is what turns "persist a setting" (which kept
    eCmdSetDimming out of the safe set) into something undoable.

    HOW THE SIBLINGS ARE PROTECTED
    ------------------------------
    The <General> block carries FeatureStates and SpindownDelay
    alongside Dim, and the device is written the WHOLE block every time
    (capture, 2026-08-06). So this reads the live block via
    config_cmd.set_config, changes ONLY <Dim>, and echoes the other two
    back exactly as read. If the block contains any field beyond the
    three we have confirmed -- a setting a newer firmware might add --
    this REFUSES rather than dropping it. A write that silently
    discarded an unknown setting would be exactly the kind of quiet
    damage the whole read-modify-write dance exists to prevent.

    Note the device is NOT sent eCmdSetDimming (which the original
    Dashboard never uses); brightness rides in the General block via
    eCmdSetConfig, which is why this lives on set_config's vetted write
    path.

    `verify` (default True) re-reads the brightness after an accepted
    write and confirms it actually changed. RESULT_ACCEPTED means the
    device RECEIVED the command, not that it applied it -- Identify
    returns the same code and blinks nothing -- so without this
    read-back "success" is only "not rejected". The read-back costs one
    more connection; pass verify=False only where that cost matters more
    than the confirmation.

    Returns config_cmd.set_config's summary, plus "verified" and the
    "read_back" value when verify is on. The summary's "acknowledged" is
    False when the device never answered and this brightness read-back
    is what established the change -- true, but on the device's state
    rather than its word. Raises DimmingWriteError on an out-of-band
    value, an unrecognised block, or a write the device accepted but did
    not apply; ConfigWriteError propagates for a refused or unreachable
    device, and ConfigWriteOutcomeUnknownError when neither the device
    nor a read-back could settle what happened, or when the device
    accepted the write and the confirming read then failed (see
    _confirming_read).
    """
    # Import here, not at module load: keeps the read-only surface of
    # this module importable in contexts that never write, and mirrors
    # how the project gates its one other write path (identify.py).
    from .config_cmd import set_config

    _validate_brightness(brightness)

    # The read-back doubles as config_cmd's arbiter for a write the
    # device never acknowledges: sent, socket closed, no result frame.
    # Reporting that as a failure would be a lie (measured on a share
    # create, 2026-08-25/26), so the arbiter is passed whatever `verify`
    # says -- see config_cmd._send_section_document.
    def brightness_reads_back() -> bool:
        return get_dimming(host, esa_id, port=port,
                           timeout=timeout) == brightness

    write_summary = set_config(
        host, esa_id, "general",
        _make_general_block_builder("Dim", brightness),
        port=port, timeout=timeout, readback=brightness_reads_back)

    # Stop here without a fresh read in two cases: config_cmd already
    # settled this write by reading the device back (its verdict IS the
    # verification -- reading again only invites a second answer), or
    # the caller asked for no verification at all.
    if decided_by_readback(write_summary) or not verify:
        return write_summary

    # Confirm the write actually took, not merely that it was accepted.
    brightness_read_back = _confirming_read(
        write_summary, "brightness",
        lambda: get_dimming(host, esa_id, port=port, timeout=timeout))
    write_summary["read_back"] = brightness_read_back
    write_summary["verified"] = brightness_read_back == brightness
    if brightness_read_back != brightness:
        raise DimmingWriteError(
            f"the device accepted the write but brightness reads back as "
            f"{brightness_read_back!r}, not {brightness}. The command was "
            f"received and not applied -- nothing here can be relied on as "
            f"changed.")
    return write_summary


def get_spindown(host: str, esa_id: str, port: int = DEFAULT_PORT,
                 timeout: float = 8.0) -> int | None:
    """
    The spin-down delay from the <General> block; None when the block
    has no SpindownDelay or it is not a number.

    Read-only. Reads the same block set_spindown writes, which is also
    how set_spindown verifies its own write -- one confirmed reader, no
    second command to depend on. A read that fails is not None: it
    raises config_cmd.ConfigError or OSError as get_config does, and a
    reply with no General block raises GeneralWriteError.
    """
    from .config_cmd import get_config
    general_block = _extract_general_block(
        get_config(host, esa_id, "general", port=port, timeout=timeout))
    delay_text = general_block.get("SpindownDelay")
    if not isinstance(delay_text, str) or not delay_text.strip():
        return None
    try:
        return int(delay_text)
    except ValueError:
        return None


# The FeatureStates bit that means "spin-down enabled". MEASURED on
# hardware 2026-08-12 (and consistent with the 2026-08-06 capture's
# 5<->7 toggle): with this bit SET, the device REFUSES a SpindownDelay
# of 0 and silently coerces it to a default (observed: sent 0, stored
# 15). So "off" is not "delay 0" -- it is clearing this bit AND zeroing
# the delay together, exactly as the original Dashboard sends it. Bits 0
# and 2 stay set throughout and are unidentified.
_SPINDOWN_ENABLED_BIT = 0b010


def _parse_feature_states(general_block: dict) -> int:
    """
    FeatureStates as a number, or raise SpindownWriteError: the
    spin-down writes compute a bitmask from it, and a bitmask computed
    from text that is not a number would be a guess sent to the device.
    """
    feature_states_text = general_block.get("FeatureStates")
    if (isinstance(feature_states_text, str)
            and feature_states_text.strip().isdigit()):
        # isdigit() is only the first filter: it also passes "²", and
        # more than 4300 digits, both of which int() refuses with a
        # ValueError that no caller expects. Those are refused below
        # like any other text that is not a number.
        try:
            return int(feature_states_text)
        except ValueError:
            pass
    raise SpindownWriteError(
        f"FeatureStates read back as {feature_states_text!r}, not a "
        f"number; refusing to compute a bitmask from something we "
        f"cannot parse")


def _validate_spindown_delay(spindown_delay: int) -> None:
    """
    Refuse anything but a whole number in 1.._SPINDOWN_MAX, before
    anything is read or sent. 0 gets its own message, because it is the
    one value somebody would send meaning "off", and the device does not
    read it that way (see set_spindown).
    """
    if (isinstance(spindown_delay, bool)
            or not isinstance(spindown_delay, int)):
        raise SpindownWriteError(
            f"spin-down delay must be a whole number, "
            f"not {type(spindown_delay).__name__}")
    if spindown_delay == 0:
        raise SpindownWriteError(
            "a spin-down delay of 0 does not disable spin-down -- the device "
            "coerces it (measured: sent 0, stored 15). To turn spin-down off "
            "use disable_spindown(), which clears the enable bit as well.")
    if not (_SPINDOWN_MIN < spindown_delay <= _SPINDOWN_MAX):
        raise SpindownWriteError(
            f"spin-down delay {spindown_delay} is outside the "
            f"1-{_SPINDOWN_MAX} sanity band; the units are unconfirmed, so a "
            f"value beyond it is far more likely a mistake than an intention")


def _spindown_changes_for(before: dict, spindown_delay: int) -> dict:
    """
    The General fields a nonzero delay changes: the delay itself, plus
    the enable bit when it is clear, because a delay with the feature
    disabled is a value nobody can observe.
    """
    general_block = _extract_general_block(before)
    feature_bits = _parse_feature_states(general_block)
    changes = {"SpindownDelay": spindown_delay}
    if not (feature_bits & _SPINDOWN_ENABLED_BIT):
        changes["FeatureStates"] = feature_bits | _SPINDOWN_ENABLED_BIT
    return changes


def set_spindown(host: str, esa_id: str, delay: int,
                 port: int = DEFAULT_PORT, timeout: float = 8.0,
                 verify: bool = True) -> dict:
    """
    Set the drive spin-down delay to a NONZERO value. Rides the vetted
    General-block path -- read the live block, change only
    <SpindownDelay>, echo the siblings, refuse anything it cannot
    preserve -- and enables spin-down (sets the FeatureStates bit) if it
    was off, because a delay with the feature disabled is a value nobody
    can observe.

    A delay of 0 is REFUSED here; use disable_spindown(). Measured on
    hardware 2026-08-12: with spin-down enabled, the device accepts a
    delay of 0, returns the success code, and stores 15 -- it coerces
    the value rather than disabling. Read-back verification caught it.
    Zero only means "off" when the enable bit is cleared in the same
    write, which is a different operation with its own name so nobody
    can trip over this again.

    This sets a SETTING; it does not spin the drives down now (there is
    no such command -- capture, 2026-08-06). Reversible: read the
    current value first and set it back. `delay` carries no
    unit in its name because its unit is unconfirmed -- minutes and a
    dropdown index both fit (see _SPINDOWN_MAX).

    `verify` re-reads and confirms;
    RESULT_ACCEPTED means received, not applied. An accepted write
    whose confirming read fails is ConfigWriteOutcomeUnknownError (see
    _confirming_read).
    """
    from .config_cmd import set_config

    _validate_spindown_delay(delay)

    # The changes depend on the live block (whether the bit is already
    # set), so build them inside build_section_document rather than
    # ahead of the read.
    def build_section_document(before: dict) -> str:
        return _make_general_block_builder(
            changes=_spindown_changes_for(before, delay))(before)

    def delay_reads_back() -> bool:
        return get_spindown(host, esa_id, port=port,
                            timeout=timeout) == delay

    write_summary = set_config(host, esa_id, "general",
                               build_section_document, port=port,
                               timeout=timeout, readback=delay_reads_back)
    # Stop here without a fresh read in two cases: config_cmd already
    # settled this write by reading the device back (its verdict IS the
    # verification -- reading again only invites a second answer), or
    # the caller asked for no verification at all.
    if decided_by_readback(write_summary) or not verify:
        return write_summary

    delay_read_back = _confirming_read(
        write_summary, "spin-down",
        lambda: get_spindown(host, esa_id, port=port, timeout=timeout))
    write_summary["read_back"] = delay_read_back
    write_summary["verified"] = delay_read_back == delay
    if delay_read_back != delay:
        raise SpindownWriteError(
            f"the device accepted the write but the spin-down delay reads "
            f"back as {delay_read_back!r}, not {delay}. Received, "
            f"not applied.")
    return write_summary


def _spindown_is_off(feature_bits: int, stored_delay) -> bool:
    """
    Whether a read-back shows spin-down off: the enable bit clear AND
    the delay "0". Either alone is not off (see disable_spindown).
    """
    return (not (feature_bits & _SPINDOWN_ENABLED_BIT)
            and stored_delay == "0")


def disable_spindown(host: str, esa_id: str, port: int = DEFAULT_PORT,
                     timeout: float = 8.0, verify: bool = True) -> dict:
    """
    Turn drive spin-down OFF: clear the FeatureStates enable bit AND
    zero the delay in ONE write -- the exact pair the original Dashboard
    sends (capture 2026-08-06: FeatureStates 7->5 with SpindownDelay
    14->0). Either half alone is wrong: a bare delay of 0 is coerced by
    the device (measured 2026-08-12), and clearing only the bit leaves a
    stale delay that re-enable would revive.

    Verified by read-back of BOTH fields; an accepted write whose
    confirming read fails is ConfigWriteOutcomeUnknownError (see
    _confirming_read). Reversible with set_spindown(delay).
    """
    from .config_cmd import set_config

    def build_section_document(before: dict) -> str:
        feature_bits = _parse_feature_states(_extract_general_block(before))
        return _make_general_block_builder(changes={
            "FeatureStates": feature_bits & ~_SPINDOWN_ENABLED_BIT,
            "SpindownDelay": 0,
        })(before)

    from .config_cmd import get_config

    def read_spindown_state() -> tuple[int, object]:
        """FeatureStates as a number, and the stored delay as read."""
        general_block = _extract_general_block(get_config(
            host, esa_id, "general", port=port, timeout=timeout))
        return (_parse_feature_states(general_block),
                general_block.get("SpindownDelay"))

    def spindown_reads_back_off() -> bool:
        return _spindown_is_off(*read_spindown_state())

    write_summary = set_config(host, esa_id, "general",
                               build_section_document, port=port,
                               timeout=timeout,
                               readback=spindown_reads_back_off)
    # Stop here without a fresh read in two cases: config_cmd already
    # settled this write by reading the device back (its verdict IS the
    # verification -- reading again only invites a second answer), or
    # the caller asked for no verification at all.
    if decided_by_readback(write_summary) or not verify:
        return write_summary

    feature_bits, stored_delay = _confirming_read(
        write_summary, "spin-down disable", read_spindown_state)
    spindown_is_off = _spindown_is_off(feature_bits, stored_delay)
    write_summary["read_back"] = {"FeatureStates": feature_bits,
                                  "SpindownDelay": stored_delay}
    write_summary["verified"] = spindown_is_off
    if not spindown_is_off:
        raise SpindownWriteError(
            f"the device accepted the disable but reads back "
            f"FeatureStates={feature_bits} "
            f"SpindownDelay={stored_delay!r}; expected the enable bit clear "
            f"and delay 0. Received, not applied.")
    return write_summary


def get_demo_mode(host: str, esa_id: str, port: int = DEFAULT_PORT,
                  timeout: float = 8.0) -> dict:
    """
    Whether this unit is in shop-display mode. See parse_demo_mode for
    why it is asked at all: a unit in demo mode reports capacity that
    is not real.
    """
    return parse_demo_mode(
        _query_reply_body(host, esa_id, "eCmdGetDemoModeInfo", port, timeout))


def get_droboapps(host: str, esa_id: str, port: int = DEFAULT_PORT,
                  timeout: float = 8.0) -> dict:
    """
    Ask the Drobo what is installed on it. Read-only.

    It goes through _query_reply_body like the other small reads, so the
    do-not-send guard and the login live in one place. It is also
    droboapps_action's check that an app is installed before acting.
    """
    return parse_droboapps(
        _query_reply_body(host, esa_id, APPS_STATUS_COMMAND_NAME, port,
                          timeout))


# The two action verbs the capture confirmed, exactly as the device
# spells them.
_APP_ACTIONS = ("Start", "Stop")


class DroboAppsActionError(SysInfoError):
    """Starting or stopping a DroboApp could not be done safely, or the
    device refused it. Its own class so the agent can tell a refused
    action from a failed read."""


def droboapps_action(host: str, esa_id: str, name: str, action: str,
                     port: int = DEFAULT_PORT, timeout: float = 8.0,
                     check_installed: bool = True) -> dict:
    """
    Start or stop an installed DroboApp. `action` is "Start" or "Stop".

    CONFIRMED format (2026-08-06 capture, both directions observed):

        <Params><Name>{app name}</Name><Action>Stop</Action></Params>

    Unlike the config writes this is a direct command, not
    eCmdSetConfig, so it has no read-modify-write to preserve -- but it
    is still a gated write (commands.VETTED_WRITES) sent by this one
    function, which validates before it acts:

      * `action` must be one of the two verbs the device actually uses.
        A typo'd "start" (lowercase) or "Restart" is refused, not sent
        hopefully.
      * `name` must be an app the device REPORTS as installed (unless
        check_installed is turned off). The app list comes from
        eCmdGetDroboAppsStatus, and the capture confirmed that exact
        string is what the action takes -- so this refuses to send an
        action for an app that is not there rather than let the device
        decide what to do with a name it does not recognise.

    Reversible: a stop is undone by a start. It touches an installed
    service, never the disk pack.

    NO read-back verification here, deliberately: a service does not
    start or stop instantly, so an immediate re-read of the status would
    race the state change and report a false failure. The caller should
    refresh the app list a moment later instead. This returns once the
    device has ACCEPTED the action (Result == RESULT_ACCEPTED); accepted
    is not the same as finished.

    Raises DroboAppsActionError for a bad verb, an app the device does
    not report, a command port that cannot be reached, a refused login,
    a reply that is not a result, or a result other than accepted.
    Before the action is sent, the installed-app check can also let out
    config_cmd.ConfigError (SysInfoError among them; a plain ConfigError
    for a short or garbled frame, or for a serial the login refuses to
    build) or OSError from its own read, and then the action was not
    sent. After the action's own connect, OSError and
    config_cmd.ConfigError propagate unwrapped (see _send_action), and
    by then the action frame may be on the wire: the action may have
    been taken, so neither is a reason to send it again blindly. (A
    serial the login refuses is a ConfigError before any frame goes
    out, on either path.)
    """
    app_name = _validated_app_action(action, name)
    command_id = _vetted_action_command_id()
    if check_installed:
        _refuse_uninstalled(host, esa_id, app_name, port, timeout)
    request_payload = _action_request_payload(command_id, esa_id, app_name,
                                              action)
    return _send_action(host, esa_id, request_payload, port, timeout,
                        action, app_name)


def _validated_app_action(action: str, name: str) -> str:
    """
    The stripped app name, or raise for a verb the device does not use
    or an empty name. Checked before anything is read or sent, so a
    typo'd "start" never reaches the device.
    """
    if action not in _APP_ACTIONS:
        raise DroboAppsActionError(
            f"action must be one of {_APP_ACTIONS!r}, not {action!r} -- "
            f"the device's verbs are case-sensitive "
            f"and only these two are confirmed")
    app_name = (name or "").strip()
    if not app_name:
        raise DroboAppsActionError("no app name given")
    return app_name


def _vetted_action_command_id() -> int:
    """
    eCmdDroboAppsAction's command id, read through the write gate: the
    command must be in commands.VETTED_WRITES and have a confirmed id,
    or nothing is sent.
    """
    command_name = "eCmdDroboAppsAction"
    if command_name not in _commands.VETTED_WRITES:
        raise DroboAppsActionError(
            f"{command_name} is not a vetted write (commands.VETTED_WRITES)")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise DroboAppsActionError(
            f"{command_name} has no confirmed command id")
    return command_id


def _refuse_uninstalled(host: str, esa_id: str, app_name: str, port: int,
                        timeout: float) -> None:
    """
    Refuse an app the device does not REPORT as installed, rather than
    let the device decide what to do with a name it does not recognise.
    """
    installed_names = {a["name"] for a in get_droboapps(
        host, esa_id, port, timeout).get("apps", [])}
    if app_name in installed_names:
        return
    raise DroboAppsActionError(
        f"{app_name!r} is not an installed DroboApp on this device "
        f"(installed: {sorted(installed_names)}); refusing to act on "
        f"a name the device did not report")


def _action_request_payload(command_id: int, esa_id: str, app_name: str,
                            action: str) -> bytes:
    """
    The captured request, <CmdID><ESAID><Params> in that order, with the
    trailing NUL every request carries.
    """
    # The Name value is escaped in case an app name ever carries a
    # special character; Action is one of two literals. quote=False
    # matches the only escaping the wire format was observed to use.
    import html
    escaped_name = html.escape(app_name, quote=False)
    params_xml = f"<Name>{escaped_name}</Name><Action>{action}</Action>"
    return (
        f"<TMCmd><CmdID>{command_id}</CmdID><ESAID>{esa_id}</ESAID>"
        f"<Params>{params_xml}</Params></TMCmd>").encode() + b"\x00"


def _send_action(host: str, esa_id: str, request_payload: bytes,
                 port: int, timeout: float, action: str,
                 app_name: str) -> dict:
    """
    Log in, send the action once, and require RESULT_ACCEPTED.

    Never retried: an action the device took and did not answer would
    otherwise be sent twice. Only the connect is wrapped: a connection
    that cannot be opened is a DroboAppsActionError. Failures after the
    connect propagate unwrapped -- OSError (a broken pipe, a timeout)
    from the sends and receives, and config_cmd.ConfigError from
    _read_frame for a hang-up, a bad signature or a short frame -- and
    by then the action frame may already be on the wire, so none of
    them means the action was not taken.
    """
    from .config_cmd import RESULT_ACCEPTED, _parse_result_code

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise DroboAppsActionError(
            f"could not reach the command port: {exc}") from exc
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        frame_type, _ = _read_frame(sock)
        if frame_type != TYPE_LOGIN_OK:
            raise DroboAppsActionError(
                f"login refused (type 0x{frame_type:02x})")
        sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        frame_type, body = _read_frame(sock)
        if frame_type != TYPE_RESULT:
            raise DroboAppsActionError(
                f"unexpected reply type 0x{frame_type:02x}")
        result_code = _parse_result_code(body or b"")
        if result_code != RESULT_ACCEPTED:
            raise DroboAppsActionError(
                f"the device did not accept {action} {app_name!r} "
                f"(Result {result_code!r}, expected {RESULT_ACCEPTED}).")
        return {"name": app_name, "action": action, "result_code": result_code}
    finally:
        sock.close()
