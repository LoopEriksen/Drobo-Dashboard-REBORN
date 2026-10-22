"""
eCmdGetUserEventLog -- the Drobo's own event history, fetched as a file.

WHAT IS CAPTURED, AND WHAT IS NOT
---------------------------------
The REQUEST is CONFIRMED: the original Dashboard sends command 91 with

    <Params><Path>/uel.log</Path></Params>

captured on the wire 2026-08-05 (docs/protocol-map.md, "The event log is
command 91, not 90"). Two earlier failures are worth remembering: this project
first asked the wrong command entirely (eCmdGetEventLogs, which answers a clean
empty body), and then asked 91 itself with empty <Params> -- which the device
does not answer cleanly; it tears the connection down (docs/command-surface.md,
"a 0-byte header read where 16 bytes were expected"). So this module never
sends 91 without the captured Path, and a dropped connection mid-reply is
reported as its own failure mode rather than as a generic short read.

The REPLY has never been captured. The 2026-08-05/06 captures recorded the
request going out, not the body coming back, so the format of what 91 returns
-- line syntax, timestamp style, severity vocabulary -- is unknown. Under the
project rule that a wire format is never guessed, the parser below does NOT
invent one. It unwraps the channel's standard TMCmd envelope (the one shape
every answered command on port 5001 shares -- see sysinfo._inner_document for
the two encodings) and returns the payload as raw text lines, verbatim. Any
reply that turns out to be XML-STRUCTURED instead of text is refused with an
error saying a capture is needed, because flattening an unseen schema into
"lines" would be exactly the guessing this refuses to do.

Consequently the normalized entries carry TIMESTAMP_NOT_DECODED (0.0)
and SEVERITY_NOT_DECODED ("") -- explicit "the fetcher could not read
this from the line" markers, NOT a claim the event happened in 1970 or
was informational.
The full raw line rides in `message`, so nothing the device said is lost.
When a real reply is finally captured, parse_event_log is the single place to
teach the actual format; the entry shape callers see will not change.

Read-only. Sends one command, which fetches a log file.
"""
from __future__ import annotations

import html
import socket
from xml.etree import ElementTree

from . import commands as _commands
from .config_cmd import (
    DEFAULT_PORT,
    TYPE_COMMAND,
    TYPE_LOGIN,
    TYPE_LOGIN_OK,
    TYPE_RESULT,
    ConfigError,
    _build_frame,
    _login_payload,
    _read_frame,
)
from .models import EventLogEntry
from .sysinfo import SysInfoError

COMMAND_NAME = "eCmdGetUserEventLog"

# The one path the Dashboard was ever seen to ask for. Deliberately NOT a
# parameter of get_event_log: any other path would be a guessed instruction to
# the device, and the empty-params variant is known to drop the connection.
USER_EVENT_LOG_PATH = "/uel.log"

# Markers for fields the reply format does not (yet) let us decode. 0.0 and ""
# rather than None so EventLogEntry keeps its concrete field types and its
# to_dict stays crash-free; callers compare against these names, not literals.
TIMESTAMP_NOT_DECODED = 0.0
SEVERITY_NOT_DECODED = ""


class EventLogError(SysInfoError):
    """The device would not, or could not, hand over its user event log --
    or handed back a shape this code refuses to guess at."""


def _command_xml(esa_id: str) -> bytes:
    """The exact request the Dashboard was captured sending: command 91 with
    the /uel.log path, in the CmdID / Params / ESAID order of the capture."""
    command_id = _commands.COMMANDS.get(COMMAND_NAME)
    if command_id is None:
        raise EventLogError(f"{COMMAND_NAME} has no confirmed command id")
    return (f"<TMCmd><CmdID>{command_id}</CmdID>"
            f"<Params><Path>{USER_EVENT_LOG_PATH}</Path></Params>"
            f"<ESAID>{esa_id}</ESAID></TMCmd>").encode() + b"\x00"


def _display_lines(text: str) -> list[str]:
    """Split into display-ready lines: trailing whitespace (and the CR of a
    CRLF file) stripped, blank lines dropped, leading indentation kept in case
    the log uses it for continuation."""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip():
            lines.append(line)
    return lines


def parse_event_log(payload: bytes) -> list[str]:
    """
    Turn an eCmdGetUserEventLog result into the log's raw text lines.

    Separate from the socket work so it is testable with no device present.
    Deliberately returns LINES, not structure -- the reply format has never
    been captured (see the module docstring), so this refuses to invent
    timestamps or severities it cannot have read.

    Accepted shapes, most to least expected:

      * the standard TMCmd envelope with the log text entity-escaped into
        <ResultDetails> -- how eCmdGetSysInfo replies on this channel;
      * the same envelope with the text sitting there unescaped;
      * a bare non-XML body, in case a file fetch skips the envelope.

    An empty reply is an empty log ([]), not an error -- though the channel's
    usual caveat applies: an empty body can also mean "not authorized", and
    the two cannot be told apart (docs/command-surface.md).

    Raises EventLogError, refusing rather than guessing, when the reply is
    XML-STRUCTURED (child elements, or escaped text that unescapes into a
    well-formed document) -- an unseen schema needs a capture, not a guess --
    and when an XML-looking body does not parse, which is indistinguishable
    from a truncated envelope and must not be shown as log lines.
    """
    trimmed_payload = payload.rstrip(b"\x00")
    if not trimmed_payload.strip():
        return []

    if not trimmed_payload.strip().startswith(b"<"):
        # No envelope at all: treat as the file body served bare. Decoded with
        # errors="replace" -- a stray byte in somebody's log must not take the
        # whole fetch down.
        return _display_lines(trimmed_payload.decode("utf-8", "replace"))

    try:
        root = ElementTree.fromstring(trimmed_payload)
    except ElementTree.ParseError as exc:
        # Could be a truncated envelope, could be a text log whose first line
        # merely begins with "<". Not distinguishable, so fail loudly -- junk
        # markup presented as "events" would be worse than an error.
        raise EventLogError(f"XML-shaped reply did not parse: {exc}") from exc

    details = root.find("ResultDetails")
    if details is None:
        raise EventLogError("result carried no <ResultDetails>")
    if len(details):
        raise EventLogError(
            "the event-log reply carries XML child elements, a shape no "
            "capture has shown; refusing to flatten an unseen schema into "
            "lines -- capture a real reply and teach parse_event_log")

    details_text = details.text or ""
    if not details_text.strip():
        return []

    unescaped = html.unescape(details_text)
    stripped_text = unescaped.strip()
    if stripped_text.startswith("<"):
        try:
            ElementTree.fromstring(stripped_text)
        except ElementTree.ParseError:
            pass  # ordinary log text that happens to open with "<"
        else:
            raise EventLogError(
                "the event-log reply unescapes into an XML document, a shape "
                "no capture has shown; refusing to guess its schema")
    return _display_lines(unescaped)


def entries_from_lines(lines: list[str]) -> list[EventLogEntry]:
    """
    Normalize raw log lines to the EventLogEntry shape the agent already
    serves (models.Snapshot.event_log, /api/eventlog) -- ts, severity,
    message -- so wiring a driver on top of this is trivial.

    ts and severity are the NOT_DECODED markers, honestly: the line format is
    uncaptured, and a fabricated timestamp or an every-line-is-"info" severity
    would misinform (imagine a drive failure filed as info). The verbatim line
    is the message. File order is preserved; which end is newest is one more
    thing the capture will have to say.
    """
    return [EventLogEntry(ts=TIMESTAMP_NOT_DECODED,
                          severity=SEVERITY_NOT_DECODED,
                          message=line)
            for line in lines]


def get_event_log(host: str, esa_id: str, port: int = DEFAULT_PORT,
                  timeout: float = 8.0) -> list[EventLogEntry]:
    """
    Fetch the Drobo's user event log. Read-only.

    Same hand-rolled channel dance as droboapps.get_droboapps: connect, plain
    login, one command, one reply. Returns entries_from_lines() of the reply
    -- see both docstrings for what is and is not decoded.

    Raises EventLogError on refusal, teardown, or an unparseable reply. A
    torn-down connection right after the command is the failure the empty-
    params variant of 91 is known to cause, so it is named in the error.
    """
    if _commands.is_dangerous(COMMAND_NAME):
        raise EventLogError(f"{COMMAND_NAME} is on the do-not-send list")
    request_payload = _command_xml(esa_id)

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        frame_type, _ = _read_frame(sock)
        if frame_type != TYPE_LOGIN_OK:
            raise EventLogError(f"login refused (type 0x{frame_type:02x})")
        sock.sendall(_build_frame(TYPE_COMMAND, request_payload))
        try:
            frame_type, body = _read_frame(sock)
        except (ConfigError, OSError) as exc:
            raise EventLogError(
                f"connection lost while reading the event-log reply ({exc}); "
                f"this is the teardown eCmdGetUserEventLog is known for when "
                f"sent without its Path -- see docs/command-surface.md") from exc
        if frame_type != TYPE_RESULT:
            raise EventLogError(f"unexpected reply type 0x{frame_type:02x}")
        return entries_from_lines(parse_event_log(body))
    finally:
        sock.close()
