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

from drobo_nasd import commands as C
from drobo_nasd import eventlog
from drobo_nasd.config_cmd import ConfigError
from drobo_nasd.models import EventLogEntry
from drobo_nasd.sysinfo import SysInfoError

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def envelope(inner_text: str, escaped: bool = True) -> bytes:
    """A TMCmd result carrying `inner_text` in <ResultDetails>, escaped the
    way eCmdGetSysInfo's replies are, or raw for the unescaped variant."""
    body = html.escape(inner_text) if escaped else inner_text
    return (b"<TMCmd><CmdID>91</CmdID><Result>8589934592</Result>"
            b"<ResultDetails>" + body.encode() + b"</ResultDetails>"
            b"</TMCmd>\x00")


# Placeholder log content -- the parser must treat it as opaque, so these
# lines are arbitrary on purpose. The CRLF endings and the blank line are the
# point: a log FILE will have them, and they must not become entries.
LOG_TEXT = ("first line of the log\r\n"
            "second line, with an & ampersand\r\n"
            "\r\n"
            "  indented continuation line\r\n")

print("== escaped text in the standard envelope (the expected shape) ==")
got = eventlog.parse_event_log(envelope(LOG_TEXT))
check("three non-blank lines survive", len(got) == 3, got)
check("lines are verbatim and in file order",
      got[0] == "first line of the log"
      and got[2] == "  indented continuation line", got)
check("entities unescape on the way through",
      got[1] == "second line, with an & ampersand", got)
check("no CR is left on any line", all("\r" not in line for line in got), got)

print("\n== the same envelope, text unescaped ==")
plain = eventlog.parse_event_log(envelope("alpha\nbeta\n", escaped=False))
check("unescaped text parses too", plain == ["alpha", "beta"], plain)

print("\n== a bare body with no envelope at all ==")
bare = eventlog.parse_event_log(b"one\ntwo\n")
check("bare text splits into lines", bare == ["one", "two"], bare)
mojibake = eventlog.parse_event_log(b"good line\n\xff\xfe broken byte line\n")
check("an undecodable byte does not take the fetch down",
      len(mojibake) == 2 and mojibake[0] == "good line", mojibake)

print("\n== empty is an empty log, never an invented entry ==")
for payload, label in ((b"", "zero bytes"),
                       (b"\x00", "a lone NUL"),
                       (envelope(""), "an empty ResultDetails"),
                       (envelope("  \r\n \n"), "whitespace-only content"),
                       (b"<TMCmd><ResultDetails></ResultDetails></TMCmd>\x00",
                        "a childless, textless envelope")):
    check(f"{label} -> []", eventlog.parse_event_log(payload) == [], label)

print("\n== shapes no capture has shown are refused, not guessed ==")
STRUCTURED = (b"<TMCmd><ResultDetails><UserEventLog><Event>x</Event>"
              b"</UserEventLog></ResultDetails></TMCmd>\x00")
ESCAPED_DOC = envelope("<UserEventLog><Event>x</Event></UserEventLog>")
TRUNCATED = b"<TMCmd><ResultDetails>cut off mid-"
NO_DETAILS = b"<TMCmd><CmdID>91</CmdID></TMCmd>\x00"
for payload, label in ((STRUCTURED, "XML child elements"),
                       (ESCAPED_DOC, "escaped text that IS an XML document"),
                       (TRUNCATED, "an XML-looking body that does not parse"),
                       (NO_DETAILS, "an envelope with no ResultDetails")):
    try:
        eventlog.parse_event_log(payload)
        check(f"{label} raises", False)
    except eventlog.EventLogError:
        check(f"{label} raises EventLogError", True)

check("...but log text merely OPENING with '<' inside the envelope still parses",
      eventlog.parse_event_log(envelope("<system boot>\nnext line\n"))
      == ["<system boot>", "next line"])

print("\n== normalization matches the agent's entry shape exactly ==")
entries = eventlog.entries_from_lines(["a line", "another"])
check("every entry is a models.EventLogEntry",
      all(isinstance(e, EventLogEntry) for e in entries), entries)
check("the raw line rides in message, untouched",
      [e.message for e in entries] == ["a line", "another"], entries)
check("ts is the NOT_DECODED marker, not a fabricated time",
      all(e.ts == eventlog.TS_NOT_DECODED for e in entries), entries)
check("severity is the NOT_DECODED marker, not an assumed 'info'",
      all(e.severity == eventlog.SEVERITY_NOT_DECODED for e in entries), entries)
d = entries[0].to_dict()
check("to_dict serves the same keys /api/eventlog already serves",
      set(d) == {"ts", "severity", "message", "ts_iso"}, d)
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
      eventlog.COMMAND == "eCmdGetUserEventLog")
check("its confirmed id is 91", C.COMMANDS.get(eventlog.COMMAND) == 91,
      C.COMMANDS.get(eventlog.COMMAND))
check("it is not on the do-not-send list",
      not C.is_dangerous(eventlog.COMMAND))
check("the path constant is exactly the captured one",
      eventlog.UEL_PATH == "/uel.log", eventlog.UEL_PATH)
check("EventLogError is catchable as SysInfoError",
      issubclass(eventlog.EventLogError, SysInfoError))
check("...and as ConfigError (callers already handle that)",
      issubclass(eventlog.EventLogError, ConfigError))

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
