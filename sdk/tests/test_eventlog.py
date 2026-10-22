"""
Tests for eCmdGetUserEventLog -- the Drobo's own event history.

WHAT THESE FIXTURES ARE, HONESTLY: no reply to command 91 has ever been
captured -- the 2026-08-05/06 captures recorded only the REQUEST
(<Params><Path>/uel.log</Path></Params>, docs/protocol-map.md). So the reply
fixtures below are built to the channel's confirmed ENVELOPE conventions (the
TMCmd / ResultDetails shapes measured for eCmdGetSysInfo and friends), with
opaque text standing in for log content. What they prove is the contract the
module promises while the format is unknown: lines come back verbatim, nothing
is invented, and any structured shape we have never seen is refused rather
than guessed at. The one CAPTURED byte-for-byte assertion here is the request.

    py test_eventlog.py
"""
import html
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import eventlog  # noqa: E402
from drobo_nasd.config_cmd import ConfigError  # noqa: E402
from drobo_nasd.models import EventLogEntry  # noqa: E402
from drobo_nasd.sysinfo import SysInfoError  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


def result_envelope(inner_text: str, escaped: bool = True) -> bytes:
    """A TMCmd result carrying `inner_text` in <ResultDetails>, escaped the
    way eCmdGetSysInfo's replies are, or raw for the unescaped variant."""
    details_text = html.escape(inner_text) if escaped else inner_text
    return (b"<TMCmd><CmdID>91</CmdID><Result>8589934592</Result>"
            b"<ResultDetails>" + details_text.encode() + b"</ResultDetails>"
            b"</TMCmd>\x00")


# Placeholder log content -- the parser must treat it as opaque, so these
# lines are arbitrary on purpose. The CRLF endings and the blank line are the
# point: a log FILE will have them, and they must not become entries.
LOG_TEXT = ("first line of the log\r\n"
            "second line, with an & ampersand\r\n"
            "\r\n"
            "  indented continuation line\r\n")

print("== escaped text in the standard envelope (the expected shape) ==")
log_lines = eventlog.parse_event_log(result_envelope(LOG_TEXT))
check("three non-blank lines survive", len(log_lines) == 3, log_lines)
check("lines are verbatim and in file order",
      log_lines[0] == "first line of the log"
      and log_lines[2] == "  indented continuation line", log_lines)
check("entities unescape on the way through",
      log_lines[1] == "second line, with an & ampersand", log_lines)
check("no CR is left on any line",
      all("\r" not in line for line in log_lines), log_lines)

print("\n== the same envelope, text unescaped ==")
unescaped_lines = eventlog.parse_event_log(
    result_envelope("alpha\nbeta\n", escaped=False))
check("unescaped text parses too", unescaped_lines == ["alpha", "beta"],
      unescaped_lines)

print("\n== a bare body with no envelope at all ==")
bare_lines = eventlog.parse_event_log(b"one\ntwo\n")
check("bare text splits into lines", bare_lines == ["one", "two"], bare_lines)
mojibake = eventlog.parse_event_log(b"good line\n\xff\xfe broken byte line\n")
check("an undecodable byte does not take the fetch down",
      len(mojibake) == 2 and mojibake[0] == "good line", mojibake)

print("\n== empty is an empty log, never an invented entry ==")
for payload, label in ((b"", "zero bytes"),
                       (b"\x00", "a lone NUL"),
                       (result_envelope(""), "an empty ResultDetails"),
                       (result_envelope("  \r\n \n"),
                        "whitespace-only content"),
                       (b"<TMCmd><ResultDetails></ResultDetails></TMCmd>\x00",
                        "a childless, textless envelope")):
    check(f"{label} -> []", eventlog.parse_event_log(payload) == [], label)

print("\n== shapes no capture has shown are refused, not guessed ==")
STRUCTURED_REPLY = (b"<TMCmd><ResultDetails><UserEventLog><Event>x</Event>"
                    b"</UserEventLog></ResultDetails></TMCmd>\x00")
ESCAPED_DOCUMENT_REPLY = result_envelope(
    "<UserEventLog><Event>x</Event></UserEventLog>")
TRUNCATED_REPLY = b"<TMCmd><ResultDetails>cut off mid-"
NO_DETAILS_REPLY = b"<TMCmd><CmdID>91</CmdID></TMCmd>\x00"
for payload, label in ((STRUCTURED_REPLY, "XML child elements"),
                       (ESCAPED_DOCUMENT_REPLY,
                        "escaped text that IS an XML document"),
                       (TRUNCATED_REPLY,
                        "an XML-looking body that does not parse"),
                       (NO_DETAILS_REPLY,
                        "an envelope with no ResultDetails")):
    try:
        eventlog.parse_event_log(payload)
        check(f"{label} raises", False)
    except eventlog.EventLogError:
        check(f"{label} raises EventLogError", True)

check("...but log text merely OPENING with '<' inside the envelope still parses",
      eventlog.parse_event_log(result_envelope("<system boot>\nnext line\n"))
      == ["<system boot>", "next line"])

print("\n== normalization matches the agent's entry shape exactly ==")
entries = eventlog.entries_from_lines(["a line", "another"])
check("every entry is a models.EventLogEntry",
      all(isinstance(entry, EventLogEntry) for entry in entries), entries)
check("the raw line rides in message, untouched",
      [entry.message for entry in entries] == ["a line", "another"], entries)
check("ts is the NOT_DECODED marker, not a fabricated time",
      all(entry.ts == eventlog.TIMESTAMP_NOT_DECODED for entry in entries),
      entries)
check("severity is the NOT_DECODED marker, not an assumed 'info'",
      all(entry.severity == eventlog.SEVERITY_NOT_DECODED for entry in entries), entries)
entry_dict = entries[0].to_dict()
check("to_dict serves the same keys /api/eventlog already serves",
      set(entry_dict) == {"ts", "severity", "message", "ts_iso"}, entry_dict)
check("an empty parse normalizes to an empty list",
      eventlog.entries_from_lines([]) == [])

print("\n== the request is the captured one, byte for byte ==")
check("command 91 with the /uel.log path, CmdID/Params/ESAID order",
      eventlog._command_xml("DRA123456789")
      == (b"<TMCmd><CmdID>91</CmdID>"
          b"<Params><Path>/uel.log</Path></Params>"
          b"<ESAID>DRA123456789</ESAID></TMCmd>\x00"),
      eventlog._command_xml("DRA123456789"))

print("\n== safety ==")
check("we send eCmdGetUserEventLog, not the eCmdGetEventLogs that answers empty",
      eventlog.COMMAND_NAME == "eCmdGetUserEventLog")
check("its confirmed id is 91",
      commands.COMMANDS.get(eventlog.COMMAND_NAME) == 91,
      commands.COMMANDS.get(eventlog.COMMAND_NAME))
check("it is not on the do-not-send list",
      not commands.is_dangerous(eventlog.COMMAND_NAME))
check("the path constant is exactly the captured one",
      eventlog.USER_EVENT_LOG_PATH == "/uel.log", eventlog.USER_EVENT_LOG_PATH)
check("EventLogError is catchable as SysInfoError",
      issubclass(eventlog.EventLogError, SysInfoError))
check("...and as ConfigError (callers already handle that)",
      issubclass(eventlog.EventLogError, ConfigError))

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
