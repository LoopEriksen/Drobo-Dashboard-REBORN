"""
Tests for read-back arbitration: what a write means when the device
never answers it.

    py test_write_arbitration.py

WHAT THIS IS ABOUT
------------------
MEASURED 2026-08-25/26 on a real Drobo 5N: a share-create was sent, the
device CLOSED THE SOCKET without sending a result frame, and the share
was created anyway. Every layer above reported a failure. That false
negative invites a retry of a write that already applied -- the worst
thing this SDK could do.

So config_cmd's write tail now decides an unanswered write by asking the
device what it holds, and the tests below pin every branch of that
decision:

  frame sent, no answer, read-back shows the change
      -> success, unacknowledged
  frame sent, no answer, read-back shows it absent
      -> genuine failure
  frame sent, no answer, read-back unusable
      -> outcome UNKNOWN
  connect/login failed before the frame went out
      -> plain failure
  a real result frame arrives
      -> exactly as before

Everything runs against fake devices on loopback. No hardware.
"""

import html
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import droboapps  # noqa: E402
from drobo_nasd import sharewrite  # noqa: E402
from drobo_nasd.shareedit import ShareEditError  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# HANGUP is the measured symptom: the device reads the command frame,
# then closes the connection without sending a result frame at all.
HANGUP = "hangup"

SET_CONFIG_ID = commands.COMMANDS["eCmdSetConfig"]


class ScriptedDrobo:
    """
    A fake device driven by a per-connection script. Each entry is
    either the reply bytes to send, or HANGUP: read the command and
    close, silently.

    When the script runs out the listening socket closes, so the NEXT
    connection fails -- which is how "the read-back is unreachable too"
    is staged without any special casing.
    """

    def __init__(self, script):
        self.script = list(script)
        self.sent = []  # command payloads received, in order
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET,
                                      socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.port = self.server_socket.getsockname()[1]
        self.server_socket.listen(4)
        threading.Thread(target=self._serve_script, daemon=True).start()

    def _receive_frame(self, connection):
        header = connection.recv(16)
        if len(header) < 16:
            return None
        payload_length = struct.unpack(">I", header[12:16])[0]
        payload = b""
        while len(payload) < payload_length:
            chunk = connection.recv(payload_length - len(payload))
            if not chunk:
                break
            payload += chunk
        return payload

    def _serve_script(self):
        for step in self.script:
            try:
                connection, _ = self.server_socket.accept()
                connection.settimeout(5)
                self._receive_frame(connection)  # login
                connection.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
                payload = self._receive_frame(connection)  # command
                self.sent.append(
                    (payload or b"").rstrip(b"\x00").decode("utf-8", "replace")
                )
                # The command frame is fully read either way -- the
                # client's sendall must complete before the socket goes
                # away, or the test would be measuring a SEND failure,
                # not an ACK failure.
                if step is not HANGUP:
                    connection.sendall(
                        config_cmd._build_frame(config_cmd.TYPE_RESULT, step))
                connection.close()
            except OSError:
                break
        self.server_socket.close()

    def set_config_writes(self):
        """Just the eCmdSetConfig frames -- how a retry would show up.
        """
        return [s for s in self.sent if f"<CmdID>{SET_CONFIG_ID}</CmdID>" in s]


def refused_port():
    """A loopback port with nothing listening: connect() is refused."""
    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_socket.bind(("127.0.0.1", 0))
    port = probe_socket.getsockname()[1]
    probe_socket.close()
    return port


# --- share fixtures, the same shapes test_sharelifecycle uses
# ---------------

def _pretty_printed_share(name, access_code="1"):
    return ("\n\t\t\t<Share>"
            "\n\t\t\t\t<ShareName>" + name + "</ShareName>"
            "\n\t\t\t\t<ShareState>0</ShareState>"
            "\n\t\t\t\t<TimeMachineEnabled>0</TimeMachineEnabled>"
            "\n\t\t\t\t<ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>"
            "\n\t\t\t\t<ShareUsers>"
            "\n\t\t\t\t\t<ShareUser>"
            "\n\t\t\t\t\t\t<ShareUsername>Everyone</ShareUsername>"
            "\n\t\t\t\t\t\t<ShareUserAccess>" + access_code
            + "</ShareUserAccess>"
            "\n\t\t\t\t\t</ShareUser>"
            "\n\t\t\t\t</ShareUsers>"
            "\n\t\t\t</Share>")


def _pretty_printed_share_config(pretty_printed_shares_xml):
    return ("<DRIShareConfig>"
            "\n\t\t<ConfigVersionBasis>21</ConfigVersionBasis>"
            "\n\t\t<UserList/>"
            "\n\t\t<Shares>" + pretty_printed_shares_xml + "\n\t\t</Shares>"
            "\n\t</DRIShareConfig>")


def _read_reply(share_config_xml):
    return (
        f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig>"
        f"{share_config_xml}</DRINASConfig></ResultDetails></TMCmd>"
    ).encode() + b"\x00"


def _accepted_reply():
    return (f"<TMCmd><CmdID>{SET_CONFIG_ID}</CmdID>"
            f"<Result>{config_cmd.RESULT_ACCEPTED}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


def _general_reply(feature_states="7", spindown_delay="14", dim_level="59"):
    return (
        f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><General>"
        f"<FeatureStates>{feature_states}</FeatureStates>"
        f"<SpindownDelay>{spindown_delay}</SpindownDelay>"
        f"<Dim>{dim_level}</Dim>"
        f"</General></DRINASConfig></ResultDetails></TMCmd>"
    ).encode() + b"\x00"


def _dimming_reply(value):
    return (
        f"<TMCmd><ResultDetails>{value}</ResultDetails></TMCmd>".encode()
        + b"\x00"
    )


SAMPLE_ESA_ID = "drb000000SAMPLE"
ORIGINAL_SHARE_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Alpha") + _pretty_printed_share("Beta"))
CONFIG_WITH_FRESH_SHARE = ORIGINAL_SHARE_CONFIG.replace(
    "</Shares>",
    sharewrite._NEW_SHARE_NODE.format(name="Fresh", volume="Volume1")
    + "</Shares>",
)
LOCKED_SHARE_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Locked", access_code="0"))
UNLOCKED_SHARE_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Locked", access_code="1"))

# ---------------------------------------------------------------------------
print(
    "== config_cmd: where the line between 'not sent' and 'no answer' sits =="
)

GENERAL_DOCUMENT = (
    "<General><FeatureStates>7</FeatureStates>"
    "<SpindownDelay>14</SpindownDelay><Dim>3</Dim></General>"
)


def _recording_readback(readback_answer):
    """A read-back that records every consultation and reports
    `readback_answer`."""
    readback_calls = []

    def readback():
        readback_calls.append(readback_answer)
        return readback_answer
    return readback, readback_calls


# A refused connection: the frame never left this process, so nothing
# can have happened and the arbiter must not even be asked.
readback, readback_calls = _recording_readback(True)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT, SET_CONFIG_ID,
                                      refused_port(), 2, readback=readback)
    check("a refused connection raises", False)
except config_cmd.ConfigWriteError as exc:
    check(
        "a refused connection is a plain ConfigWriteError, "
        "not outcome-unknown",
        not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError),
        type(exc).__name__,
    )
    check("...with the pre-existing 'could not reach' wording",
          "could not reach the command port" in str(exc), str(exc))
check("...and the read-back arbiter was never consulted", readback_calls == [],
      readback_calls)

# A refused LOGIN is also before the frame goes out.
login_refusing_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
login_refusing_server.bind(("127.0.0.1", 0))
login_refusing_port = login_refusing_server.getsockname()[1]
login_refusing_server.listen(1)


def _refuse_login():
    connection, _ = login_refusing_server.accept()
    connection.settimeout(3)
    header = connection.recv(16)
    payload_length = struct.unpack(">I", header[12:16])[0]
    connection.recv(payload_length)
    connection.sendall(
        config_cmd._build_frame(0x99, b""))   # not TYPE_LOGIN_OK
    connection.close()
    login_refusing_server.close()


threading.Thread(target=_refuse_login, daemon=True).start()
readback, readback_calls = _recording_readback(True)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT,
                                      SET_CONFIG_ID, login_refusing_port, 3,
                                      readback=readback)
    check("a refused login raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a refused login is a plain failure, not outcome-unknown",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError)
          and "login refused" in str(exc), str(exc))
check(
    "...and the arbiter was never consulted for a refused login",
    readback_calls == [],
    readback_calls,
)

print("\n== config_cmd: the acknowledged path is untouched ==")

scripted_drobo = ScriptedDrobo([_accepted_reply()])
# would fail the write if it were ever consulted
readback, readback_calls = _recording_readback(False)
result = config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID,
                                           "general", GENERAL_DOCUMENT,
                                           SET_CONFIG_ID,
                                           scripted_drobo.port, 5,
                                           readback=readback)
check("an accepted write still returns the device's result code",
      result["result_code"] == config_cmd.RESULT_ACCEPTED, result)
check("...and reports itself acknowledged",
      result["acknowledged"] is True, result)
check("...and does NOT read the device back", readback_calls == [],
      readback_calls)
check("...and carries no 'verified' of its own",
      "verified" not in result, result)

# A device that answers with a non-accept code still fails, on its own
# word.
scripted_drobo = ScriptedDrobo(
    [("<TMCmd><Result>0</Result><ResultDetails/></TMCmd>").encode() + b"\x00"]
)
# would rescue the write if it were ever consulted
readback, readback_calls = _recording_readback(True)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT,
                                      SET_CONFIG_ID, scripted_drobo.port, 5,
                                      readback=readback)
    check("a non-accept Result still raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a non-accept Result still raises ConfigWriteError",
          "did not accept" in str(exc), str(exc))
check("...and an explicit refusal is never overridden by a read-back",
      readback_calls == [], readback_calls)

print("\n== config_cmd: sent, then silence -- the read-back decides ==")

scripted_drobo = ScriptedDrobo([HANGUP])
readback, readback_calls = _recording_readback(True)
result = config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID,
                                           "general", GENERAL_DOCUMENT,
                                           SET_CONFIG_ID,
                                           scripted_drobo.port, 5,
                                           readback=readback)
check("an unanswered write whose change IS present succeeds",
      result["verified"] is True, result)
check("...and is flagged unacknowledged",
      result["acknowledged"] is False, result)
check("...with no result code, because the device never gave one",
      result["result_code"] is None, result)
check("...and says so in the note",
      "never acknowledged" in result["note"]
      and "read-back" in result["note"], result)
check("...and the arbiter was consulted exactly once",
      readback_calls == [True], readback_calls)
check("...and the write frame went out exactly once (no retry)",
      len(scripted_drobo.set_config_writes()) == 1, scripted_drobo.sent)

scripted_drobo = ScriptedDrobo([HANGUP])
readback, readback_calls = _recording_readback(False)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT,
                                      SET_CONFIG_ID, scripted_drobo.port, 5,
                                      readback=readback)
    check("an unanswered write whose change is ABSENT raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unanswered write whose change is ABSENT is a genuine failure",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError)
          and "did not apply" in str(exc), str(exc))
check(
    "...and still only one write frame went out",
    len(scripted_drobo.set_config_writes()) == 1,
    scripted_drobo.sent,
)

print("\n== config_cmd: sent, silence, and no usable read-back -> UNKNOWN ==")


def _failing_readback():
    raise config_cmd.ConfigError("the read-back connection died too")


scripted_drobo = ScriptedDrobo([HANGUP])
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT, SET_CONFIG_ID,
                                      scripted_drobo.port, 5,
                                      readback=_failing_readback)
    check("an unarbitrable write raises", False)
except config_cmd.ConfigWriteOutcomeUnknownError as exc:
    message = str(exc)
    check("an unarbitrable write raises ConfigWriteOutcomeUnknownError", True)
    check("...the wording says the frame WAS sent",
          "WAS sent" in message, message)
    check(
        "...the wording says it MAY have applied",
        "MAY have applied" in message,
        message,
    )
    check("...the wording tells the caller to re-read first",
          "Re-read the configuration" in message, message)
    check(
        "...the wording forbids a blind retry",
        "do NOT simply retry" in message,
        message,
    )
    check(
        "...and nothing in it implies nothing happened",
        "did not apply" not in message
        and "Nothing is confirmed changed" not in message,
        message,
    )

# With no arbiter at all the outcome is equally unknown -- never a
# failure.
scripted_drobo = ScriptedDrobo([HANGUP])
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT,
                                      SET_CONFIG_ID, scripted_drobo.port, 5)
    check("an unanswered write with no arbiter raises", False)
except config_cmd.ConfigWriteOutcomeUnknownError as exc:
    check(
        "an unanswered write with no arbiter is outcome-unknown, not failure",
        "MAY have applied" in str(exc),
        str(exc),
    )

# An arbiter that fails in an unexpected way still yields UNKNOWN, never
# a claim that the write failed.
scripted_drobo = ScriptedDrobo([HANGUP])


def _buggy_readback():
    raise TypeError("an arbiter with a bug in it")


try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT, SET_CONFIG_ID,
                                      scripted_drobo.port, 5,
                                      readback=_buggy_readback)
    check("a broken arbiter raises", False)
except config_cmd.ConfigWriteOutcomeUnknownError as exc:
    check("a broken arbiter yields UNKNOWN, not a false failure",
          "TypeError" in str(exc) and "MAY have applied" in str(exc), str(exc))

check(
    "ConfigWriteOutcomeUnknownError is still a ConfigWriteError "
    "(broad handlers catch it)",
    issubclass(
        config_cmd.ConfigWriteOutcomeUnknownError, config_cmd.ConfigWriteError
    ),
)

print("\n== create_share: the write the hardware actually did this to ==")

# Sent, hung up on, and the share IS there -- the measured 2026-08-25/26
# case.
scripted_drobo = ScriptedDrobo(
    [_read_reply(ORIGINAL_SHARE_CONFIG), HANGUP,
     _read_reply(CONFIG_WITH_FRESH_SHARE)])
result = sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                                 volume="Volume1",
                                 port=scripted_drobo.port, timeout=5)
check("an unacknowledged create that applied is reported as SUCCESS",
      result.get("verified") is True, result)
check("...and surfaces that the device never acknowledged it",
      result.get("acknowledged") is False, result)
check(
    "...and explains why in a note",
    "never acknowledged" in result.get("note", ""),
    result,
)
check(
    "...having sent the create exactly once",
    len(scripted_drobo.set_config_writes()) == 1,
    scripted_drobo.sent,
)
check("...and having read back exactly once, not twice",
      len(scripted_drobo.sent) == 3, scripted_drobo.sent)

# Sent, hung up on, and the share is NOT there -- a real failure.
scripted_drobo = ScriptedDrobo(
    [_read_reply(ORIGINAL_SHARE_CONFIG), HANGUP,
     _read_reply(ORIGINAL_SHARE_CONFIG)])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            volume="Volume1",
                            port=scripted_drobo.port, timeout=5)
    check("an unacknowledged create that did NOT apply raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged create that did NOT apply is a genuine failure",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError)
          and "did not apply" in str(exc), str(exc))
check("...and the create was never re-sent",
      len(scripted_drobo.set_config_writes()) == 1, scripted_drobo.sent)

# Sent, hung up on, and the read-back is unreachable -- genuinely
# unknown.
scripted_drobo = ScriptedDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), HANGUP])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            volume="Volume1",
                            port=scripted_drobo.port, timeout=3)
    check("an unarbitrable create raises", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    message = str(exc)
    check("an unarbitrable create raises ShareWriteOutcomeUnknownError", True)
    check("...saying the write WAS sent", "WAS sent" in message, message)
    check("...saying the share MAY now exist",
          "MAY now exist" in message, message)
    check("...telling the caller to re-read first",
          "Re-read the share config" in message, message)
    check("...and forbidding a blind retry",
          "do NOT simply retry" in message, message)
except ShareEditError as exc:
    check("an unarbitrable create raises ShareWriteOutcomeUnknownError "
          "(got plain ShareEditError)", False, str(exc))
check("...and the create was sent once and never repeated",
      len(scripted_drobo.set_config_writes()) == 1, scripted_drobo.sent)

print("\n== delete_share: the destructive write gets the same treatment ==")

scripted_drobo = ScriptedDrobo(
    [_read_reply(CONFIG_WITH_FRESH_SHARE), HANGUP,
     _read_reply(ORIGINAL_SHARE_CONFIG)])
result = sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                                 port=scripted_drobo.port,
                                 timeout=5)
check("an unacknowledged delete that applied is reported as SUCCESS",
      result.get("verified") is True
      and result.get("acknowledged") is False, result)
check(
    "...having sent the delete exactly once",
    len(scripted_drobo.set_config_writes()) == 1,
    scripted_drobo.sent,
)

scripted_drobo = ScriptedDrobo(
    [_read_reply(CONFIG_WITH_FRESH_SHARE), HANGUP,
     _read_reply(CONFIG_WITH_FRESH_SHARE)])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            port=scripted_drobo.port,
                            timeout=5)
    check("an unacknowledged delete that did NOT apply raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged delete that did NOT apply is a genuine failure",
          "did not apply" in str(exc), str(exc))

scripted_drobo = ScriptedDrobo([_read_reply(CONFIG_WITH_FRESH_SHARE), HANGUP])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            port=scripted_drobo.port,
                            timeout=3)
    check("an unarbitrable delete raises", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    message = str(exc)
    check("an unarbitrable delete raises ShareWriteOutcomeUnknownError", True)
    check(
        "...saying the share MAY already be gone",
        "MAY already be gone" in message,
        message,
    )
    check("...and forbidding a blind retry of a destructive write",
          "do NOT simply retry" in message, message)

print("\n== set_share_user_access: the same, on the permission write ==")

scripted_drobo = ScriptedDrobo(
    [_read_reply(LOCKED_SHARE_CONFIG), HANGUP,
     _read_reply(UNLOCKED_SHARE_CONFIG)])
result = sharewrite.set_share_user_access("127.0.0.1", SAMPLE_ESA_ID, "Locked",
                                          "Everyone", "1",
                                          port=scripted_drobo.port, timeout=5)
check("an unacknowledged permission write that applied is reported as SUCCESS",
      result.get("verified") is True
      and result.get("acknowledged") is False, result)

scripted_drobo = ScriptedDrobo(
    [_read_reply(LOCKED_SHARE_CONFIG), HANGUP,
     _read_reply(LOCKED_SHARE_CONFIG)])
try:
    sharewrite.set_share_user_access("127.0.0.1", SAMPLE_ESA_ID, "Locked",
                                     "Everyone",
                                     "1", port=scripted_drobo.port, timeout=5)
    check(
        "an unacknowledged permission write that did NOT apply raises", False
    )
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged permission write that did NOT apply is a failure",
          "did not apply" in str(exc), str(exc))

scripted_drobo = ScriptedDrobo([_read_reply(LOCKED_SHARE_CONFIG), HANGUP])
try:
    sharewrite.set_share_user_access("127.0.0.1", SAMPLE_ESA_ID, "Locked",
                                     "Everyone",
                                     "1", port=scripted_drobo.port, timeout=3)
    check("an unarbitrable permission write raises", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    check(
        "an unarbitrable permission write raises "
        "ShareWriteOutcomeUnknownError",
        "WAS sent" in str(exc) and "MAY now be changed" in str(exc),
        str(exc),
    )

print("\n== every config write benefits, not just the share ones ==")

# Brightness rides the same tail, so it gets the same arbitration.
scripted_drobo = ScriptedDrobo(
    [_general_reply(dim_level="59"), HANGUP, _dimming_reply(10)])
result = droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                               port=scripted_drobo.port, timeout=5)
check("an unacknowledged brightness write that applied is reported as SUCCESS",
      result.get("verified") is True
      and result.get("acknowledged") is False, result)
check(
    "...having sent the write exactly once",
    len(scripted_drobo.set_config_writes()) == 1,
    scripted_drobo.sent,
)

scripted_drobo = ScriptedDrobo(
    [_general_reply(dim_level="59"), HANGUP, _dimming_reply(59)])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=scripted_drobo.port, timeout=5)
    check(
        "an unacknowledged brightness write that did NOT apply raises", False
    )
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged brightness write that did NOT apply is a failure",
          "did not apply" in str(exc), str(exc))

# Spin-down too.
scripted_drobo = ScriptedDrobo(
    [_general_reply(feature_states="7", spindown_delay="14"), HANGUP,
     _general_reply(feature_states="7", spindown_delay="30")])
result = droboapps.set_spindown("127.0.0.1", SAMPLE_ESA_ID, 30,
                                port=scripted_drobo.port, timeout=5)
check("an unacknowledged spin-down write that applied is reported as SUCCESS",
      result.get("verified") is True
      and result.get("acknowledged") is False, result)

scripted_drobo = ScriptedDrobo(
    [_general_reply(feature_states="7", spindown_delay="14"), HANGUP,
     _general_reply(feature_states="5", spindown_delay="0")])
result = droboapps.disable_spindown("127.0.0.1", SAMPLE_ESA_ID,
                                    port=scripted_drobo.port, timeout=5)
check(
    "an unacknowledged spin-down DISABLE that applied is reported as SUCCESS",
    result.get("verified") is True and result.get("acknowledged") is False,
    result,
)

print(
    "\n== accepted-but-unapplied is untouched: "
    "still 'Received, not applied' =="
)

scripted_drobo = ScriptedDrobo(
    [_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply(),
     _read_reply(ORIGINAL_SHARE_CONFIG)])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            volume="Volume1",
                            port=scripted_drobo.port, timeout=5)
    check("an accepted create the read-back does not show still raises", False)
except ShareEditError as exc:
    check("an accepted create the read-back does not show still raises",
          "Received, not applied" in str(exc), str(exc))

scripted_drobo = ScriptedDrobo(
    [_read_reply(LOCKED_SHARE_CONFIG), _accepted_reply(),
     _read_reply(LOCKED_SHARE_CONFIG)])
try:
    sharewrite.set_share_user_access("127.0.0.1", SAMPLE_ESA_ID, "Locked",
                                     "Everyone",
                                     "1", port=scripted_drobo.port, timeout=5)
    check(
        "an accepted permission write the read-back does not show "
        "still raises",
        False,
    )
except ShareEditError as exc:
    check(
        "an accepted permission write the read-back does not show "
        "still raises",
        "Received, not applied" in str(exc),
        str(exc),
    )

print("\n== the acknowledged share path is unchanged, and says so ==")

scripted_drobo = ScriptedDrobo(
    [_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply(),
     _read_reply(CONFIG_WITH_FRESH_SHARE)])
result = sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                                 volume="Volume1",
                                 port=scripted_drobo.port, timeout=5)
check("a normally acknowledged create verifies as before",
      result.get("verified") is True, result)
check("...and reports acknowledged True",
      result.get("acknowledged") is True, result)
check("...and still carries the device's result code",
      result.get("result_code") == config_cmd.RESULT_ACCEPTED, result)
written_document = html.unescape(scripted_drobo.sent[1])
check(
    "...and the document on the wire is still the read plus the new node",
    "<ShareName>Fresh</ShareName>" in written_document
    and "<ShareName>Alpha</ShareName>" in written_document,
    written_document,
)

# The no-op permission write still sends nothing, and says nothing was
# acknowledged because nothing was sent.
scripted_drobo = ScriptedDrobo([_read_reply(UNLOCKED_SHARE_CONFIG)])
result = sharewrite.set_share_user_access("127.0.0.1", SAMPLE_ESA_ID, "Locked",
                                          "Everyone", "1",
                                          port=scripted_drobo.port, timeout=5)
check("a no-op permission write still writes nothing",
      result.get("unchanged") is True
      and len(scripted_drobo.set_config_writes()) == 0, result)
check("...and reports acknowledged None (there was nothing to acknowledge)",
      result.get("acknowledged") is None, result)

print("\n== a reply we cannot read is noise, not a verdict ==")


def _serve_garbled_reply(port_holder):
    """Accept the login, take the command frame in full, then answer
    with a frame type that is not TYPE_RESULT."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port_holder.append(server_socket.getsockname()[1])
    server_socket.listen(1)

    def serve():
        connection, _ = server_socket.accept()
        connection.settimeout(5)
        for frame_index in range(2):  # login, then the command
            header = connection.recv(16)
            if len(header) < 16:
                break
            payload_length = struct.unpack(">I", header[12:16])[0]
            payload = b""
            while len(payload) < payload_length:
                chunk = connection.recv(payload_length - len(payload))
                if not chunk:
                    break
                payload += chunk
            if frame_index == 0:
                connection.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
        # not TYPE_RESULT
        connection.sendall(config_cmd._build_frame(0x77, b"who knows"))
        connection.close()
        server_socket.close()
    threading.Thread(target=serve, daemon=True).start()


# The device answered with SOMETHING unreadable. The command frame is
# already on the wire, so this says no more about the outcome than
# silence does: the read-back has to settle it.
garbled_port_holder = []
_serve_garbled_reply(garbled_port_holder)
readback, readback_calls = _recording_readback(True)
result = config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID,
                                           "general", GENERAL_DOCUMENT,
                                           SET_CONFIG_ID,
                                           garbled_port_holder[0], 3,
                                           readback=readback)
check("an unreadable reply consults the read-back", readback_calls == [True],
      readback_calls)
check("...and a read-back that finds the change reports success",
      result.get("verified") is True, result)
check("...marked as never acknowledged by the device",
      result.get("acknowledged") is False, result)
check("...and carries no result code, because none was readable",
      result.get("result_code") is None, result)

# Same unreadable reply, but the change is genuinely absent: that IS a
# failure, and it must not be dressed up as merely unconfirmed.
garbled_port_holder = []
_serve_garbled_reply(garbled_port_holder)
readback, readback_calls = _recording_readback(False)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT, SET_CONFIG_ID,
                                      garbled_port_holder[0], 3,
                                      readback=readback)
    check("an unreadable reply with the change absent raises", False)
except config_cmd.ConfigWriteError as exc:
    check(
        "an unreadable reply with the change absent is a plain failure",
        not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError),
        type(exc).__name__,
    )
    check("...and the read-back was what decided it",
          readback_calls == [False], readback_calls)

# A stated refusal is still a verdict: the device read the command and
# said no, so a read-back may not overrule it.
refusing_drobo = ScriptedDrobo(
    [
        (
            f"<TMCmd><CmdID>{SET_CONFIG_ID}</CmdID><Result>5</Result>"
            f"<ResultDetails/></TMCmd>"
        ).encode()
        + b"\x00"
    ]
)
readback, readback_calls = _recording_readback(True)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT, SET_CONFIG_ID,
                                      refusing_drobo.port, 3,
                                      readback=readback)
    check("a stated refusal raises", False)
except config_cmd.ConfigWriteError as exc:
    check(
        "a stated refusal stays a refusal",
        "did not accept" in str(exc),
        str(exc),
    )
check("...and the read-back was never consulted for it", readback_calls == [],
      readback_calls)

print(
    "\n== a failure before the frame goes out is typed as a write failure =="
)

# The layers above read an untyped ConfigError as "this may have
# applied". Everything above THE LINE must therefore be a
# ConfigWriteError, or a login that never sent the command would be
# reported as possibly-applied.
login_hangup_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
login_hangup_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
login_hangup_server.bind(("127.0.0.1", 0))
login_hangup_port = login_hangup_server.getsockname()[1]
login_hangup_server.listen(1)


def _hangup_at_login():
    connection, _ = login_hangup_server.accept()
    connection.recv(16)
    connection.close()          # gone before the login is answered
    login_hangup_server.close()


threading.Thread(target=_hangup_at_login, daemon=True).start()
readback, readback_calls = _recording_readback(True)
try:
    config_cmd._send_section_document("127.0.0.1", SAMPLE_ESA_ID, "general",
                                      GENERAL_DOCUMENT,
                                      SET_CONFIG_ID, login_hangup_port, 3,
                                      readback=readback)
    check("a login that dies raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a login that dies is a ConfigWriteError, not a bare ConfigError",
          True, type(exc).__name__)
    check(
        "...and not outcome-unknown, because nothing was sent",
        not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError),
        type(exc).__name__,
    )
except config_cmd.ConfigError as exc:
    check("a login that dies is a ConfigWriteError, not a bare ConfigError",
          False, type(exc).__name__)
check("...and the read-back was never consulted", readback_calls == [],
      readback_calls)

print("\n== nothing in the write path can ever re-send ==")

# Structural, not behavioural: there is exactly ONE place in config_cmd
# that puts a command frame on the wire for a write. A second one -- a
# retry, a fallback connection -- is the failure mode this whole change
# exists to prevent, and it would show up here before it ever reached
# hardware.
_CONFIG_CMD_SOURCE = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "drobo_nasd", "config_cmd.py"),
    encoding="utf-8").read()
check("config_cmd sends a command frame from exactly three places "
      "(two reads, one write)",
      _CONFIG_CMD_SOURCE.count("sendall(_build_frame(TYPE_COMMAND") == 3,
      _CONFIG_CMD_SOURCE.count("sendall(_build_frame(TYPE_COMMAND"))
check("...and _send_section_document -- the write -- holds exactly one "
      "of them",
      _CONFIG_CMD_SOURCE.split("def _send_section_document")[1]
      .split("def _arbitrate_unacknowledged")[0]
      .count("sendall(_build_frame(TYPE_COMMAND") == 1)
check("...and the arbiter itself never sends anything",
      "sendall" not in _CONFIG_CMD_SOURCE
      .split("def _arbitrate_unacknowledged")[1]
      .split("def get_config_element")[0])

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
