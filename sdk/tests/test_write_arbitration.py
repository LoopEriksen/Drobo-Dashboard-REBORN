"""
Tests for read-back arbitration: what a write means when the device never
answers it.

    py test_write_arbitration.py

WHAT THIS IS ABOUT
------------------
MEASURED 2026-08-25/26 on a real Drobo 5N: a share-create was sent, the device
CLOSED THE SOCKET without sending a result frame, and the share was created
anyway. Every layer above reported a failure. That false negative invites a
retry of a write that already applied -- the worst thing this SDK could do.

So config_cmd's write tail now decides an unanswered write by asking the device
what it holds, and the tests below pin every branch of that decision:

  frame sent, no answer, read-back shows the change   -> success, unacknowledged
  frame sent, no answer, read-back shows it absent    -> genuine failure
  frame sent, no answer, read-back unusable           -> outcome UNKNOWN
  connect/login failed before the frame went out      -> plain failure
  a real result frame arrives                         -> exactly as before

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

from drobo_nasd import commands
from drobo_nasd import config_cmd
from drobo_nasd import droboapps
from drobo_nasd import sharewrite
from drobo_nasd.shareedit import ShareEditError

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# HANGUP is the measured symptom: the device reads the command frame, then
# closes the connection without sending a result frame at all.
HANGUP = "hangup"

SET_CONFIG_ID = commands.COMMANDS["eCmdSetConfig"]


class ScriptedDrobo:
    """
    A fake device driven by a per-connection script. Each entry is either the
    reply bytes to send, or HANGUP: read the command and close, silently.

    When the script runs out the listening socket closes, so the NEXT
    connection fails -- which is how "the read-back is unreachable too" is
    staged without any special casing.
    """

    def __init__(self, script):
        self.script = list(script)
        self.sent = []  # command payloads received, in order
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(4)
        threading.Thread(target=self._run, daemon=True).start()

    def _frame(self, conn):
        head = conn.recv(16)
        if len(head) < 16:
            return None
        ln = struct.unpack(">I", head[12:16])[0]
        buf = b""
        while len(buf) < ln:
            chunk = conn.recv(ln - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def _run(self):
        for step in self.script:
            try:
                conn, _ = self.srv.accept()
                conn.settimeout(5)
                self._frame(conn)  # login
                conn.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
                payload = self._frame(conn)  # command
                self.sent.append((payload or b"").rstrip(b"\x00").decode("utf-8", "replace"))
                # The command frame is fully read either way -- the client's
                # sendall must complete before the socket goes away, or the
                # test would be measuring a SEND failure, not an ACK failure.
                if step is not HANGUP:
                    conn.sendall(
                        config_cmd._build_frame(config_cmd.TYPE_RESULT, step))
                conn.close()
            except OSError:
                break
        self.srv.close()

    def writes(self):
        """Just the eCmdSetConfig frames -- how a retry would show up."""
        return [s for s in self.sent if f"<CmdID>{SET_CONFIG_ID}</CmdID>" in s]


def dead_port():
    """A loopback port with nothing listening: connect() is refused."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- share fixtures, the same shapes test_sharelifecycle uses ---------------

def _pp_share(name, access="1"):
    return ("\n\t\t\t<Share>"
            "\n\t\t\t\t<ShareName>" + name + "</ShareName>"
            "\n\t\t\t\t<ShareState>0</ShareState>"
            "\n\t\t\t\t<TimeMachineEnabled>0</TimeMachineEnabled>"
            "\n\t\t\t\t<ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>"
            "\n\t\t\t\t<ShareUsers>"
            "\n\t\t\t\t\t<ShareUser>"
            "\n\t\t\t\t\t\t<ShareUsername>Everyone</ShareUsername>"
            "\n\t\t\t\t\t\t<ShareUserAccess>" + access + "</ShareUserAccess>"
            "\n\t\t\t\t\t</ShareUser>"
            "\n\t\t\t\t</ShareUsers>"
            "\n\t\t\t</Share>")


def _pp_config(shares_pp):
    return ("<DRIShareConfig>"
            "\n\t\t<ConfigVersionBasis>21</ConfigVersionBasis>"
            "\n\t\t<UserList/>"
            "\n\t\t<Shares>" + shares_pp + "\n\t\t</Shares>"
            "\n\t</DRIShareConfig>")


def _read_reply(shareconfig_xml):
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig>"
            f"{shareconfig_xml}</DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _accept():
    return (f"<TMCmd><CmdID>{SET_CONFIG_ID}</CmdID>"
            f"<Result>{config_cmd.RESULT_ACCEPTED}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


def _general_reply(feature="7", spindown="14", dim="59"):
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><General>"
            f"<FeatureStates>{feature}</FeatureStates>"
            f"<SpindownDelay>{spindown}</SpindownDelay><Dim>{dim}</Dim>"
            f"</General></DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _dimming_reply(value):
    return f"<TMCmd><ResultDetails>{value}</ResultDetails></TMCmd>".encode() + b"\x00"


ESA = "drb000000SAMPLE"
ORIG = _pp_config(_pp_share("Alpha") + _pp_share("Beta"))
WITH_FRESH = ORIG.replace(
    "</Shares>", sharewrite._NEW_SHARE_NODE.format(name="Fresh", volume="Volume1") + "</Shares>")
LOCKED = _pp_config(_pp_share("Locked", access="0"))
UNLOCKED = _pp_config(_pp_share("Locked", access="1"))

# ---------------------------------------------------------------------------
print("== config_cmd: where the line between 'not sent' and 'no answer' sits ==")

DOC = "<General><FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay><Dim>3</Dim></General>"


def _arbiter(answer):
    """A read-back that records every consultation and reports `answer`."""
    calls = []

    def readback():
        calls.append(answer)
        return answer
    return readback, calls


# A refused connection: the frame never left this process, so nothing can have
# happened and the arbiter must not even be asked.
ask, calls = _arbiter(True)
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID,
                                      dead_port(), 2, readback=ask)
    check("a refused connection raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a refused connection is a plain ConfigWriteError, not outcome-unknown",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError), type(exc).__name__)
    check("...with the pre-existing 'could not reach' wording",
          "could not reach the command port" in str(exc), str(exc))
check("...and the read-back arbiter was never consulted", calls == [], calls)

# A refused LOGIN is also before the frame goes out.
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.bind(("127.0.0.1", 0))
lport = srv.getsockname()[1]
srv.listen(1)


def _refuse_login():
    conn, _ = srv.accept()
    conn.settimeout(3)
    head = conn.recv(16)
    ln = struct.unpack(">I", head[12:16])[0]
    conn.recv(ln)
    conn.sendall(config_cmd._build_frame(0x99, b""))   # not TYPE_LOGIN_OK
    conn.close()
    srv.close()


threading.Thread(target=_refuse_login, daemon=True).start()
ask, calls = _arbiter(True)
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID, lport, 3, readback=ask)
    check("a refused login raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a refused login is a plain failure, not outcome-unknown",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError)
          and "login refused" in str(exc), str(exc))
check("...and the arbiter was never consulted for a refused login", calls == [], calls)

print("\n== config_cmd: the acknowledged path is untouched ==")

dev = ScriptedDrobo([_accept()])
ask, calls = _arbiter(False)   # would fail the write if it were ever consulted
res = config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                        SET_CONFIG_ID, dev.port, 5,
                                        readback=ask)
check("an accepted write still returns the device's result code",
      res["result_code"] == config_cmd.RESULT_ACCEPTED, res)
check("...and reports itself acknowledged", res["acknowledged"] is True, res)
check("...and does NOT read the device back", calls == [], calls)
check("...and carries no 'verified' of its own", "verified" not in res, res)

# A device that answers with a non-accept code still fails, on its own word.
dev = ScriptedDrobo([(f"<TMCmd><Result>0</Result><ResultDetails/></TMCmd>").encode() + b"\x00"])
ask, calls = _arbiter(True)   # would rescue the write if it were ever consulted
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID, dev.port, 5, readback=ask)
    check("a non-accept Result still raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a non-accept Result still raises ConfigWriteError",
          "did not accept" in str(exc), str(exc))
check("...and an explicit refusal is never overridden by a read-back",
      calls == [], calls)

print("\n== config_cmd: sent, then silence -- the read-back decides ==")

dev = ScriptedDrobo([HANGUP])
ask, calls = _arbiter(True)
res = config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                        SET_CONFIG_ID, dev.port, 5,
                                        readback=ask)
check("an unanswered write whose change IS present succeeds",
      res["verified"] is True, res)
check("...and is flagged unacknowledged", res["acknowledged"] is False, res)
check("...with no result code, because the device never gave one",
      res["result_code"] is None, res)
check("...and says so in the note",
      "never acknowledged" in res["note"] and "read-back" in res["note"], res)
check("...and the arbiter was consulted exactly once", calls == [True], calls)
check("...and the write frame went out exactly once (no retry)",
      len(dev.writes()) == 1, dev.sent)

dev = ScriptedDrobo([HANGUP])
ask, calls = _arbiter(False)
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID, dev.port, 5, readback=ask)
    check("an unanswered write whose change is ABSENT raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unanswered write whose change is ABSENT is a genuine failure",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError)
          and "did not apply" in str(exc), str(exc))
check("...and still only one write frame went out", len(dev.writes()) == 1, dev.sent)

print("\n== config_cmd: sent, silence, and no usable read-back -> UNKNOWN ==")


def _exploding():
    raise config_cmd.ConfigError("the read-back connection died too")


dev = ScriptedDrobo([HANGUP])
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID,
                                      dev.port, 5, readback=_exploding)
    check("an unarbitrable write raises", False)
except config_cmd.ConfigWriteOutcomeUnknownError as exc:
    msg = str(exc)
    check("an unarbitrable write raises ConfigWriteOutcomeUnknownError", True)
    check("...the wording says the frame WAS sent", "WAS sent" in msg, msg)
    check("...the wording says it MAY have applied", "MAY have applied" in msg, msg)
    check("...the wording tells the caller to re-read first",
          "Re-read the configuration" in msg, msg)
    check("...the wording forbids a blind retry", "do NOT simply retry" in msg, msg)
    check("...and nothing in it implies nothing happened",
          "did not apply" not in msg and "Nothing is confirmed changed" not in msg, msg)

# With no arbiter at all the outcome is equally unknown -- never a failure.
dev = ScriptedDrobo([HANGUP])
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID, dev.port, 5)
    check("an unanswered write with no arbiter raises", False)
except config_cmd.ConfigWriteOutcomeUnknownError as exc:
    check("an unanswered write with no arbiter is outcome-unknown, not failure",
          "MAY have applied" in str(exc), str(exc))

# An arbiter that fails in an unexpected way still yields UNKNOWN, never a
# claim that the write failed.
dev = ScriptedDrobo([HANGUP])


def _buggy():
    raise TypeError("an arbiter with a bug in it")


try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID,
                                      dev.port, 5, readback=_buggy)
    check("a broken arbiter raises", False)
except config_cmd.ConfigWriteOutcomeUnknownError as exc:
    check("a broken arbiter yields UNKNOWN, not a false failure",
          "TypeError" in str(exc) and "MAY have applied" in str(exc), str(exc))

check("ConfigWriteOutcomeUnknownError is still a ConfigWriteError (broad handlers catch it)",
      issubclass(config_cmd.ConfigWriteOutcomeUnknownError,
                 config_cmd.ConfigWriteError))

print("\n== create_share: the write the hardware actually did this to ==")

# Sent, hung up on, and the share IS there -- the measured 2026-08-25/26 case.
dev = ScriptedDrobo([_read_reply(ORIG), HANGUP, _read_reply(WITH_FRESH)])
res = sharewrite.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1",
                              port=dev.port, timeout=5)
check("an unacknowledged create that applied is reported as SUCCESS",
      res.get("verified") is True, res)
check("...and surfaces that the device never acknowledged it",
      res.get("acknowledged") is False, res)
check("...and explains why in a note", "never acknowledged" in res.get("note", ""), res)
check("...having sent the create exactly once", len(dev.writes()) == 1, dev.sent)
check("...and having read back exactly once, not twice",
      len(dev.sent) == 3, dev.sent)

# Sent, hung up on, and the share is NOT there -- a real failure.
dev = ScriptedDrobo([_read_reply(ORIG), HANGUP, _read_reply(ORIG)])
try:
    sharewrite.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1",
                            port=dev.port, timeout=5)
    check("an unacknowledged create that did NOT apply raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged create that did NOT apply is a genuine failure",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError)
          and "did not apply" in str(exc), str(exc))
check("...and the create was never re-sent", len(dev.writes()) == 1, dev.sent)

# Sent, hung up on, and the read-back is unreachable -- genuinely unknown.
dev = ScriptedDrobo([_read_reply(ORIG), HANGUP])
try:
    sharewrite.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1",
                            port=dev.port, timeout=3)
    check("an unarbitrable create raises", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    msg = str(exc)
    check("an unarbitrable create raises ShareWriteOutcomeUnknownError", True)
    check("...saying the write WAS sent", "WAS sent" in msg, msg)
    check("...saying the share MAY now exist", "MAY now exist" in msg, msg)
    check("...telling the caller to re-read first",
          "Re-read the share config" in msg, msg)
    check("...and forbidding a blind retry", "do NOT simply retry" in msg, msg)
except ShareEditError as exc:
    check("an unarbitrable create raises ShareWriteOutcomeUnknownError "
          "(got plain ShareEditError)", False, str(exc))
check("...and the create was sent once and never repeated",
      len(dev.writes()) == 1, dev.sent)

print("\n== delete_share: the destructive write gets the same treatment ==")

dev = ScriptedDrobo([_read_reply(WITH_FRESH), HANGUP, _read_reply(ORIG)])
res = sharewrite.delete_share("127.0.0.1", ESA, "Fresh", port=dev.port,
                              timeout=5)
check("an unacknowledged delete that applied is reported as SUCCESS",
      res.get("verified") is True and res.get("acknowledged") is False, res)
check("...having sent the delete exactly once", len(dev.writes()) == 1, dev.sent)

dev = ScriptedDrobo([_read_reply(WITH_FRESH), HANGUP, _read_reply(WITH_FRESH)])
try:
    sharewrite.delete_share("127.0.0.1", ESA, "Fresh", port=dev.port,
                            timeout=5)
    check("an unacknowledged delete that did NOT apply raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged delete that did NOT apply is a genuine failure",
          "did not apply" in str(exc), str(exc))

dev = ScriptedDrobo([_read_reply(WITH_FRESH), HANGUP])
try:
    sharewrite.delete_share("127.0.0.1", ESA, "Fresh", port=dev.port,
                            timeout=3)
    check("an unarbitrable delete raises", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    msg = str(exc)
    check("an unarbitrable delete raises ShareWriteOutcomeUnknownError", True)
    check("...saying the share MAY already be gone", "MAY already be gone" in msg, msg)
    check("...and forbidding a blind retry of a destructive write",
          "do NOT simply retry" in msg, msg)

print("\n== set_share_user_access: the same, on the permission write ==")

dev = ScriptedDrobo([_read_reply(LOCKED), HANGUP, _read_reply(UNLOCKED)])
res = sharewrite.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone",
                                       "1", port=dev.port, timeout=5)
check("an unacknowledged permission write that applied is reported as SUCCESS",
      res.get("verified") is True and res.get("acknowledged") is False, res)

dev = ScriptedDrobo([_read_reply(LOCKED), HANGUP, _read_reply(LOCKED)])
try:
    sharewrite.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone",
                                     "1", port=dev.port, timeout=5)
    check("an unacknowledged permission write that did NOT apply raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged permission write that did NOT apply is a failure",
          "did not apply" in str(exc), str(exc))

dev = ScriptedDrobo([_read_reply(LOCKED), HANGUP])
try:
    sharewrite.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone",
                                     "1", port=dev.port, timeout=3)
    check("an unarbitrable permission write raises", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    check("an unarbitrable permission write raises ShareWriteOutcomeUnknownError",
          "WAS sent" in str(exc) and "MAY now be changed" in str(exc), str(exc))

print("\n== every config write benefits, not just the share ones ==")

# Brightness rides the same tail, so it gets the same arbitration.
dev = ScriptedDrobo([_general_reply(dim="59"), HANGUP, _dimming_reply(10)])
res = droboapps.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
check("an unacknowledged brightness write that applied is reported as SUCCESS",
      res.get("verified") is True and res.get("acknowledged") is False, res)
check("...having sent the write exactly once", len(dev.writes()) == 1, dev.sent)

dev = ScriptedDrobo([_general_reply(dim="59"), HANGUP, _dimming_reply(59)])
try:
    droboapps.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("an unacknowledged brightness write that did NOT apply raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unacknowledged brightness write that did NOT apply is a failure",
          "did not apply" in str(exc), str(exc))

# Spin-down too.
dev = ScriptedDrobo([_general_reply(feature="7", spindown="14"), HANGUP,
                     _general_reply(feature="7", spindown="30")])
res = droboapps.set_spindown("127.0.0.1", ESA, 30, port=dev.port, timeout=5)
check("an unacknowledged spin-down write that applied is reported as SUCCESS",
      res.get("verified") is True and res.get("acknowledged") is False, res)

dev = ScriptedDrobo([_general_reply(feature="7", spindown="14"), HANGUP,
                     _general_reply(feature="5", spindown="0")])
res = droboapps.disable_spindown("127.0.0.1", ESA, port=dev.port, timeout=5)
check("an unacknowledged spin-down DISABLE that applied is reported as SUCCESS",
      res.get("verified") is True and res.get("acknowledged") is False, res)

print("\n== accepted-but-unapplied is untouched: still 'Received, not applied' ==")

dev = ScriptedDrobo([_read_reply(ORIG), _accept(), _read_reply(ORIG)])
try:
    sharewrite.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1",
                            port=dev.port, timeout=5)
    check("an accepted create the read-back does not show still raises", False)
except ShareEditError as exc:
    check("an accepted create the read-back does not show still raises",
          "Received, not applied" in str(exc), str(exc))

dev = ScriptedDrobo([_read_reply(LOCKED), _accept(), _read_reply(LOCKED)])
try:
    sharewrite.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone",
                                     "1", port=dev.port, timeout=5)
    check("an accepted permission write the read-back does not show still raises", False)
except ShareEditError as exc:
    check("an accepted permission write the read-back does not show still raises",
          "Received, not applied" in str(exc), str(exc))

print("\n== the acknowledged share path is unchanged, and says so ==")

dev = ScriptedDrobo([_read_reply(ORIG), _accept(), _read_reply(WITH_FRESH)])
res = sharewrite.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1",
                              port=dev.port, timeout=5)
check("a normally acknowledged create verifies as before",
      res.get("verified") is True, res)
check("...and reports acknowledged True", res.get("acknowledged") is True, res)
check("...and still carries the device's result code",
      res.get("result_code") == config_cmd.RESULT_ACCEPTED, res)
sent = html.unescape(dev.sent[1])
check("...and the document on the wire is still the read plus the new node",
      "<ShareName>Fresh</ShareName>" in sent and "<ShareName>Alpha</ShareName>" in sent,
      sent)

# The no-op permission write still sends nothing, and says nothing was
# acknowledged because nothing was sent.
dev = ScriptedDrobo([_read_reply(UNLOCKED)])
res = sharewrite.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone",
                                       "1", port=dev.port, timeout=5)
check("a no-op permission write still writes nothing",
      res.get("unchanged") is True and len(dev.writes()) == 0, res)
check("...and reports acknowledged None (there was nothing to acknowledge)",
      res.get("acknowledged") is None, res)

print("\n== a reply we cannot read is noise, not a verdict ==")


def _garbled_reply(port_holder):
    """Accept the login, take the command frame in full, then answer with a
    frame type that is not TYPE_RESULT."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port_holder.append(srv.getsockname()[1])
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.settimeout(5)
        for _ in range(2):                      # login, then the command
            head = conn.recv(16)
            if len(head) < 16:
                break
            ln = struct.unpack(">I", head[12:16])[0]
            buf = b""
            while len(buf) < ln:
                chunk = conn.recv(ln - len(buf))
                if not chunk:
                    break
                buf += chunk
            if _ == 0:
                conn.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
        # not TYPE_RESULT
        conn.sendall(config_cmd._build_frame(0x77, b"who knows"))
        conn.close()
        srv.close()
    threading.Thread(target=serve, daemon=True).start()


# The device answered with SOMETHING unreadable. The command frame is already
# on the wire, so this says no more about the outcome than silence does: the
# read-back has to settle it.
holder = []
_garbled_reply(holder)
ask, calls = _arbiter(True)
res = config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                        SET_CONFIG_ID, holder[0], 3,
                                        readback=ask)
check("an unreadable reply consults the read-back", calls == [True], calls)
check("...and a read-back that finds the change reports success",
      res.get("verified") is True, res)
check("...marked as never acknowledged by the device",
      res.get("acknowledged") is False, res)
check("...and carries no result code, because none was readable",
      res.get("result_code") is None, res)

# Same unreadable reply, but the change is genuinely absent: that IS a failure,
# and it must not be dressed up as merely unconfirmed.
holder = []
_garbled_reply(holder)
ask, calls = _arbiter(False)
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID,
                                      holder[0], 3, readback=ask)
    check("an unreadable reply with the change absent raises", False)
except config_cmd.ConfigWriteError as exc:
    check("an unreadable reply with the change absent is a plain failure",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError), type(exc).__name__)
    check("...and the read-back was what decided it", calls == [False], calls)

# A stated refusal is still a verdict: the device read the command and said no,
# so a read-back may not overrule it.
srv2 = ScriptedDrobo([(f"<TMCmd><CmdID>{SET_CONFIG_ID}</CmdID><Result>5</Result>"
                       f"<ResultDetails/></TMCmd>").encode() + b"\x00"])
ask, calls = _arbiter(True)
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID,
                                      srv2.port, 3, readback=ask)
    check("a stated refusal raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a stated refusal stays a refusal", "did not accept" in str(exc), str(exc))
check("...and the read-back was never consulted for it", calls == [], calls)

print("\n== a failure before the frame goes out is typed as a write failure ==")

# The layers above read an untyped ConfigError as "this may have applied".
# Everything above THE LINE must therefore be a ConfigWriteError, or a login
# that never sent the command would be reported as possibly-applied.
srv3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv3.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv3.bind(("127.0.0.1", 0))
hport = srv3.getsockname()[1]
srv3.listen(1)


def _hangup_at_login():
    conn, _ = srv3.accept()
    conn.recv(16)
    conn.close()          # gone before the login is answered
    srv3.close()


threading.Thread(target=_hangup_at_login, daemon=True).start()
ask, calls = _arbiter(True)
try:
    config_cmd._send_section_document("127.0.0.1", ESA, "general", DOC,
                                      SET_CONFIG_ID, hport, 3, readback=ask)
    check("a login that dies raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a login that dies is a ConfigWriteError, not a bare ConfigError",
          True, type(exc).__name__)
    check("...and not outcome-unknown, because nothing was sent",
          not isinstance(exc, config_cmd.ConfigWriteOutcomeUnknownError), type(exc).__name__)
except config_cmd.ConfigError as exc:
    check("a login that dies is a ConfigWriteError, not a bare ConfigError",
          False, type(exc).__name__)
check("...and the read-back was never consulted", calls == [], calls)

print("\n== nothing in the write path can ever re-send ==")

# Structural, not behavioural: there is exactly ONE place in config_cmd that
# puts a command frame on the wire for a write. A second one -- a retry, a
# fallback connection -- is the failure mode this whole change exists to
# prevent, and it would show up here before it ever reached hardware.
_SOURCE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "drobo_nasd", "config_cmd.py"),
               encoding="utf-8").read()
check("config_cmd sends a command frame from exactly three places "
      "(two reads, one write)",
      _SOURCE.count("sendall(_build_frame(TYPE_COMMAND") == 3,
      _SOURCE.count("sendall(_build_frame(TYPE_COMMAND"))
check("...and _send_section_document -- the write -- holds exactly one "
      "of them",
      _SOURCE.split("def _send_section_document")[1]
      .split("def _arbitrate_unacknowledged")[0]
      .count("sendall(_build_frame(TYPE_COMMAND") == 1)
check("...and the arbiter itself never sends anything",
      "sendall" not in _SOURCE.split("def _arbitrate_unacknowledged")[1]
      .split("def get_config_element")[0])

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
