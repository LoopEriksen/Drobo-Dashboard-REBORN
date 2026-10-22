"""
Tests for the eCmdGetConfig command path -- the first thing this project SENDS
to a Drobo.

Runs entirely offline against a fake device, so it passes on a machine with no
hardware. The fixtures are shaped exactly like the real responses observed on a
live 5N, including the credential fields the device really does hand out.

The redaction tests are the important ones. Asking a Drobo for its admin config
makes it return <Password>, <EncryptedPassword> and <ValidPassword> in the
clear, to anyone who completes a login -- and the login is just the device
serial, which the Drobo gives to any unauthenticated caller. We cannot fix the
device. We can refuse to become a second copy of the leak, and these tests are
what keep that true.

    py test_config_cmd.py
"""
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import config_cmd
from drobo_nasd import commands
from drobo_nasd import esatm

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------
# Fixtures shaped like the real device's replies
# --------------------------------------------------------------------------
NETWORK = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>30</CmdID><Result>0</Result><ResultDetails><DRINASConfig>
<DRINasNetworkConfig><Version>11</Version><NasName>TestDrobo</NasName>
<NasWorkgroup>WORKGROUP</NasWorkgroup>
<IPConfig><IPConfigType>0</IPConfigType><IP>10.0.0.5</IP>
<Subnet>255.255.255.0</Subnet><Gateway>10.0.0.1</Gateway></IPConfig>
</DRINasNetworkConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

ADMIN = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><DRINasAdminConfig>
<UserName>admin</UserName><Password>hunter2</Password>
<ValidPassword>1</ValidPassword>
<EncryptedPassword>QUJDREVGRw==</EncryptedPassword>
</DRINasAdminConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

SHARES = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><DRIShareConfig>
<Shares>
<Share><ShareName>Alpha</ShareName><ShareState>0</ShareState>
<ShareUsers><ShareUser><ShareUsername>Everyone</ShareUsername>
<SharePassword>leakme</SharePassword></ShareUser></ShareUsers></Share>
<Share><ShareName>Beta</ShareName><ShareState>0</ShareState></Share>
</Shares></DRIShareConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

SECRETS = ("hunter2", "QUJDREVGRw==", "leakme")

print("== parsing, and the redaction that matters ==")

net = config_cmd.parse_config_result(NETWORK)
ncfg = net["DRINASConfig"]["DRINasNetworkConfig"]
check("network parses", ncfg["NasName"] == "TestDrobo", ncfg.get("NasName"))
check("nested IPConfig parses", ncfg["IPConfig"]["IP"] == "10.0.0.5", ncfg.get("IPConfig"))

adm = (config_cmd.parse_config_result(ADMIN)
       ["DRINASConfig"]["DRINasAdminConfig"])
check("admin username kept (not a secret)", adm["UserName"] == "admin", adm.get("UserName"))
check("Password redacted", adm["Password"] == "[redacted]", adm.get("Password"))
check("ValidPassword redacted", adm["ValidPassword"] == "[redacted]", adm.get("ValidPassword"))
check("EncryptedPassword redacted",
      adm["EncryptedPassword"] == "[redacted]", adm.get("EncryptedPassword"))
check("no admin secret survives anywhere in the result",
      not any(s in repr(adm) for s in SECRETS), adm)

sh = config_cmd.parse_config_result(SHARES)["DRINASConfig"]["DRIShareConfig"]
shares = sh["Shares"]["Share"]
check("repeated <Share> becomes a list", isinstance(shares, list) and len(shares) == 2,
      type(shares).__name__)
check("share names parsed", [s["ShareName"] for s in shares] == ["Alpha", "Beta"],
      [s.get("ShareName") for s in shares])
check("a password NESTED inside a share is redacted too",
      "leakme" not in repr(sh), sh)

print("\n== the two redaction holes found by audit, 2026-07-26 ==")
# Hole 1: this firmware sometimes returns its result as an XML document
# *escaped into a text node* (&lt;Password&gt;, not <Password>) -- which is
# exactly how eCmdGetSysInfo replies. _strip_secrets matches on element tags,
# so against that shape it had nothing to match and the whole document walked
# straight out. eCmdGetAdminConfig is in nasd_sweep's allowlist, so the next
# sweep would have written a live admin password to disk.
import html as _html

LEAK = "hunter2-REAL-SECRET"


def escaped_result(inner: str) -> bytes:
    return (b"<TMCmd><CmdID>30</CmdID><ResultDetails>"
            + _html.escape(inner).encode()
            + b"</ResultDetails></TMCmd>\x00")


esc = config_cmd.parse_config_result(escaped_result(
    f"<DRINasAdminConfig><UserName>admin</UserName>"
    f"<Password>{LEAK}</Password></DRINasAdminConfig>"))
check("secret inside an ESCAPED inner document is redacted", LEAK not in repr(esc), esc)

check("...also when the inner document carries its own <?xml?> declaration",
      LEAK not in repr(config_cmd.parse_config_result(escaped_result(
          '<?xml version="1.0" encoding="UTF-8"?>'
          f"<DRINasAdminConfig><Password>{LEAK}</Password></DRINasAdminConfig>"))))

check("...also when the secret is nested several levels down",
      LEAK not in repr(config_cmd.parse_config_result(escaped_result(
          f"<Shares><Share><ShareName>Docs</ShareName>"
          f"<SharePassword>{LEAK}</SharePassword></Share></Shares>"))))

check("XML-shaped text we CAN'T parse is redacted rather than trusted",
      LEAK not in repr(config_cmd.parse_config_result(escaped_result(f"<Password>{LEAK}"))))

# Hole 2: a credential element whose value lived in CHILD elements rather than
# its own text survived, because the old code replaced .text (empty here) and
# then deliberately skipped descending.
child_doc = (b"<TMCmd><ResultDetails><DRINasAdminConfig>"
             b"<UserName>admin</UserName>"
             b"<Password><Value>" + LEAK.encode() + b"</Value></Password>"
             b"</DRINasAdminConfig></ResultDetails></TMCmd>\x00")
check("a credential element's CHILD elements are redacted too",
      LEAK not in repr(config_cmd.parse_config_result(child_doc)),
      config_cmd.parse_config_result(child_doc))

# ...without the fix becoming so blunt it eats real data.
sysinfo_like = config_cmd.parse_config_result(escaped_result(
    "<SysInfo><UpTime>1209645</UpTime><Temperature>36</Temperature></SysInfo>"))
check("a non-credential escaped document is NOT redacted away",
      "36" in repr(sysinfo_like) and "1209645" in repr(sysinfo_like), sysinfo_like)

plain = config_cmd.parse_config_result(
    b"<TMCmd><ResultDetails><DRINasNetworkConfig><NasName>MyDrobo</NasName>"
    b"<IPConfig><IP>10.0.0.5</IP></IPConfig></DRINasNetworkConfig>"
    b"</ResultDetails></TMCmd>\x00")
check("ordinary config is untouched by the new pass",
      plain["DRINasNetworkConfig"]["NasName"] == "MyDrobo"
      and plain["DRINasNetworkConfig"]["IPConfig"]["IP"] == "10.0.0.5", plain)

print("\n== malformed and empty input ==")
try:
    config_cmd.parse_config_result(b"<TMCmd><CmdID>30</CmdID></TMCmd>\x00")
    check("missing ResultDetails raises", False)
except config_cmd.ConfigError:
    check("missing ResultDetails raises ConfigError", True)
try:
    config_cmd.parse_config_result(b"<not xml at all\x00")
    check("malformed XML raises", False)
except config_cmd.ConfigError:
    check("malformed XML raises ConfigError", True)
check("empty payload gives an empty dict", config_cmd.parse_config_result(b"\x00") == {})

print("\n== frame construction matches the observed wire format ==")
f = config_cmd._build_frame(config_cmd.TYPE_COMMAND, b"abc")
check("frame starts with DRINETTM", f[:8] == b"DRINETTM", f[:8])
check("type byte placed correctly", f[8] == config_cmd.TYPE_COMMAND, f[8])
check("length is big-endian", struct.unpack(">I", f[12:16])[0] == 3, f[12:16].hex())

lp = config_cmd._login_payload("drb000000SAMPLE")
check("login payload is 220 bytes", len(lp) == 220, len(lp))
check("serial at offset 0", lp[:15] == b"drb000000SAMPLE", lp[:15])
check("serial repeated at offset 20", lp[20:35] == b"drb000000SAMPLE", lp[20:35])
check("rest is NUL padding", set(lp[35:]) == {0})
try:
    config_cmd._login_payload("x" * 40)
    check("over-long serial rejected", False)
except config_cmd.ConfigError:
    check("over-long serial rejected", True)

print("\n== safety guards ==")
check("eCmdGetConfig has a confirmed id", commands.COMMANDS.get("eCmdGetConfig") == 30,
      commands.COMMANDS.get("eCmdGetConfig"))
check("the dangerous commands are still refused everywhere",
      {"eCmdRunCommand", "eCmdWriteHostBuffer"} <= commands.DO_NOT_SEND)
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", section="nonsense")
    check("unknown section rejected before connecting", False)
except config_cmd.ConfigError as exc:
    check("unknown section rejected before connecting", "unknown section" in str(exc))


# --------------------------------------------------------------------------
# End to end against a fake Drobo -- proves login + command + parse together
# --------------------------------------------------------------------------
print("\n== end to end against a fake device ==")


def fake_drobo(reply: bytes, port_holder: list, login_ok=True):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port_holder.append(srv.getsockname()[1])
    srv.listen(1)

    def run():
        try:
            conn, _ = srv.accept()
            conn.settimeout(5)
            # login frame
            head = conn.recv(16)
            ln = struct.unpack(">I", head[12:16])[0]
            conn.recv(ln)
            conn.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK if login_ok else 0x99, b""))
            if not login_ok:
                conn.close(); srv.close(); return
            head = conn.recv(16)
            ln = struct.unpack(">I", head[12:16])[0]
            conn.recv(ln)
            conn.sendall(
                config_cmd._build_frame(config_cmd.TYPE_RESULT, reply))
            conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()


ports = []
fake_drobo(ADMIN, ports)
got = config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", section="admin",
                            port=ports[0], timeout=5)
a = got["DRINASConfig"]["DRINasAdminConfig"]
check("end-to-end fetch works", a["UserName"] == "admin", a)
check("end-to-end result carries NO credential",
      not any(s in repr(got) for s in SECRETS), got)

ports = []
fake_drobo(b"", ports)
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", port=ports[0],
                          timeout=5)
    check("empty device reply raises", False)
except config_cmd.ConfigError:
    check("empty device reply raises ConfigError", True)

ports = []
fake_drobo(NETWORK, ports, login_ok=False)
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", port=ports[0],
                          timeout=5)
    check("refused login raises", False)
except config_cmd.ConfigError as exc:
    check("refused login raises ConfigError", "login refused" in str(exc), str(exc))

# --------------------------------------------------------------------------
# A dribbling peer cannot hold the command channel open for ever
# --------------------------------------------------------------------------
print("\n== a dribbling peer cannot hold the command channel open ==")
# This is the defect the bounded-read fix closed on the status channel
# (esatm._receive_exactly), still live here until now. _receive_exactly
# had only a PER-RECV socket timeout, and a socket timeout is not a bound: a
# peer that sends one byte just inside every window keeps the loop running for
# as long as it likes, and the header can declare a payload of up to
# MAX_PAYLOAD_BYTES to make "as long as it likes" mean hours. On THIS channel
# that is worse than a stalled read, because a write left waiting on a result
# frame that never finishes arriving is an outcome-unknown write.
#
# Everything below is a socketpair or 127.0.0.1: loopback only, no hardware.

# A well-formed header declaring the largest payload the module will accept --
# exactly what a peer wanting to stall would send.
HEAD_MAX = (config_cmd.SIGNATURE + bytes([config_cmd.TYPE_RESULT, 0x01])
            + b"\x00\x00"
            + struct.pack(">I", config_cmd.MAX_PAYLOAD_BYTES))
BUDGET = 0.4        # the deadline under test
CEILING = 5.0       # generous: this asserts "bounded at all", not a stopwatch


def dribble(sock, data, gap=0.02):
    """Send `data` a byte at a time -- the peer a per-recv timeout can't catch."""
    def run():
        try:
            for i in range(len(data)):
                sock.sendall(data[i:i + 1])
                time.sleep(gap)
        except OSError:
            pass  # the reader gave up and closed, which is the point
    threading.Thread(target=run, daemon=True).start()


for _label, _whole, _trickle in (
        ("payload", HEAD_MAX, b"x" * 4096),
        ("header", b"", HEAD_MAX + b"x" * 64)):
    a, b = socket.socketpair()
    if _whole:
        b.sendall(_whole)
    dribble(b, _trickle)
    _t0 = time.monotonic()
    try:
        config_cmd._read_frame(a, deadline=time.monotonic() + BUDGET)
        check(f"a dribbled {_label} gives up", False)
    except (config_cmd.ConfigError, OSError):
        # ConfigError once the budget is spent, or the socket timeout the
        # shrinking deadline puts on the last recv -- which one you get depends
        # on the scheduler, so this asserts that it stopped, not how.
        check(f"a dribbled {_label} gives up", True)
    finally:
        _elapsed = time.monotonic() - _t0
        a.close()
        b.close()
    check(f"...and the dribbled {_label} read ends within its budget",
          _elapsed < CEILING, _elapsed)

# ...without the bound getting in the way of a device that simply answers.
a, b = socket.socketpair()
b.sendall(config_cmd._build_frame(config_cmd.TYPE_RESULT, NETWORK))
frame_type, body = config_cmd._read_frame(a,
                                          deadline=time.monotonic() + CEILING)
check("a normal read still succeeds under a deadline",
      frame_type == config_cmd.TYPE_RESULT and body == NETWORK, frame_type)
a.close()
b.close()

# And the default is still no bound at all, so every caller outside this
# module -- droboapps, eventlog, identify, sysinfo, drobosync, nasd_sweep --
# reads exactly as it did before.
a, b = socket.socketpair()
b.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
frame_type, body = config_cmd._read_frame(a)
check("a read with no deadline is unchanged",
      frame_type == config_cmd.TYPE_LOGIN_OK and body == b"", frame_type)
check("the deadline stays optional",
      config_cmd._receive_exactly.__defaults__ == (None,),
      config_cmd._receive_exactly.__defaults__)
a.close()
b.close()

print("\n-- the error is the one esatm raises for the same situation --")
# Same situation on both channels: the budget is already spent when the read
# begins, so neither module even reaches recv. The class differs because each
# raises its own framing error -- FrameError there, ConfigError here, which is
# what every other bad-frame path in this module raises and what
# _send_section_document reads as "the result frame was lost". The WORDING must
# not differ: one fix, one message, whichever channel stalled.
_spent = time.monotonic() - 1.0
a, b = socket.socketpair()
c, d = socket.socketpair()
cc_exc = es_exc = None
try:
    config_cmd._read_frame(a, deadline=_spent)
except config_cmd.ConfigError as exc:
    cc_exc = exc
try:
    esatm.read_frame(c, deadline=_spent)
except esatm.FrameError as exc:
    es_exc = exc
for _sock in (a, b, c, d):
    _sock.close()
check("a spent budget raises this module's own framing error",
      type(cc_exc) is config_cmd.ConfigError, type(cc_exc).__name__)
check("esatm raises its own for the same situation",
      type(es_exc) is esatm.FrameError, type(es_exc).__name__)
check("...and both say the same thing", str(cc_exc) == str(es_exc),
      (str(cc_exc), str(es_exc)))

print("\n== the command path is bounded, not just the helper ==")
# A bounded helper is worth nothing if get_config never passes a deadline.
# Here is the end-to-end version: a device that logs us in, takes the command,
# and then trickles a reply that never finishes.


def fake_dribbler(port_holder):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port_holder.append(srv.getsockname()[1])
    srv.listen(1)

    def run():
        try:
            conn, _ = srv.accept()
            conn.settimeout(5)
            head = conn.recv(16)
            conn.recv(struct.unpack(">I", head[12:16])[0])
            conn.sendall(
                config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
            head = conn.recv(16)
            conn.recv(struct.unpack(">I", head[12:16])[0])
            conn.sendall(HEAD_MAX)          # a payload that never arrives...
            for _ in range(4096):           # ...one byte at a time
                conn.sendall(b"x")
                time.sleep(0.02)
            conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()


ports = []
fake_dribbler(ports)
_t0 = time.monotonic()
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", port=ports[0],
                          timeout=0.5)
    check("get_config gives up on a dribbling device", False)
except (config_cmd.ConfigError, OSError):
    check("get_config gives up on a dribbling device", True)
_elapsed = time.monotonic() - _t0
check("...in bounded time", _elapsed < CEILING, _elapsed)


print("\n== a bounded read hands the socket back as it found it ==")
# The deadline is implemented by shrinking the socket's OWN timeout as the
# budget runs down, and that shrunken value belongs to the one read. This
# channel, unlike esatm's read-the-greeting-and-close, keeps using the socket:
# get_config, _send_section_document and get_config_element all read the login
# reply and then send the command frame on the same socket. A timeout left
# shrunken would quietly become the budget for that sendall, so a device that
# answered LOGIN_OK late in the budget could make the command frame time out on
# send -- a narrowing of the caller's timeout that nobody asked for.
a, b = socket.socketpair()
a.settimeout(5.0)
dribble(b, config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
config_cmd._read_frame(a, deadline=time.monotonic() + CEILING)
check("a completed bounded read leaves the socket's timeout alone",
      a.gettimeout() == 5.0, a.gettimeout())
a.close()
b.close()

# ...and so does one that runs out of budget: the restore sits in a finally,
# so the failing path cannot be the one that leaks the shrunken value.
a, b = socket.socketpair()
a.settimeout(5.0)
b.sendall(HEAD_MAX)          # a header promising a payload that never arrives
try:
    config_cmd._read_frame(a, deadline=time.monotonic() + 0.2)
except (config_cmd.ConfigError, OSError):
    pass
check("so does one that runs out of budget", a.gettimeout() == 5.0, a.gettimeout())
a.close()
b.close()

# With no deadline the timeout is never touched at all.
a, b = socket.socketpair()
a.settimeout(3.5)
b.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
config_cmd._read_frame(a)
check("an unbounded read never touches it", a.gettimeout() == 3.5, a.gettimeout())
a.close()
b.close()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
