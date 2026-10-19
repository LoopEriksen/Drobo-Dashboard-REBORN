"""
eCmdGetDroboAppsStatus -- what's installed on the Drobo itself.

DroboApps are packages that run ON the NAS: Python, rsync, Plex, an SSH server.
This is how people extended these boxes, and it is the one place the device
tells you about software rather than storage.

Measured on the live 5N (firmware 4.3.1): answers on a plain login, no crypto
handshake, returning a real list with names, versions, descriptions, whether
each is stopped, and whether it has a web interface.

Worth knowing: **DroboPix is a DroboApp, so where it is installed it answers
here.** That is the photo-upload service Phase 4 of this project sets out to
replace. Whether it still functions with its vendor servers gone is a separate
question -- but the app, its version and its web interface are all
discoverable from here, which is a better starting point for Phase 4 than
guessing.

Like eCmdGetSysInfo, the reply nests its payload as an entity-escaped XML
document inside <ResultDetails>, so it is parsed with the same unwrapping
helper rather than by looking for child elements that are not there.
"""
from __future__ import annotations

import socket

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
    # A pure predicate over a write summary -- no socket, nothing to gate --
    # so unlike set_config it is imported at module level with the rest.
    decided_by_readback,
)
from .sysinfo import SysInfoError, _inner_document

import xml.etree.ElementTree as ET

APPS_STATUS_COMMAND_NAME = "eCmdGetDroboAppsStatus"


def _text(node, tag: str) -> str:
    found = node.find(tag)
    return (found.text or "").strip() if found is not None else ""


def parse_droboapps(payload: bytes) -> dict:
    """
    Turn a DroboApps reply into {'sdk_version', 'apps': [...]}.

    Separate from the socket work so it is testable with no device present.
    An app with no name is skipped -- a nameless entry is not something a
    person can act on, and showing a blank row is worse than showing nothing.
    """
    text = payload.rstrip(b"\x00").rstrip()
    if not text:
        return {"sdk_version": "", "apps": []}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SysInfoError(f"malformed result XML: {exc}") from exc

    details = root.find("ResultDetails")
    if details is None:
        raise SysInfoError("result carried no <ResultDetails>")

    inner = _inner_document(details)
    node = inner if inner is not None else details
    if node.tag != "DroboApps":
        # `is not None`, never `or`: an Element with no children is
        # falsy, so `or` would quietly fall back to the parent.
        apps_node = node.find("DroboApps")
        node = apps_node if apps_node is not None else node

    apps = []
    for el in node.findall("App"):
        name = _text(el, "Name")
        if not name:
            continue
        # "Stopped" is the device's own polarity, and it is inverted relative
        # to how anyone would say it out loud. Flip it here so every caller
        # above this line deals in "running", which is what a person reads.
        stopped = _text(el, "Stopped")
        apps.append({
            "name": name,
            "version": _text(el, "Version"),
            "description": _text(el, "Description"),
            "running": stopped == "0",
            "status": _text(el, "ActiveStatus"),
            "has_web_ui": bool(_text(el, "WebUI")),
        })

    apps.sort(key=lambda a: a["name"].lower())
    return {"sdk_version": _text(node, "SDKVersion"), "apps": apps}


def parse_dimming(payload: bytes) -> int | None:
    """
    LED brightness, from eCmdGetDimming.

    Unlike every other command here, this one does NOT nest a document: it
    puts a bare number straight into <ResultDetails>. The live 5N returned
    `59`. What the scale is -- percent, or 0-255, or Drobo's own steps -- is
    not documented anywhere in the firmware table, so this returns the raw
    integer and lets the caller present it without inventing a unit.

    Returns None rather than 0 when there is no reading. Zero is a legitimate
    brightness ("lights off"), so conflating the two would be a real bug.
    """
    text = payload.rstrip(b"\x00").rstrip()
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SysInfoError(f"malformed result XML: {exc}") from exc
    details = root.find("ResultDetails")
    if details is None:
        return None
    raw = (details.text or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_demo_mode(payload: bytes) -> dict:
    """
    eCmdGetDemoModeInfo -- whether the unit is in shop-display mode.

    Answered on the live 5N with `FirmwareInfo > DemoMode, ScaleFactor`. Demo
    mode is what a retailer switched on so a Drobo on a shelf would show
    plausible fake capacity instead of five empty bays; ScaleFactor is the
    multiplier it invented capacity with.

    Genuinely obscure, and surfaced anyway for one reason: **a unit left in demo
    mode reports capacity that is not real.** Every number this project displays
    would be a lie and nothing else would say so. On a normal unit this reads
    disabled and nobody ever thinks about it again -- but it costs ten lines to
    rule out, and the alternative is a dashboard that cannot tell a fabricated
    array from a real one.

    Returns {"demo_mode": bool|None, "scale_factor": int|None}. None means the
    device did not say, which is NOT the same as "off" -- see the caller, which
    only warns on an explicit yes.
    """
    text = payload.rstrip(b"\x00").rstrip()
    out: dict = {"demo_mode": None, "scale_factor": None}
    if not text:
        return out
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SysInfoError(f"malformed result XML: {exc}") from exc

    details = root.find("ResultDetails")
    if details is None:
        return out
    inner = _inner_document(details)
    node = inner if inner is not None else details
    if node.tag != "FirmwareInfo":
        firmware_node = node.find("FirmwareInfo")
        node = firmware_node if firmware_node is not None else node

    raw = _text(node, "DemoMode")
    if raw:
        # The device sends 0/1. Anything else is left as unknown rather than
        # coerced -- a truthy string would otherwise read as "demo mode on"
        # and put a false warning on a perfectly ordinary array.
        if raw in ("0", "1"):
            out["demo_mode"] = raw == "1"
    scale = _text(node, "ScaleFactor")
    if scale:
        try:
            out["scale_factor"] = int(scale)
        except ValueError:
            pass
    return out


def _query_reply_body(host: str, esa_id: str, command_name: str,
                      port: int = DEFAULT_PORT,
                      timeout: float = 8.0) -> bytes:
    """
    Send one query and hand back its raw reply body.

    Shared by the small reads that need no bespoke socket dance of their own
    (dimming, demo mode). The guard and the login are identical either way, so
    duplicating them per command would just be three more places for the
    do-not-send check to be forgotten.
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
    """LED brightness as the device reports it. Read-only; see parse_dimming."""
    return parse_dimming(_query_reply_body(host, esa_id, "eCmdGetDimming", port, timeout))


# The fields the <General> block is known to carry, in the order the device
# sends them. CONFIRMED from the 2026-08-06 capture (two writes:
# FeatureStates/SpindownDelay/Dim = 5/0/59 and 7/14/59). The ORDER is part of
# the contract -- the serializer below emits exactly these, exactly so.
_GENERAL_FIELDS = ("FeatureStates", "SpindownDelay", "Dim")

# A sanity band, NOT a device limit -- the firmware documents no scale for Dim
# (see parse_dimming). The capture showed 1 and 59; get_dimming returns 59. If
# the true scale is 0-255 this still admits every real value, and it turns a
# fat-fingered 5900 into a refusal instead of a write. 0 is allowed: "lights
# off" is a legitimate brightness.
_DIMMING_MIN, _DIMMING_MAX = 0, 255

# SpindownDelay's band. Even looser and even less certain: the capture showed 0
# (disabled) and 14, and the units are unconfirmed -- minutes and a dropdown
# index are both consistent. 0..1440 (a day, if minutes) is a generous typo
# guard that constrains nothing real. 0 is "never spin down".
_SPINDOWN_MIN, _SPINDOWN_MAX = 0, 1440


class GeneralWriteError(SysInfoError):
    """A write to the <General> config block could not be done safely, or the
    device refused it. Base for the specific setters below so a caller can
    catch the whole family or a single field's own errors."""


class DimmingWriteError(GeneralWriteError):
    """LED brightness could not be set safely, or the device refused it."""


class SpindownWriteError(GeneralWriteError):
    """The spin-down delay could not be set safely, or the device refused it."""


def _extract_general_block(before: dict) -> dict:
    """
    Pull the <General> field dict out of whatever wrapper depth it arrived in.

    The real 5N wraps it: {'DRINASConfig': {'General': {...}}} (measured on
    firmware 4.3.1, 2026-08-10). It may also arrive as {'General': {...}} or as
    the fields directly. All three are accepted; anything else is refused,
    because writing a block built from a shape we did not recognise is exactly
    the guessing this path exists to avoid.

    An earlier version only looked for a TOP-LEVEL 'General' key and so did not
    see the DRINASConfig wrapper the device actually sends -- offline fixtures
    used the flat shape and hid it until the first real read. That is why the
    read-before-write happens against hardware before anything is trusted.
    """
    block = before
    if isinstance(block, dict) and "DRINASConfig" in block \
            and isinstance(block["DRINASConfig"], dict):
        block = block["DRINASConfig"]
    if isinstance(block, dict) and "General" in block and isinstance(block["General"], dict):
        block = block["General"]
    if not isinstance(block, dict) or not (set(block) & set(_GENERAL_FIELDS)):
        raise GeneralWriteError(
            "the device's General config did not parse into the expected fields; "
            "refusing to write a block built from an unrecognised shape "
            f"(got keys {sorted(block) if isinstance(block, dict) else type(block).__name__})")
    return block


def _general_setter(field=None, new_value=None, *, changes: dict | None = None):
    """
    Build a set_config build_section_document that changes ONLY the named
    field(s) in the <General> block and echoes every other sibling back
    exactly as read.

    Call as _general_setter("Dim", 5) for one field, or
    _general_setter(changes={"FeatureStates": 5, "SpindownDelay": 0}) for
    several that must move together. Two coupled fields exist for a reason:
    disabling spin-down is BOTH clearing the FeatureStates bit AND zeroing the
    delay -- the device coerces a lone delay of 0 back to a default while the
    bit still says "enabled" (measured on hardware 2026-08-12).

    Shared by set_dimming and set_spindown because the safety is identical and
    must not drift: it refuses if the block carries a field beyond the three
    confirmed (would drop it), if it is missing one (would write a block unlike
    what was read), or if any ECHOED sibling is not a scalar string (would emit
    a Python repr). The field being SET is exempt from the scalar check -- its
    old shape does not matter, it is overwritten.
    """
    targets = dict(changes) if changes else {field: new_value}
    bad = set(targets) - set(_GENERAL_FIELDS)
    if bad:
        raise GeneralWriteError(f"cannot set unknown General field(s) {sorted(bad)}")

    def build_section_document(before: dict) -> str:
        block = _extract_general_block(before)

        unknown = set(block) - set(_GENERAL_FIELDS)
        if unknown:
            raise GeneralWriteError(
                f"the General block has fields this build does not know how to "
                f"preserve ({sorted(unknown)}); refusing to write rather than "
                f"risk dropping them")
        missing = set(_GENERAL_FIELDS) - set(block)
        if missing:
            raise GeneralWriteError(
                f"the General block is missing expected fields ({sorted(missing)}); "
                f"refusing to write a block that does not match what was read")

        for f in _GENERAL_FIELDS:
            if f in targets:
                continue  # overwritten below; its prior shape is irrelevant
            if not isinstance(block[f], str):
                raise GeneralWriteError(
                    f"General field <{f}> came back as {type(block[f]).__name__}, "
                    f"not a scalar; refusing to serialize a value we cannot "
                    f"round-trip faithfully")

        out = dict(block)
        for f, v in targets.items():
            out[f] = str(v)
        parts = "".join(f"<{f}>{out[f]}</{f}>" for f in _GENERAL_FIELDS)
        return f"<General>{parts}</General>"

    return build_section_document


def set_dimming(host: str, esa_id: str, value: int, port: int = DEFAULT_PORT,
                timeout: float = 8.0, verify: bool = True) -> dict:
    """
    Set the front-panel LED brightness. The first configuration WRITE this
    project sends -- and it is a genuine read-modify-write, not a blind poke.

    WHY THIS IS A SAFE WRITE
    ------------------------
    It qualifies under the same four tests that admitted Identify
    (commands.SAFE_WRITES): it touches no file and no disk pack, affects no
    availability, and it is REVERSIBLE -- read the current value first, and you
    can set it straight back. That read-back is not optional dressing; it is
    what turns "persist a setting" (which kept eCmdSetDimming out of the safe
    set) into something undoable.

    HOW THE SIBLINGS ARE PROTECTED
    ------------------------------
    The <General> block carries FeatureStates and SpindownDelay alongside Dim,
    and the device is written the WHOLE block every time (capture, 2026-08-06).
    So this reads the live block via config_cmd.set_config, changes ONLY <Dim>,
    and echoes the other two back exactly as read. If the block contains any
    field beyond the three we have confirmed -- a setting a newer firmware might
    add -- this REFUSES rather than dropping it. A write that silently discarded
    an unknown setting would be exactly the kind of quiet damage the whole
    read-modify-write dance exists to prevent.

    Note the device is NOT sent eCmdSetDimming (which the original Dashboard
    never uses); brightness rides in the General block via eCmdSetConfig, which
    is why this lives on set_config's vetted write path.

    `verify` (default True) re-reads the brightness after an accepted write and
    confirms it actually changed. RESULT_ACCEPTED means the device RECEIVED the
    command, not that it applied it -- Identify returns the same code and blinks
    nothing -- so without this read-back "success" is only "not rejected". The
    read-back costs one more connection; pass verify=False only where that cost
    matters more than the confirmation.

    Returns config_cmd.set_config's summary, plus "verified" and the "read_back"
    value when verify is on. The summary's "acknowledged" is False when the
    device never answered and this brightness read-back is what established the
    change -- true, but on the device's state rather than its word. Raises
    DimmingWriteError on an out-of-band value, an unrecognised block, or a write
    the device accepted but did not apply; ConfigWriteError propagates for a
    refused or unreachable device, and ConfigWriteOutcomeUnknownError when neither
    the device nor a read-back could settle what happened.
    """
    # Import here, not at module load: keeps the read-only surface of this
    # module importable in contexts that never write, and mirrors how the
    # project gates its one other write path (identify.py).
    from .config_cmd import set_config, ConfigWriteError  # noqa: F401

    if isinstance(value, bool) or not isinstance(value, int):
        raise DimmingWriteError(
            f"brightness must be a whole number, not {type(value).__name__}")
    if not (_DIMMING_MIN <= value <= _DIMMING_MAX):
        raise DimmingWriteError(
            f"brightness {value} is outside the "
            f"{_DIMMING_MIN}-{_DIMMING_MAX} sanity "
            f"band; the scale is unconfirmed, so a value beyond it is far more "
            f"likely a mistake than an intention")

    # The read-back doubles as config_cmd's arbiter for a write the device never
    # acknowledges: sent, socket closed, no result frame. Reporting that as a
    # failure would be a lie (measured on a share create, 2026-08-25/26), so the
    # arbiter is passed whatever `verify` says -- see
    # config_cmd._send_section_document.
    def _reads_back() -> bool:
        return get_dimming(host, esa_id, port=port, timeout=timeout) == value

    result = set_config(host, esa_id, "general", _general_setter("Dim", value),
                        port=port, timeout=timeout, readback=_reads_back)

    # Stop here without a fresh read in two cases: config_cmd already settled
    # this write by reading the device back (its verdict IS the verification --
    # reading again only invites a second answer), or the caller asked for no
    # verification at all.
    if decided_by_readback(result) or not verify:
        return result

    # Confirm the write actually took, not merely that it was accepted.
    read_back = get_dimming(host, esa_id, port=port, timeout=timeout)
    result["read_back"] = read_back
    result["verified"] = read_back == value
    if read_back != value:
        raise DimmingWriteError(
            f"the device accepted the write but brightness reads back as "
            f"{read_back!r}, not {value}. The command was received and not "
            f"applied -- nothing here can be relied on as changed.")
    return result


def get_spindown(host: str, esa_id: str, port: int = DEFAULT_PORT,
                 timeout: float = 8.0) -> int | None:
    """
    The spin-down delay from the <General> block, or None if unreadable.

    Read-only. Reads the same block set_spindown writes, which is also how
    set_spindown verifies its own write -- one confirmed reader, no second
    command to depend on.
    """
    from .config_cmd import get_config
    block = _extract_general_block(get_config(host, esa_id, "general", port=port, timeout=timeout))
    raw = block.get("SpindownDelay")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# The FeatureStates bit that means "spin-down enabled". MEASURED on hardware
# 2026-08-12 (and consistent with the 2026-08-06 capture's 5<->7 toggle): with
# this bit SET, the device REFUSES a SpindownDelay of 0 and silently coerces it
# to a default (observed: sent 0, stored 15). So "off" is not "delay 0" -- it is
# clearing this bit AND zeroing the delay together, exactly as the original
# Dashboard sends it. Bits 0 and 2 stay set throughout and are unidentified.
_SPINDOWN_ENABLED_BIT = 0b010


def _feature_states(block: dict) -> int:
    raw = block.get("FeatureStates")
    if not isinstance(raw, str) or not raw.strip().isdigit():
        raise SpindownWriteError(
            f"FeatureStates read back as {raw!r}, not a number; refusing to "
            f"compute a bitmask from something we cannot parse")
    return int(raw)


def set_spindown(host: str, esa_id: str, delay: int, port: int = DEFAULT_PORT,
                 timeout: float = 8.0, verify: bool = True) -> dict:
    """
    Set the drive spin-down delay to a NONZERO value. Rides the vetted
    General-block path -- read the live block, change only <SpindownDelay>,
    echo the siblings, refuse anything it cannot preserve -- and enables
    spin-down (sets the FeatureStates bit) if it was off, because a delay with
    the feature disabled is a value nobody can observe.

    A delay of 0 is REFUSED here; use disable_spindown(). Measured on hardware
    2026-08-12: with spin-down enabled, the device accepts a delay of 0, returns
    the success code, and stores 15 -- it coerces the value rather than
    disabling. Read-back verification caught it. Zero only means "off" when
    the enable bit is cleared in the same write, which is a different operation
    with its own name so nobody can trip over this again.

    This sets a SETTING; it does not spin the drives down now (there is no such
    command -- capture, 2026-08-06). Reversible: read the current value first
    and set it back. Units unconfirmed (see _SPINDOWN_MAX).

    `verify` re-reads and confirms; RESULT_ACCEPTED means received, not applied.
    """
    from .config_cmd import set_config  # noqa: F401

    if isinstance(delay, bool) or not isinstance(delay, int):
        raise SpindownWriteError(
            f"spin-down delay must be a whole number, not {type(delay).__name__}")
    if delay == 0:
        raise SpindownWriteError(
            "a spin-down delay of 0 does not disable spin-down -- the device "
            "coerces it (measured: sent 0, stored 15). To turn spin-down off "
            "use disable_spindown(), which clears the enable bit as well.")
    if not (_SPINDOWN_MIN < delay <= _SPINDOWN_MAX):
        raise SpindownWriteError(
            f"spin-down delay {delay} is outside the 1-{_SPINDOWN_MAX} sanity "
            f"band; the units are unconfirmed, so a value beyond it is far more "
            f"likely a mistake than an intention")

    def changes_for(before: dict) -> dict:
        block = _extract_general_block(before)
        fs = _feature_states(block)
        out = {"SpindownDelay": delay}
        if not (fs & _SPINDOWN_ENABLED_BIT):
            out["FeatureStates"] = fs | _SPINDOWN_ENABLED_BIT
        return out

    # The changes depend on the live block (whether the bit is already set), so
    # build them inside build_section_document rather than ahead of the
    # read.
    def build_section_document(before: dict) -> str:
        return _general_setter(changes=changes_for(before))(before)

    def _reads_back() -> bool:
        return get_spindown(host, esa_id, port=port, timeout=timeout) == delay

    result = set_config(host, esa_id, "general", build_section_document,
                        port=port, timeout=timeout, readback=_reads_back)
    # Stop here without a fresh read in two cases: config_cmd already settled
    # this write by reading the device back (its verdict IS the verification --
    # reading again only invites a second answer), or the caller asked for no
    # verification at all.
    if decided_by_readback(result) or not verify:
        return result

    read_back = get_spindown(host, esa_id, port=port, timeout=timeout)
    result["read_back"] = read_back
    result["verified"] = read_back == delay
    if read_back != delay:
        raise SpindownWriteError(
            f"the device accepted the write but the spin-down delay reads back "
            f"as {read_back!r}, not {delay}. Received, not applied.")
    return result


def disable_spindown(host: str, esa_id: str, port: int = DEFAULT_PORT,
                     timeout: float = 8.0, verify: bool = True) -> dict:
    """
    Turn drive spin-down OFF: clear the FeatureStates enable bit AND zero the
    delay in ONE write -- the exact pair the original Dashboard sends (capture
    2026-08-06: FeatureStates 7->5 with SpindownDelay 14->0). Either half alone
    is wrong: a bare delay of 0 is coerced by the device (measured 2026-08-12),
    and clearing only the bit leaves a stale delay that re-enable would revive.

    Verified by read-back of BOTH fields. Reversible with set_spindown(delay).
    """
    from .config_cmd import set_config  # noqa: F401

    def build_section_document(before: dict) -> str:
        fs = _feature_states(_extract_general_block(before))
        return _general_setter(changes={
            "FeatureStates": fs & ~_SPINDOWN_ENABLED_BIT,
            "SpindownDelay": 0,
        })(before)

    from .config_cmd import get_config

    def _read_state() -> tuple[int, object]:
        block = _extract_general_block(get_config(host, esa_id, "general",
                                                  port=port, timeout=timeout))
        return _feature_states(block), block.get("SpindownDelay")

    def _reads_back() -> bool:
        fs, delay = _read_state()
        return not (fs & _SPINDOWN_ENABLED_BIT) and delay == "0"

    result = set_config(host, esa_id, "general", build_section_document,
                        port=port, timeout=timeout, readback=_reads_back)
    # Stop here without a fresh read in two cases: config_cmd already settled
    # this write by reading the device back (its verdict IS the verification --
    # reading again only invites a second answer), or the caller asked for no
    # verification at all.
    if decided_by_readback(result) or not verify:
        return result

    fs, delay = _read_state()
    ok = not (fs & _SPINDOWN_ENABLED_BIT) and delay == "0"
    result["read_back"] = {"FeatureStates": fs, "SpindownDelay": delay}
    result["verified"] = ok
    if not ok:
        raise SpindownWriteError(
            f"the device accepted the disable but reads back FeatureStates={fs} "
            f"SpindownDelay={delay!r}; expected the enable bit clear and delay 0. "
            f"Received, not applied.")
    return result


def get_demo_mode(host: str, esa_id: str, port: int = DEFAULT_PORT,
                  timeout: float = 8.0) -> dict:
    """Whether this unit is in shop-display mode. See parse_demo_mode."""
    return parse_demo_mode(_query_reply_body(host, esa_id, "eCmdGetDemoModeInfo", port, timeout))


def get_droboapps(host: str, esa_id: str, port: int = DEFAULT_PORT,
                  timeout: float = 8.0) -> dict:
    """Ask the Drobo what is installed on it. Read-only."""
    if _commands.is_dangerous(APPS_STATUS_COMMAND_NAME):
        raise SysInfoError(
            f"{APPS_STATUS_COMMAND_NAME} is on the do-not-send list")
    command_id = _commands.COMMANDS.get(APPS_STATUS_COMMAND_NAME)
    if command_id is None:
        raise SysInfoError(
            f"{APPS_STATUS_COMMAND_NAME} has no confirmed command id")

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
        return parse_droboapps(body)
    finally:
        sock.close()


# The two action verbs the capture confirmed, exactly as the device spells them.
_APP_ACTIONS = ("Start", "Stop")


class DroboAppsActionError(SysInfoError):
    """Starting or stopping a DroboApp could not be done safely, or the device
    refused it."""


def droboapps_action(host: str, esa_id: str, name: str, action: str,
                     port: int = DEFAULT_PORT, timeout: float = 8.0,
                     check_installed: bool = True) -> dict:
    """
    Start or stop an installed DroboApp. `action` is "Start" or "Stop".

    CONFIRMED format (2026-08-06 capture, both directions observed):

        <Params><Name>{app name}</Name><Action>Stop</Action></Params>

    Unlike the config writes this is a direct command, not eCmdSetConfig, so it
    has no read-modify-write to preserve -- but it is still a gated write
    (commands.VETTED_WRITES) sent by this one function, which validates before
    it acts:

      * `action` must be one of the two verbs the device actually uses. A
        typo'd "start" (lowercase) or "Restart" is refused, not sent hopefully.
      * `name` must be an app the device REPORTS as installed (unless
        check_installed is turned off). The app list comes from
        eCmdGetDroboAppsStatus, and the capture confirmed that exact string is
        what the action takes -- so this refuses to send an action for an app
        that is not there rather than let the device decide what to do with a
        name it does not recognise.

    Reversible: a stop is undone by a start. It touches an installed service,
    never the disk pack.

    NO read-back verification here, deliberately: a service does not start or
    stop instantly, so an immediate re-read of the status would race the state
    change and report a false failure. The caller should refresh the app list a
    moment later instead. This returns once the device has ACCEPTED the action
    (Result == RESULT_ACCEPTED); accepted is not the same as finished.

    Raises DroboAppsActionError on a bad verb, an unknown app, a refused device,
    or a non-accepted result.
    """
    from .config_cmd import RESULT_ACCEPTED, _parse_result_code

    if action not in _APP_ACTIONS:
        raise DroboAppsActionError(
            f"action must be one of {_APP_ACTIONS!r}, not {action!r} -- the "
            f"device's verbs are case-sensitive and only these two are confirmed")
    clean_name = (name or "").strip()
    if not clean_name:
        raise DroboAppsActionError("no app name given")

    command_name = "eCmdDroboAppsAction"
    if command_name not in _commands.VETTED_WRITES:
        raise DroboAppsActionError(f"{command_name} is not a vetted write (commands.VETTED_WRITES)")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise DroboAppsActionError(
            f"{command_name} has no confirmed command id")

    if check_installed:
        installed = {a["name"] for a in get_droboapps(host, esa_id, port, timeout).get("apps", [])}
        if clean_name not in installed:
            raise DroboAppsActionError(
                f"{clean_name!r} is not an installed DroboApp on this device "
                f"(installed: {sorted(installed)}); refusing to act on a name "
                f"the device did not report")

    # The Name value is escaped in case an app name ever carries a special
    # character; Action is one of two literals. quote=False matches the only
    # escaping the wire format was observed to use.
    import html
    safe_name = html.escape(clean_name, quote=False)
    params = f"<Name>{safe_name}</Name><Action>{action}</Action>"
    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><ESAID>{esa_id}</ESAID>"
        f"<Params>{params}</Params></TMCmd>").encode() + b"\x00"

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise DroboAppsActionError(f"could not reach the command port: {exc}") from exc
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        frame_type, _ = _read_frame(sock)
        if frame_type != TYPE_LOGIN_OK:
            raise DroboAppsActionError(f"login refused (type 0x{frame_type:02x})")
        sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        frame_type, body = _read_frame(sock)
        if frame_type != TYPE_RESULT:
            raise DroboAppsActionError(f"unexpected reply type 0x{frame_type:02x}")
        code = _parse_result_code(body or b"")
        if code != RESULT_ACCEPTED:
            raise DroboAppsActionError(
                f"the device did not accept {action} {clean_name!r} "
                f"(Result {code!r}, expected {RESULT_ACCEPTED}).")
        return {"name": clean_name, "action": action, "result_code": code}
    finally:
        sock.close()
