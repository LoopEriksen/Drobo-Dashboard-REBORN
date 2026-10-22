"""
Asking the Drobo for its configuration -- the first thing we *send* it.

Everything else in this project reads the status the device volunteers on TCP
5000. This module uses the command channel on TCP 5001, recovered 2026-07-25
(see docs/protocol-map.md). It sends exactly one command, eCmdGetConfig, which
is a read.

WHY THIS ONE COMMAND IS THE DEPENDABLE ONE
------------------------------------------
eCmdGetConfig answers reliably after a plain login -- measured repeatedly
against real hardware, including 3/3 at a moment when other commands had
stopped answering entirely. It is the command to build on.

An earlier version of this note said temperature was unreachable, gated behind
the RSA challenge-response. **That was wrong**, and the correction is in
sysinfo.py: eCmdGetSysInfo needs no handshake, and returns a real chassis
temperature. It only looked gated because its reply nests an *entity-escaped*
XML document inside <ResultDetails>, so code looking for a <Temperature>
element found nothing and recorded the command as unanswered.

What IS true is that eCmdGetSysInfo is not dependable the way this command is --
see sysinfo.py for the measurements and for why it is polled rarely.

CREDENTIALS -- READ THIS BEFORE CHANGING ANYTHING HERE
------------------------------------------------------
Asking for DRINasAdminConfig makes the Drobo hand back <Password>,
<EncryptedPassword> and <ValidPassword> to anyone who completes a login -- and
the login is just the device serial, which the Drobo gives to any
unauthenticated caller on port 5000.

**Measured 2026-07-26 on firmware 4.3.1**, and it is better news than we
previously recorded: <Password> comes back as sixteen *identical* characters --
a mask, not a credential -- and <EncryptedPassword> as a single character. This
firmware does not appear to disclose the real admin password. An earlier version
of this note asserted it handed them over "in the clear"; that was not measured,
and on this firmware it is wrong.

**The redaction stays anyway, and must not be removed.** We have checked one
section, on one firmware, on one device. We cannot promise the same for 4.2.x,
for a business-model Drobo, or for a section nobody has looked at yet. Costing
nothing to keep and being the only thing standing between a device quirk and a
credential in our logs, _strip_secrets() runs on every parsed document before it
is returned -- so those values never enter a Snapshot, never reach the JSON API,
never land in a log, and never sit in the history buffer.

Separately confirmed the same day: the admin account is **not** passwordless.
Presenting `admin` with an empty password makes the Drobo fall back to granting
a *guest* session rather than authenticating the account -- which is proof it
rejected the empty credential.

If you add a section here, assume it may contain credentials until you have
looked.
"""

from __future__ import annotations

import html
import re
import socket
import struct
import time
from xml.etree import ElementTree

from . import commands as _commands
# The number the device puts in <Result> when it accepts a command. Imported
# rather than duplicated so "did the write take" has one source of truth; see
# envelope.RESULT_ACCEPTED for the honest limits of that check. envelope pulls
# in only `commands`, so this does not cycle back through config_cmd.
from .envelope import RESULT_ACCEPTED

SIGNATURE = b"DRINETTM"
HEADER_BYTES = 16
DEFAULT_PORT = 5001
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024

TYPE_LOGIN = 0x07
TYPE_LOGIN_OK = 0x87
TYPE_COMMAND = 0x0A
TYPE_RESULT = 0x8A

# The login packet is the device serial twice, in two 20-byte NUL-padded slots,
# then padding to 220. Confirmed on the wire.
LOGIN_PAYLOAD_BYTES = 220
_LOGIN_SLOT_BYTES = 20

# Sections eCmdGetConfig understands. "admin" is deliberately last and
# deliberately noisy in the docs -- see the credentials note above.
#
# "general" was added 2026-08-10. A 2026-08-06 capture shows Drobo Dashboard
# reading it with exactly this <General>General</General> shape (twice), then
# writing it back (twice) -- the read-modify-write that set_config below
# reproduces. It carries FeatureStates, SpindownDelay and Dim (LED brightness).
SECTIONS = {
    "network": "Network",
    "shares": "DRIShareConfig",
    "admin": "DRINasAdminConfig",
    "general": "General",
}

# The document root eCmdSetConfig writes wrap their section in. CONFIRMED from
# the 2026-08-06 capture: the escaped payload is <DRINASConfig><General>...
# (note the ALL-CAPS "NAS", which is the device's own spelling and must match).
_WRITE_ROOT_TAG = "DRINASConfig"

# Any element whose name matches this never leaves this module with its value.
_CREDENTIAL_NAME_RE = re.compile(r"(?i)pass|secret|credential|\bkey\b|token")
_REDACTION_SENTINEL = "[redacted]"


class ConfigError(Exception):
    """The command channel refused us, or answered with something unusable."""


def _build_frame(frame_type: int, payload: bytes) -> bytes:
    return (SIGNATURE + bytes([frame_type, 0x01]) + b"\x00\x00"
            + struct.pack(">I", len(payload)) + payload)


def _login_payload(esa_id: str) -> bytes:
    serial_bytes = esa_id.encode("ascii", "ignore")
    if not serial_bytes or len(serial_bytes) > _LOGIN_SLOT_BYTES:
        raise ConfigError(f"implausible device serial {esa_id!r}")
    login_bytes = bytearray(LOGIN_PAYLOAD_BYTES)
    login_bytes[0:len(serial_bytes)] = serial_bytes
    login_bytes[_LOGIN_SLOT_BYTES:
                _LOGIN_SLOT_BYTES + len(serial_bytes)] = serial_bytes
    return bytes(login_bytes)


def _receive_exactly(sock: socket.socket, byte_count: int, deadline: float | None = None) -> bytes:
    """
    Read exactly byte_count bytes, or as many as arrive before the socket
    gives up.

    `deadline` is a time.monotonic() instant that bounds the WHOLE read, and
    it matters because sock.settimeout() does not. A socket timeout is per
    recv call, so a peer that sends one byte just inside every timeout window
    keeps this loop alive for as long as it likes -- and with MAX_PAYLOAD_BYTES
    at 4 MB it can declare a length that makes "as long as it likes" mean
    hours.

    This is the same defect esatm._receive_exactly was fixed for, carried
    across to the channel where it was still live. Nothing about port 5001
    makes the peer answering on it any more trustworthy than the one on 5000,
    and the stall costs more here: a write left waiting on a result frame that
    never finishes arriving is an outcome-unknown write, held open for as long
    as the peer cares to hold it.

    Left at None -- the default -- the read is unbounded exactly as it has
    always been, so every caller that passes no deadline is unaffected.

    The error is this module's ConfigError rather than esatm's FrameError for
    the same reason every other framing failure here is one: that is what
    callers of the command channel catch, and _send_section_document already
    reads a ConfigError from this read as "the result frame was lost" -- which
    is precisely what a read that ran out of budget is.
    """
    received = b""
    # The shrinking timeout below belongs to THIS read. Left in place it
    # would silently become the budget for whatever the caller does next on
    # the same socket -- and on the command channel the next thing is always
    # a sendall of the command frame, so a login reply that arrived late in
    # the budget would leave that send with the remainder of the budget
    # instead of the timeout the caller asked for. Hand the socket back as
    # it was found.
    original_timeout = sock.gettimeout() if deadline is not None else None
    try:
        while len(received) < byte_count:
            if deadline is not None:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise ConfigError(
                        f"timed out reading frame: got {len(received)} of {byte_count} bytes")
                # Shrink the per-recv wait as the budget runs down, or the very
                # last recv could still overshoot by a full socket timeout.
                sock.settimeout(remaining_seconds)
            chunk = sock.recv(min(65536, byte_count - len(received)))
            if not chunk:
                break
            received += chunk
    finally:
        if deadline is not None:
            try:
                sock.settimeout(original_timeout)
            except OSError:
                # The socket is already unusable -- the peer went away and it
                # was closed underneath this read. That is the read's own
                # failure to report; restoring a timeout on it must not
                # replace that error with a confusing one from the cleanup.
                pass
    return received


def _read_frame(sock: socket.socket,
                deadline: float | None = None) -> tuple[int, bytes]:
    """
    Read one DRINETTM frame: its type byte and its raw payload.

    `deadline` (a time.monotonic() instant) bounds header and payload
    together; see _receive_exactly for why a socket timeout alone is not a
    bound. Keyword-optional so existing callers keep working unchanged.
    """
    header = _receive_exactly(sock, HEADER_BYTES, deadline)
    if len(header) < HEADER_BYTES:
        raise ConfigError(
            f"short header: {len(header)} of {HEADER_BYTES} bytes")
    if header[:8] != SIGNATURE:
        raise ConfigError(f"bad signature {header[:8]!r}")
    length = struct.unpack(">I", header[12:16])[0]
    if length > MAX_PAYLOAD_BYTES:
        raise ConfigError(f"implausible payload length {length}")
    body = _receive_exactly(sock, length, deadline)
    if len(body) < length:
        # Name truncation as truncation. Returned silently, a write reply cut
        # off before <Result> read as "the device did not accept" -- a
        # rejection -- when the truth is "the connection dropped after the
        # command was sent", which is the outcome-unknown case a caller must
        # treat differently (do not blindly retry a write).
        raise ConfigError(f"short payload: {len(body)} of {length} bytes")
    return header[8], body


def _strip_secrets(node: ElementTree.Element) -> None:
    """
    Replace the text of any credential-looking element, in place, before the
    document is turned into anything we keep. Recursive, so a password nested
    inside a share or user entry is caught too.

    Two things here are deliberate, and both were holes until 2026-07-26:

    1. A credential element is emptied ENTIRELY -- children removed, not just
       its own text replaced. Previously a `<Password>` carrying child elements
       rather than text kept those children verbatim, because the old code
       replaced `.text` (empty in that case) and then skipped descending. The
       value walked straight out through _element_to_dict.

    2. Text that turns out to be an escaped XML document is handled by
       _redact_escaped_document below, not here -- see the note there. That is
       the encoding this firmware genuinely uses.
    """
    for child in node:
        if _CREDENTIAL_NAME_RE.search(child.tag):
            # Whatever hangs off a credential node is suspect, so nothing from
            # it survives: no text, no attributes, no children.
            child.text = _REDACTION_SENTINEL
            child.tail = child.tail  # keep layout, drop nothing else
            child.attrib.clear()
            for grandchild in list(child):
                child.remove(grandchild)
            continue
        _strip_secrets(child)


def _redact_escaped_document(node: ElementTree.Element) -> None:
    """
    Catch credentials hiding inside an entity-escaped inner document.

    WHY THIS EXISTS
    ---------------
    `_strip_secrets` matches on element *tags*. That works when the device
    returns real XML children -- which is what eCmdGetConfig does. But this
    firmware also returns results as an XML document *escaped into a text
    node*: `&lt;Password&gt;...&lt;/Password&gt;` rather than `<Password>`.
    That is how eCmdGetSysInfo replies (see sysinfo.py, where the same
    encoding is what made temperature look unobtainable for weeks).

    Against that shape `_strip_secrets` has nothing to match -- the element has
    no children -- so the whole document, credentials included, passed through
    untouched. Both this module's and nasd_sweep's docstrings claimed redaction
    ran on "every response ... with no exception". It didn't.

    So: any text that unescapes into something XML-shaped is parsed, redacted
    with the same rules, and re-serialized. If it cannot be parsed, it is
    replaced wholesale rather than trusted -- an unreadable blob that might be
    a credential document is not something to pass along on the hope that it
    isn't.
    """
    for child in node.iter():
        text = (child.text or "").strip()
        # NOTE: by the time we see it, ElementTree has ALREADY turned &lt; back
        # into '<' -- so look for a literal angle bracket, not for the entity.
        # (Checking for "&lt;" here was the first attempt at this fix, and it
        # never matched a single real document.)
        if not text or "<" not in text:
            continue
        unescaped_text = html.unescape(text).strip()
        if not unescaped_text.startswith("<"):
            continue
        # Drop an inner <?xml ...?> declaration; ElementTree rejects one that
        # isn't at the very start of the string it is handed.
        document_text = unescaped_text
        if document_text.startswith("<?xml"):
            end = document_text.find("?>")
            if end != -1:
                document_text = document_text[end + 2:].strip()
        try:
            inner_root = ElementTree.fromstring(document_text)
        except ElementTree.ParseError:
            # Unparseable but XML-shaped. Refuse to pass it on.
            if _CREDENTIAL_NAME_RE.search(text):
                child.text = _REDACTION_SENTINEL
            continue
        if _CREDENTIAL_NAME_RE.search(inner_root.tag):
            child.text = _REDACTION_SENTINEL
            continue
        _strip_secrets(inner_root)
        child.text = ElementTree.tostring(inner_root, encoding="unicode")


def _element_to_dict(node: ElementTree.Element):
    """XML subtree -> plain dict/list/str, with repeated tags becoming lists."""
    children = list(node)
    if not children:
        return (node.text or "").strip()
    converted: dict = {}
    for child in children:
        value = _element_to_dict(child)
        if child.tag in converted:
            if not isinstance(converted[child.tag], list):
                converted[child.tag] = [converted[child.tag]]
            converted[child.tag].append(value)
        else:
            converted[child.tag] = value
    return converted


def parse_config_result(payload: bytes, redact: bool = True) -> dict:
    """
    Turn a command result into a plain dict.

    `redact` defaults to True and MUST stay True for anything that will be
    returned to a caller, logged, put in a Snapshot, or shown -- it replaces
    credential-looking values with a sentinel so a secret never enters our
    structures. See the module header.

    redact=False exists for ONE purpose: the write path. A read-modify-write
    has to echo back every field it is not changing, and a masked password
    echoed back would overwrite the real one with the literal "[redacted]" --
    credential corruption on the single unit available (found by the write-safety
    audit, 2026-08-10, before any secret-bearing write was wired). So set_config
    reads its baseline with redact=False and keeps that document strictly local:
    it is never returned, never logged, only serialized straight back to the
    device it came from. Do not widen its use.

    Kept separate from the socket work so it can be tested against a recorded
    response with no device present.
    """
    text = payload.rstrip(b"\x00").rstrip()
    if not text:
        return {}
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ConfigError(f"malformed result XML: {exc}") from exc

    details = root.find("ResultDetails")
    if details is None:
        raise ConfigError("result carried no <ResultDetails>")

    # Redact BEFORE converting, so a secret never exists in our structures.
    # Both passes are needed: one for real child elements, one for an inner
    # document the device escaped into a text node.
    if redact:
        _strip_secrets(details)
        _redact_escaped_document(details)
    return _element_to_dict(details)


def get_config(host: str, esa_id: str, section: str = "network",
               port: int = DEFAULT_PORT, timeout: float = 10.0,
               redact: bool = True) -> dict:
    """
    Ask the Drobo for one configuration section. Read-only.

    section is a key of SECTIONS. Raises ConfigError on anything unexpected --
    callers should treat a failure as "this data is unavailable right now",
    never as a reason to stop monitoring.

    `redact` defaults to True and should stay so for every ordinary read. Only
    set_config sets it False, for a write baseline it keeps strictly local; see
    parse_config_result.
    """
    if section not in SECTIONS:
        raise ConfigError(f"unknown section {section!r}; choose from {sorted(SECTIONS)}")

    command_name = "eCmdGetConfig"
    if command_name in _commands.DO_NOT_SEND:
        raise ConfigError(f"{command_name} is on the do-not-send list")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise ConfigError(f"{command_name} has no confirmed command id")

    section_tag = SECTIONS[section]
    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><ESAID>{esa_id}</ESAID>"
        f"<Params><{section_tag}>{section_tag}</{section_tag}>"
        f"</Params></TMCmd>").encode() + b"\x00"

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        # `timeout` is the budget for each frame in turn, anchored as that
        # frame's read begins -- the same reading esatm.fetch_greeting gives
        # it, and the one the old per-recv socket timeout already implied. A
        # device that answers each frame inside it sees no change at all; a
        # peer that dribbles is now bounded instead of unbounded.
        frame_type, _ = _read_frame(sock, deadline=time.monotonic() + timeout)
        if frame_type != TYPE_LOGIN_OK:
            raise ConfigError(f"login refused (type 0x{frame_type:02x})")

        sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        frame_type, body = _read_frame(
            sock, deadline=time.monotonic() + timeout)
        if frame_type != TYPE_RESULT:
            raise ConfigError(f"unexpected reply type 0x{frame_type:02x}")
        if not body:
            raise ConfigError(f"device returned nothing for {section!r}")
        return parse_config_result(body, redact=redact)
    finally:
        sock.close()


class ConfigWriteError(ConfigError):
    """
    A configuration WRITE could not be performed safely, or the device refused
    it. Distinct from a plain ConfigError so a caller can tell "the read failed"
    apart from "the change itself was rejected or unsafe" -- the first is worth
    retrying, the second is not.
    """


class ConfigWriteOutcomeUnknownError(ConfigWriteError):
    """
    The write frame WAS put on the wire, the device never answered, and the
    read-back that would settle the question could not be completed either. So
    whether the change applied is genuinely UNKNOWN.

    MEASURED 2026-08-25/26 on a real 5N: a share-create was sent, the device
    closed the socket without a result frame, and the change APPLIED ANYWAY.
    A missing acknowledgement therefore says nothing about whether the device
    acted -- which is why this is a distinct outcome and not a failure.

    A subclass of ConfigWriteError only so existing `except ConfigWriteError`
    callers keep catching it. Anything that decides what to do next should catch
    THIS first: a refused write may be retried, an unknown-outcome write must
    NOT be, because retrying a write that already applied is the harm. Re-read
    the device and decide from what it now holds.
    """


def _parse_result_code(payload: bytes) -> int | None:
    """
    The integer in the reply's <Result>, or None if it is absent or ambiguous.

    Regex, not a parse: this runs on a reply that may be empty or truncated, and
    a parse failure must not turn an uncertain write outcome into an exception.
    But it requires EXACTLY ONE <Result> -- a body with several is not something
    to guess the meaning of by taking the first, so it returns None (which the
    caller treats as "not accepted") rather than risk reading the wrong one.
    """
    matches = re.findall(r"<Result>\s*(-?\d+)\s*</Result>",
                         payload.decode("utf-8", "replace"))
    if len(matches) != 1:
        return None
    try:
        return int(matches[0])
    except ValueError:
        return None


def set_config(host: str, esa_id: str, section: str, build_section_document,
               port: int = DEFAULT_PORT, timeout: float = 10.0,
               readback=None) -> dict:
    """
    The one sanctioned way to send eCmdSetConfig. Read-modify-send, with the
    device's own current document as the starting point.

    THE MODEL, AND WHY IT IS SHAPED THIS WAY
    ----------------------------------------
    eCmdSetConfig (31) is the single command Drobo Dashboard uses for EVERY
    configuration write -- LED brightness, spin-down, rename, share
    permissions. So safety cannot live on the command id (it is, correctly, in
    commands.DESTRUCTIVE -- a naked SetConfig with a hand-written document is
    dangerous). Safety lives in the PAYLOAD, and this function is the only place
    allowed to build one, because it guarantees the two things that make a write
    defensible:

      1. It is built from a REAL PRIOR READ. `build_section_document` is handed
         the exact document the device returned microseconds earlier, so it can
         preserve every field it does not mean to change. The 2026-08-06
         capture proved this matters: the <General> block ships FeatureStates,
         SpindownDelay and Dim together, and a write of only <Dim> would
         plausibly clear the other two. Rename and share writes send the WHOLE
         section for the same reason.

      2. The device is asked whether it accepted it. The reply's <Result> must
         equal RESULT_ACCEPTED; anything else raises rather than being reported
         as success. Identify taught this: a well-formed reply is not the same
         as an accepted command.

    `build_section_document(before: dict) -> str` receives the parsed current
    section and must return the inner document to write (e.g.
    "<General>...</General>"), having changed only what it intends and
    having REFUSED -- by raising ConfigWriteError -- if the read shows anything
    it does not recognise well enough to preserve. The confinement rules are the
    caller's, because they are domain-specific (only <Dim> for brightness;
    shareedit's own guards for shares). This function owns the transport and
    the accepted-check, nothing more.

    `readback` is the optional arbiter for a write the device never answers --
    a zero-argument callable returning True when the device now shows the
    intended change. Pass it for every write that has a read-back available:
    without it, a write that applied but was never acknowledged can only be
    reported as ConfigWriteOutcomeUnknownError. See
    _send_section_document.

    Returns a small dict: what was sent, the device's result code, and whether
    the device acknowledged it (see _send_section_document's RESULT
    KEYS). Raises ConfigWriteError on a bad section, a failed read, a
    build refusal, a non-accepted result, a transport error before the frame
    went out, or a read-back that shows the change absent;
    ConfigWriteOutcomeUnknownError when the frame went out and neither the
    device nor a read-back could settle it.

    KNOWN LIMITATION -- the read and the write are separate connections, and
    nasd offers no locking, so this read-modify-write is not atomic. If some
    other client changes the same section between our read and our write, our
    write silently reverts their change (we echo back what WE read). The window
    is milliseconds and the only writer of a home Drobo's config is normally its
    owner, one change at a time -- but a share write, which sends every share,
    is where this would bite hardest, so callers doing high-stakes writes should
    prefer a moment when nothing else is touching the device. It is documented
    rather than fixed because the fix (device-side locking) does not exist.
    """
    if section not in SECTIONS:
        raise ConfigWriteError(
            f"unknown section {section!r}; choose from {sorted(SECTIONS)}")

    # The gate is real, not advisory. eCmdSetConfig is in commands.DESTRUCTIVE
    # so no other code path may send it; this function is the sanctioned
    # exception and says so by name, so "who can write config" is one grep.
    command_name = "eCmdSetConfig"
    if command_name not in _commands.VETTED_WRITES:
        raise ConfigWriteError(
            f"{command_name} is not a vetted write (commands.VETTED_WRITES)")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise ConfigWriteError(f"{command_name} has no confirmed command id")

    # Read first -- always, and WITHOUT redaction. A write must echo back every
    # field it does not change, and get_config's normal masking of credential
    # fields would otherwise write the literal "[redacted]" over a real
    # password. This document is strictly local: never returned, never logged.
    current_section = get_config(host, esa_id, section, port=port,
                                 timeout=timeout, redact=False)

    section_document = build_section_document(current_section)
    if not isinstance(section_document, str) or not section_document.strip():
        raise ConfigWriteError(
            "build_section_document must return a non-empty document string")

    return _send_section_document(host, esa_id, section, section_document,
                                  command_id, port, timeout,
                                  readback=readback)


def _send_section_document(host: str, esa_id: str, section: str,
                           section_document: str,
                           command_id: int, port: float, timeout: float,
                           readback=None) -> dict:
    """
    The shared tail of every eCmdSetConfig write: confine, escape, send, decide.

    Split out so the dict path (set_config) and the surgical raw-XML path
    (set_config_raw) run IDENTICAL safety checks and transport -- the confinement
    to the section's own root element, the redaction-sentinel refusal, the
    single correct escaping pass, and the accepted-check. A second copy of this
    is a second place for one of those guards to be forgotten.

    HOW THE OUTCOME IS DECIDED -- READ-BACK ARBITRATION
    ---------------------------------------------------
    A write has two independent questions: did the device SAY yes, and did the
    change actually LAND. They can disagree in both directions, and this project
    has now measured both:

      * accepted but not applied -- RESULT_ACCEPTED came back and nothing
        changed (spin-down delay 0, measured 2026-08-12). Callers' read-backs
        catch that one, and that path is untouched here.
      * applied but never acknowledged -- the device took the write, then closed
        the socket without sending a result frame (share create, measured
        2026-08-25/26; the change WAS stored). The old code raised on the
        unreadable frame and reported a successful write as a failure, which
        invites a retry of a write that already happened.

    So: once the command frame is fully on the wire, a TRANSPORT failure while
    awaiting the acknowledgement is never reported as a failed write. `readback`
    -- a zero-argument callable returning True when the device now shows the
    intended change -- becomes the arbiter:

        read-back True   -> success, `acknowledged` False (see the result keys)
        read-back False  -> genuine failure, ConfigWriteError
        read-back itself unusable, or none supplied -> ConfigWriteOutcomeUnknownError

    Nothing here ever re-sends. A retry after an unacknowledged-but-applied
    write is precisely the harm this arbitration exists to prevent.

    RESULT KEYS
    -----------
    section, sent_root, sent_bytes as before, plus:
      result_code -- the device's <Result>, or None when it never answered.
      acknowledged -- True when the device itself returned RESULT_ACCEPTED;
        False when it never answered and the read-back decided. (Callers that
        send nothing at all report None: there was nothing to acknowledge.)
      verified, note -- present only on the arbitrated path, where this function
        has already done the read-back the caller would otherwise do.
    """
    # Confine the write to the section that was read. Without this, the "one
    # sanctioned path" is only a convention: an empty <DRIShareConfig/> could
    # wipe every share. Parse it and require its root to be the section's own
    # element.
    try:
        root_element = ElementTree.fromstring(section_document)
    except ElementTree.ParseError as exc:
        raise ConfigWriteError(f"the document to write is not valid XML: {exc}") from exc
    expected_root_tag = SECTIONS[section]
    if root_element.tag != expected_root_tag:
        raise ConfigWriteError(
            f"the document root is <{root_element.tag}> but section "
            f"{section!r} writes <{expected_root_tag}>; refusing a document "
            f"that does not match the section it was built from")

    # Belt and braces for the redaction fix: a real write document must never
    # carry the sentinel. If it does -- a device that literally stored
    # "[redacted]", or a value sourced from a redacted read -- refuse rather
    # than write a mask over a real value.
    if _REDACTION_SENTINEL in section_document:
        raise ConfigWriteError(
            f"the document to write contains the redaction sentinel {_REDACTION_SENTINEL!r}; "
            f"refusing -- a masked value must never be written back to the device")

    # The device escapes the inner document into <Params>. quote=False so only
    # &lt; &gt; &amp; are produced -- the only escaping the capture confirmed;
    # emitting &#x27;/&quot; for an apostrophe in a device or share name is not
    # something the wire format was ever observed to accept.
    escaped_document = html.escape(f"<{_WRITE_ROOT_TAG}>{section_document}</{_WRITE_ROOT_TAG}>", quote=False)
    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><ESAID>{esa_id}</ESAID>"
        f"<Params>{escaped_document}</Params></TMCmd>").encode() + b"\x00"

    # Everything from here to the sendall below is the "nothing happened" half
    # of the write. A refused connection, a rejected login, a login reply we
    # cannot read: the command frame has not left this process, so the device
    # cannot have acted on it, and a plain failure is the honest report.
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise ConfigWriteError(f"could not reach the command port: {exc}") from exc
    sock.settimeout(timeout)
    # The transport error that ate the result frame, if one does.
    acknowledgement_error = None
    try:
        # Typed as a write failure rather than left as a bare transport error.
        # The layers above read an untyped ConfigError as "the write may have
        # applied", which would be a lie about a login that never got as far
        # as sending the command frame.
        try:
            sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
            frame_type, _ = _read_frame(sock, deadline=time.monotonic() + timeout)
        except (OSError, ConfigError) as exc:
            raise ConfigWriteError(f"could not log in to write: {exc}") from exc
        if frame_type != TYPE_LOGIN_OK:
            raise ConfigWriteError(f"login refused (type 0x{frame_type:02x})")

        # A partial sendall raises too, and that also counts as not sent: a
        # truncated DRINETTM frame is a length the device is still waiting to
        # fill, not a command it can parse and act on.
        try:
            sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        except OSError as exc:
            raise ConfigWriteError(f"the write was not sent: {exc}") from exc

        # ------------------------------------------------------------------
        # THE LINE. The write is now on the wire and out of our hands. Below
        # this point NO transport failure may be reported as "the write
        # failed" -- see the read-back arbitration note in the docstring.
        # ------------------------------------------------------------------
        try:
            # A peer that never finishes the result frame is the "went silent"
            # case below, now reached in bounded time rather than never: the
            # budget runs out, the ConfigError lands here, and the read-back
            # settles the outcome exactly as it does for a dropped connection.
            frame_type, body = _read_frame(sock, deadline=time.monotonic() + timeout)
        except (OSError, ConfigError) as exc:
            # The device hung up (or went silent) before answering. Not an
            # answer of "no" -- no answer at all.
            acknowledgement_error = exc

        if acknowledgement_error is None:
            # The device answered. This is the path that already worked, and it
            # is deliberately unchanged: a real result frame still decides the
            # outcome on its own, with no read-back involved.
            if frame_type != TYPE_RESULT:
                # Noise, not a verdict. A reply we cannot read as a result is
                # no more an answer of "no" than silence is, and the command
                # frame is already on the wire either way -- so the read-back
                # settles it, exactly as it does when nothing comes back.
                acknowledgement_error = ConfigError(
                    f"unexpected reply type 0x{frame_type:02x}")
            else:
                result_code = _parse_result_code(body or b"")
                if result_code != RESULT_ACCEPTED:
                    raise ConfigWriteError(
                        f"the device did not accept the write "
                        f"(Result {result_code!r}, "
                        f"expected {RESULT_ACCEPTED}). "
                        f"Nothing is confirmed changed.")
                # NOT the document itself: for a secret-bearing section (rename
                # carries password fields) that would hand the un-redacted
                # baseline -- which this function promises to keep strictly
                # local -- straight to the caller. A size and root are enough
                # to log or display.
                return {"section": section, "sent_root": root_element.tag,
                        "sent_bytes": len(section_document),
                        "result_code": result_code,
                        "acknowledged": True}
    finally:
        sock.close()

    # Reached only when the frame went out and the acknowledgement did not come
    # back. The socket is closed first so the arbitrating read starts clean.
    return _arbitrate_unacknowledged(section, root_element.tag,
                                     len(section_document),
                                     readback, acknowledgement_error)


def decided_by_readback(result: dict) -> bool:
    """
    True when a write summary was settled by reading the device back, because
    the device itself never acknowledged the write.

    Callers use this for two things. A UI or API layer needs it to say "the
    change is there, but the device never confirmed it" rather than a flat
    "done". A write function needs it to SKIP its own confirming read-back:
    _send_section_document has already done that read and only returns at all
    when the change was present, so reading again just invites a second answer
    that could disagree with the first.
    """
    return result.get("acknowledged") is False


def _arbitrate_unacknowledged(section: str, sent_root: str, sent_bytes: int,
                              readback, cause: Exception) -> dict:
    """
    Decide the outcome of a write the device never acknowledged, by asking the
    device what it now holds. Never re-sends anything.

    The wording of every outcome here matters as much as the branch it reports:
    a caller told "the write failed" will retry, and retrying a write that
    already applied is the harm this whole path exists to prevent. So no message
    below says or implies that nothing happened.
    """
    if readback is None:
        raise ConfigWriteOutcomeUnknownError(
            f"the write to section {section!r} WAS sent, then the device closed "
            f"the connection without answering ({type(cause).__name__}: {cause}), "
            f"and no read-back was available to settle it. The write MAY have "
            f"applied -- a device that hangs up after a write has been measured "
            f"to apply it anyway. Re-read the configuration before deciding "
            f"anything, and do NOT simply retry the write.") from cause

    try:
        applied = readback()
    except Exception as exc:
        # Deliberately any exception, not just transport ones. Past THE LINE the
        # only honest verdicts are "applied", "did not apply" and "unknown"; an
        # arbiter that fell over for any reason leaves us in the third, and
        # letting its exception escape would surface as a failed write. The
        # cause chain keeps a genuine bug in the arbiter findable.
        raise ConfigWriteOutcomeUnknownError(
            f"the write to section {section!r} WAS sent, then the device closed "
            f"the connection without answering ({type(cause).__name__}: {cause}), "
            f"and the read-back that would settle it could not be completed "
            f"either ({type(exc).__name__}: {exc}). The write MAY have applied. "
            f"Re-read the configuration before deciding anything, and do NOT "
            f"simply retry the write.") from exc

    if not applied:
        # The device is the arbiter and it says the change is not there. This
        # is the one branch where a failed write is the truth.
        raise ConfigWriteError(
            f"the write to section {section!r} was sent, the device never "
            f"answered ({type(cause).__name__}: {cause}), and the read-back "
            f"shows the change is NOT present. The write did not apply.")

    return {"section": section, "sent_root": sent_root,
            "sent_bytes": sent_bytes, "result_code": None,
            "acknowledged": False, "verified": True,
            "note": (f"the device never acknowledged this write -- it closed "
                     f"the connection first ({type(cause).__name__}: {cause}) "
                     f"-- but the read-back shows the change is present. "
                     f"Believed applied on the device's own state, not on its "
                     f"word.")}


def get_config_element(host: str, esa_id: str, section: str,
                       port: int = DEFAULT_PORT, timeout: float = 10.0):
    """
    Read one section and return its raw XML Element -- un-redacted, un-flattened.

    The surgical counterpart to get_config's dict. For a whole-section write
    (shares, rename) a dict round-trip is lossy: it drops attributes, collapses
    <UserList/> to an empty string, and cannot promise the reserialized document
    matches what the device sent. Editing the Element in place and reserializing
    it changes exactly the one node you touch and leaves every other byte the
    device's own. Strictly local, like the redact=False read it is built on:
    never returned to a UI, never logged.
    """
    if section not in SECTIONS:
        raise ConfigError(f"unknown section {section!r}; choose from {sorted(SECTIONS)}")

    command_name = "eCmdGetConfig"
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise ConfigError(f"{command_name} has no confirmed command id")
    section_tag = SECTIONS[section]
    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><ESAID>{esa_id}</ESAID>"
        f"<Params><{section_tag}>{section_tag}</{section_tag}>"
        f"</Params></TMCmd>").encode() + b"\x00"

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        frame_type, _ = _read_frame(sock, deadline=time.monotonic() + timeout)
        if frame_type != TYPE_LOGIN_OK:
            raise ConfigError(f"login refused (type 0x{frame_type:02x})")
        sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        frame_type, body = _read_frame(
            sock, deadline=time.monotonic() + timeout)
        if frame_type != TYPE_RESULT:
            raise ConfigError(f"unexpected reply type 0x{frame_type:02x}")
        if not body:
            raise ConfigError(f"device returned nothing for {section!r}")
    finally:
        sock.close()

    root = ElementTree.fromstring(body.rstrip(b"\x00").rstrip())
    details = root.find("ResultDetails")
    if details is None:
        raise ConfigError("result carried no <ResultDetails>")
    # The section element may be a direct child of ResultDetails, nested under a
    # <DRINASConfig> wrapper (which is what the real 5N sends -- measured
    # 2026-08-10), or an escaped inner document in the text.
    # `.iter(section_tag)` finds it at any depth in the first two cases;
    # the escaped case is handled below.
    # An earlier version used details.find(tag), which only searches direct
    # children and so missed the wrapper the device actually uses.
    section_element = next(iter(details.iter(section_tag)), None)
    if section_element is not None:
        return section_element
    text = (details.text or "").strip()
    if text and "<" in html.unescape(text):
        inner_root = ElementTree.fromstring(html.unescape(text))
        escaped_section_element = (
            inner_root if inner_root.tag == section_tag
            else next(iter(inner_root.iter(section_tag)), None))
        if escaped_section_element is not None:
            return escaped_section_element
    raise ConfigError(
        f"reply for {section!r} contained no <{section_tag}> element")


def set_config_raw(host: str, esa_id: str, section: str, edit,
                   port: int = DEFAULT_PORT, timeout: float = 10.0,
                   readback=None) -> dict:
    """
    Surgical read-modify-send: hand `edit` the section's live XML Element to
    change in place, then send the whole thing back byte-for-byte except what
    it touched.

    This is the path for whole-section writes where faithful preservation
    matters more than convenience -- share permissions, rename. `edit(element)`
    receives the parsed <DRIShareConfig> (or <Network>...) Element, mutates it
    in place (typically one .text change on one node), and returns nothing. The
    element is un-redacted, so real credential values round-trip untouched;
    _send_section_document's sentinel check still guards against a mask
    slipping in.

    `edit` may instead RETURN the complete inner document as a string,
    built from the element it was handed. That exists for one reason:
    some captured byte shapes are outside what ElementTree's serializer
    can express -- the create-share node (captured 2026-08-25) carries
    an empty open-close <ShareMaxTMSizeGB></ShareMaxTMSizeGB> pair,
    which ElementTree can only emit as <ShareMaxTMSizeGB />. Mirroring a
    capture byte-for-byte therefore needs string-level control of the
    one piece being added. A returned document passes through every
    check in _send_section_document unchanged -- confinement to the
    section root, the sentinel refusal, the accepted-check -- so it is
    exactly as guarded as the mutate-in-place path.

    Same gate, confinement, accepted-check, read-back arbitration (`readback`)
    and KNOWN LIMITATION (non-atomic RMW) as set_config -- see there. Returns
    the same summary.
    """
    command_name = "eCmdSetConfig"
    if command_name not in _commands.VETTED_WRITES:
        raise ConfigWriteError(
            f"{command_name} is not a vetted write (commands.VETTED_WRITES)")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise ConfigWriteError(f"{command_name} has no confirmed command id")

    element = get_config_element(host, esa_id, section, port=port, timeout=timeout)
    replacement = edit(element)
    if replacement is None:
        section_document = ElementTree.tostring(element, encoding="unicode")
    elif isinstance(replacement, str) and replacement.strip():
        section_document = replacement
    else:
        raise ConfigWriteError(
            "edit must mutate the element in place (returning None) or return "
            "the complete inner document as a non-empty string; got "
            f"{type(replacement).__name__}")
    return _send_section_document(host, esa_id, section, section_document,
                                  command_id, port, timeout,
                                  readback=readback)
