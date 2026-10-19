"""
Tests for sharewrite -- the send half of share-permission edits, and the
highest-stakes write in the SDK.

    py test_sharewrite.py

The whole point of the surgical design is that changing ONE user's access on ONE
share leaves every other byte the device's own: every other share, every other
user, ConfigVersionBasis, UserList. So the tests assert exactly that -- not just
that the target changed, but that nothing else did -- because a share silently
dropped from the write would be a share gone from a device that may hold the
only copy of somebody's data.

Runs against a fake device on loopback. No hardware.
"""
import html
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import config_cmd as cc
from drobo_nasd import sharewrite as sw
from drobo_nasd.shareedit import ShareEditError

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def _share(name, access, user="Everyone"):
    return (f"<Share><ShareName>{name}</ShareName><ShareState>0</ShareState>"
            f"<TimeMachineEnabled>0</TimeMachineEnabled>"
            f"<ShareUsers><ShareUser><ShareUsername>{user}</ShareUsername>"
            f"<ShareUserAccess>{access}</ShareUserAccess></ShareUser></ShareUsers>"
            f"</Share>")


def _shareconfig(shares_xml, basis="15"):
    """A DRIShareConfig with ConfigVersionBasis and an empty UserList, the exact
    shape the capture confirmed."""
    return (f"<DRIShareConfig><ConfigVersionBasis>{basis}</ConfigVersionBasis>"
            f"<UserList /><Shares>{shares_xml}</Shares></DRIShareConfig>")


def _read_reply(shareconfig_xml):
    """A read reply in the shape the real 5N sends: DRIShareConfig nested under a
    <DRINASConfig> wrapper inside ResultDetails (measured 2026-08-10 -- the
    wrapper is what get_config_element must dig through)."""
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig>{shareconfig_xml}"
            f"</DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _accept():
    return (f"<TMCmd><CmdID>31</CmdID><Result>{cc.RESULT_ACCEPTED}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


class FakeDrobo:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
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
            c = conn.recv(ln - len(buf))
            if not c:
                break
            buf += c
        return buf

    def _run(self):
        for reply in self.replies:
            try:
                conn, _ = self.srv.accept()
                conn.settimeout(5)
                self._frame(conn)  # login
                conn.sendall(cc._frame(cc.TYPE_LOGIN_OK, b""))
                payload = self._frame(conn)  # command
                self.sent.append((payload or b"").rstrip(b"\x00").decode("utf-8", "replace"))
                conn.sendall(cc._frame(cc.TYPE_RESULT, reply))
                conn.close()
            except OSError:
                break
        self.srv.close()


ESA = "drb000000SAMPLE"
TWO = _shareconfig(_share("Locked", "0") + _share("Other", "0"))

# ---------------------------------------------------------------------------
print("== unlock a share: set Everyone from 0 to 1, touch nothing else ==")

# read-element, write, verify read-back (Locked now 1)
after = _shareconfig(_share("Locked", "1") + _share("Other", "0"))
dev = FakeDrobo([_read_reply(TWO), _accept(), _read_reply(after)])
res = sw.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone", "1", port=dev.port, timeout=5)
check("the write is accepted and verified", res.get("verified") is True, res)

sent = html.unescape(dev.sent[1])
check("read, write, then verify -- three connections", len(dev.sent) == 3, len(dev.sent))
check("Locked's Everyone is now access 1",
      "<ShareName>Locked</ShareName>" in sent and "<ShareUserAccess>1</ShareUserAccess>" in sent, sent)
check("ConfigVersionBasis is preserved exactly",
      "<ConfigVersionBasis>15</ConfigVersionBasis>" in sent, sent)
check("the empty UserList is preserved", "UserList" in sent, sent)
check("the OTHER share is still present and unchanged",
      "<ShareName>Other</ShareName>" in sent, sent)
check("Other's access is still 0 (not touched)",
      sent.count("<ShareUserAccess>0</ShareUserAccess>") == 1, sent)
check("exactly one access code became 1",
      sent.count("<ShareUserAccess>1</ShareUserAccess>") == 1, sent)

print("\n== the guards: a disallowed code never reaches the device ==")

# Code 2 is unconfirmed and not on the device -> refuse, send nothing.
dev = FakeDrobo([_read_reply(TWO), _accept(), _read_reply(TWO)])
try:
    sw.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone", "2", port=dev.port, timeout=5)
    check("an unconfirmed code is refused", False)
except ShareEditError as exc:
    check("an unconfirmed code is refused", "neither confirmed" in str(exc), str(exc))
check("...and only the read happened, no write", len(dev.sent) == 1, dev.sent)

# An unknown share -> refuse after the read, before any write.
dev = FakeDrobo([_read_reply(TWO), _accept(), _read_reply(TWO)])
try:
    sw.set_share_user_access("127.0.0.1", ESA, "Nope", "Everyone", "1", port=dev.port, timeout=5)
    check("an unknown share is refused", False)
except ShareEditError as exc:
    check("an unknown share is refused", "no share named" in str(exc), str(exc))
check("...and nothing was written", len(dev.sent) == 1, dev.sent)

# Bad inputs are refused before any network at all.
check("an empty share name is refused before connecting",
      _raises(lambda: sw.set_share_user_access("127.0.0.1", ESA, "", "Everyone", "1",
                                               port=1, timeout=1)))
check("a non-numeric code is refused before connecting",
      _raises(lambda: sw.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone", "x",
                                               port=1, timeout=1)))

print("\n== a code already on the device is allowed even if unconfirmed ==")
# If some share already carries code 2, copying it to another is allowed --
# it is a real code on this device, not an invented one.
HAS_TWO = _shareconfig(_share("A", "2") + _share("B", "0"))
after2 = _shareconfig(_share("A", "2") + _share("B", "2"))
dev = FakeDrobo([_read_reply(HAS_TWO), _accept(), _read_reply(after2)])
res = sw.set_share_user_access("127.0.0.1", ESA, "B", "Everyone", "2", port=dev.port, timeout=5)
check("copying an existing on-device code is allowed", res.get("verified") is True, res)

print("\n== revoke sets 0, and reports a device that did not apply it ==")
after0 = _shareconfig(_share("Locked", "0") + _share("Other", "0"))
dev = FakeDrobo([_read_reply(_shareconfig(_share("Locked", "1") + _share("Other", "0"))),
                 _accept(), _read_reply(after0)])
res = sw.revoke_share_user("127.0.0.1", ESA, "Locked", "Everyone", port=dev.port, timeout=5)
check("revoke sets access 0", "<ShareUserAccess>0</ShareUserAccess>" in html.unescape(dev.sent[1]))

# Device accepts but read-back still shows the old value -> raise.
dev = FakeDrobo([_read_reply(TWO), _accept(), _read_reply(TWO)])  # verify still shows 0
try:
    sw.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone", "1", port=dev.port, timeout=5)
    check("accepted-but-unapplied raises", False)
except ShareEditError as exc:
    check("accepted-but-unapplied raises", "not applied" in str(exc), str(exc))

print("\n== duplicate/case-colliding user rows are refused, not half-changed ==")
# A share whose ShareUsers holds TWO entries folding to the same name. Changing
# the first and leaving the second is how a revoke silently half-succeeds --
# so this must refuse before any write, and send nothing.
DUP = ("<Share><ShareName>Photos</ShareName><ShareState>0</ShareState>"
       "<TimeMachineEnabled>0</TimeMachineEnabled><ShareUsers>"
       "<ShareUser><ShareUsername>Everyone</ShareUsername>"
       "<ShareUserAccess>1</ShareUserAccess></ShareUser>"
       "<ShareUser><ShareUsername>everyone</ShareUsername>"
       "<ShareUserAccess>1</ShareUserAccess></ShareUser>"
       "</ShareUsers></Share>")
dev = FakeDrobo([_read_reply(_shareconfig(DUP)), _accept(), _read_reply(_shareconfig(DUP))])
try:
    sw.revoke_share_user("127.0.0.1", ESA, "Photos", "Everyone", port=dev.port, timeout=5)
    check("a revoke on colliding rows is refused", False)
except ShareEditError as exc:
    check("a revoke on colliding rows is refused", "folding to" in str(exc), str(exc))
check("...and NOTHING was written -- no half-revoke on the wire",
      len(dev.sent) == 1, dev.sent)

# Verify requires EVERY matching row to equal the target: a device that reads
# back one Everyone at 0 and a residual one at 1 must NOT report success.
DUP_MIXED = ("<Share><ShareName>Photos</ShareName><ShareUsers>"
             "<ShareUser><ShareUsername>Everyone</ShareUsername>"
             "<ShareUserAccess>0</ShareUserAccess></ShareUser>"
             "<ShareUser><ShareUsername>Everyone</ShareUsername>"
             "<ShareUserAccess>1</ShareUserAccess></ShareUser>"
             "</ShareUsers></Share>")
dev = FakeDrobo([_read_reply(_shareconfig(DUP_MIXED))])  # one read for the verify
check("read-back with a residual grant fails verification",
      sw._reads_back_as("127.0.0.1", ESA, "Photos", "Everyone", "0",
                        dev.port, 5) is False)

print("\n== share names differing only by case: exact wins, else refuse (audit HIGH) ==")
# 'Photos' and 'photos' both exist. Requesting 'photos' must hit 'photos'
# exactly -- never the first case-insensitive match. Verified by asserting the
# write changed the SECOND share, not the first.
CASE = _shareconfig(_share("Photos", "0") + _share("photos", "0"))
after_case = _shareconfig(_share("Photos", "0") + _share("photos", "1"))
dev = FakeDrobo([_read_reply(CASE), _accept(), _read_reply(after_case)])
res = sw.set_share_user_access("127.0.0.1", ESA, "photos", "Everyone", "1", port=dev.port, timeout=5)
sent = html.unescape(dev.sent[1])
check("an exact-case match is chosen over an earlier fold",
      "<ShareName>Photos</ShareName><ShareState>0</ShareState><TimeMachineEnabled>0</TimeMachineEnabled>"
      "<ShareUsers><ShareUser><ShareUsername>Everyone</ShareUsername><ShareUserAccess>0</ShareUserAccess>" in sent
      and sent.count("<ShareUserAccess>1</ShareUserAccess>") == 1, sent)
check("...and it verifies against the right share", res.get("verified") is True, res)

# Requesting 'PHOTOS' -- no exact match, two folds -> refuse, send nothing.
dev = FakeDrobo([_read_reply(CASE), _accept(), _read_reply(CASE)])
try:
    sw.set_share_user_access("127.0.0.1", ESA, "PHOTOS", "Everyone", "1", port=dev.port, timeout=5)
    check("an ambiguous fold with no exact match is refused", False)
except ShareEditError as exc:
    check("an ambiguous fold with no exact match is refused", "Ambiguous" in str(exc), str(exc))
check("...and nothing was written", len(dev.sent) == 1, dev.sent)

# Verify must not confirm the wrong share either: read-back where 'Photos'=1
# but the requested 'photos'=0 must be False.
dev = FakeDrobo([_read_reply(_shareconfig(_share("Photos", "1") + _share("photos", "0")))])
check("read-back resolves the exact share, not the first fold",
      sw._reads_back_as("127.0.0.1", ESA, "photos", "Everyone", "1", dev.port, 5) is False)

print("\n== a no-op write is skipped, not sent ==")
# Row already holds the target code -> nothing on the wire, reported as unchanged.
dev = FakeDrobo([_read_reply(_shareconfig(_share("Locked", "1")))])
res = sw.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone", "1", port=dev.port, timeout=5)
check("already-set access sends nothing (only the read happened)", len(dev.sent) == 1, dev.sent)
check("...and reports unchanged + verified", res.get("unchanged") is True and res.get("verified") is True, res)

print("\n== malformed rows are refused, not half-edited ==")
TWO_ACCESS = ("<Share><ShareName>Odd</ShareName><ShareUsers><ShareUser>"
              "<ShareUsername>Everyone</ShareUsername>"
              "<ShareUserAccess>0</ShareUserAccess><ShareUserAccess>1</ShareUserAccess>"
              "</ShareUser></ShareUsers></Share>")
dev = FakeDrobo([_read_reply(_shareconfig(TWO_ACCESS)), _accept()])
try:
    sw.set_share_user_access("127.0.0.1", ESA, "Odd", "Everyone", "1", port=dev.port, timeout=5)
    check("a user row with two <ShareUserAccess> is refused", False)
except ShareEditError as exc:
    check("a user row with two <ShareUserAccess> is refused", "unambiguously" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

print("\n== an accepted write whose verify read-back fails is named as ACCEPTED-unverified ==")
# Read OK, write accepted, then the verify connection dies -> must NOT look like
# a rejected write. Only two replies queued: the third connection is refused.
dev = FakeDrobo([_read_reply(TWO), _accept()])
try:
    sw.set_share_user_access("127.0.0.1", ESA, "Locked", "Everyone", "1", port=dev.port, timeout=3)
    check("verify transport failure raises ShareWriteOutcomeUnknownError", False)
except sw.ShareWriteOutcomeUnknownError as exc:
    check("verify transport failure raises ShareWriteOutcomeUnknownError", "ACCEPTED" in str(exc), str(exc))
    check("...and warns not to blindly retry", "do NOT simply retry" in str(exc))
except ShareEditError as exc:
    check("verify transport failure raises ShareWriteOutcomeUnknownError (got plain ShareEditError)", False, str(exc))
check("...it is still a ShareEditError subclass so broad handlers catch it",
      issubclass(sw.ShareWriteOutcomeUnknownError, ShareEditError))

print("\n== a truncated reply is truncation, not a rejection ==")
# A reply whose declared length exceeds what arrives must raise 'short payload',
# not be parsed as a body lacking <Result> (which read as 'did not accept').
import struct as _st
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM); srv.bind(("127.0.0.1", 0)); srv.listen(1)
tport = srv.getsockname()[1]
def _truncating():
    conn, _ = srv.accept(); conn.settimeout(3)
    head = conn.recv(16); ln = _st.unpack(">I", head[12:16])[0]; conn.recv(ln)   # login
    conn.sendall(cc._frame(cc.TYPE_LOGIN_OK, b""))
    head = conn.recv(16); ln = _st.unpack(">I", head[12:16])[0]; conn.recv(ln)   # command
    full = _accept()
    frame = cc._frame(cc.TYPE_RESULT, full)
    conn.sendall(frame[:16 + len(full) // 2])   # header promises full, body cut short
    conn.close(); srv.close()
threading.Thread(target=_truncating, daemon=True).start()
try:
    cc.get_config("127.0.0.1", ESA, "shares", port=tport, timeout=3)
    check("truncated payload raises", False)
except cc.ConfigError as exc:
    check("truncated payload is reported as 'short payload'", "short payload" in str(exc), str(exc))

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
