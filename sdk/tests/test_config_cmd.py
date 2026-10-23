"""
Tests for the eCmdGetConfig command path -- the first thing this project
SENDS to a Drobo.

Runs entirely offline against a fake device, so it passes on a machine
with no hardware. The fixtures are shaped exactly like the real
responses observed on a live 5N, including the credential fields the
device really does hand out.

The redaction tests are the important ones. Asking a Drobo for its admin
config makes it return <Password>, <EncryptedPassword> and
<ValidPassword> in the clear, to anyone who completes a login -- and the
login is just the device serial, which the Drobo gives to any
unauthenticated caller. We cannot fix the device. We can refuse to
become a second copy of the leak, and these tests are what keep that
true.

    py test_config_cmd.py
"""
import html
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import commands  # noqa: E402
from drobo_nasd import esatm  # noqa: E402

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
NETWORK_CONFIG_REPLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>30</CmdID><Result>0</Result><ResultDetails><DRINASConfig>
<DRINasNetworkConfig><Version>11</Version><NasName>TestDrobo</NasName>
<NasWorkgroup>WORKGROUP</NasWorkgroup>
<IPConfig><IPConfigType>0</IPConfigType><IP>10.0.0.5</IP>
<Subnet>255.255.255.0</Subnet><Gateway>10.0.0.1</Gateway></IPConfig>
</DRINasNetworkConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

ADMIN_CONFIG_REPLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><DRINasAdminConfig>
<UserName>admin</UserName><Password>hunter2</Password>
<ValidPassword>1</ValidPassword>
<EncryptedPassword>QUJDREVGRw==</EncryptedPassword>
</DRINasAdminConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

SHARE_CONFIG_REPLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><DRIShareConfig>
<Shares>
<Share><ShareName>Alpha</ShareName><ShareState>0</ShareState>
<ShareUsers><ShareUser><ShareUsername>Everyone</ShareUsername>
<SharePassword>leakme</SharePassword></ShareUser></ShareUsers></Share>
<Share><ShareName>Beta</ShareName><ShareState>0</ShareState></Share>
</Shares></DRIShareConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

FIXTURE_SECRETS = ("hunter2", "QUJDREVGRw==", "leakme")

print("== parsing, and the redaction that matters ==")

network_result = config_cmd.parse_config_result(NETWORK_CONFIG_REPLY)
network_config = network_result["DRINASConfig"]["DRINasNetworkConfig"]
check("network parses", network_config["NasName"] == "TestDrobo",
      network_config.get("NasName"))
check(
    "nested IPConfig parses",
    network_config["IPConfig"]["IP"] == "10.0.0.5",
    network_config.get("IPConfig"),
)

admin_config = (config_cmd.parse_config_result(ADMIN_CONFIG_REPLY)
                ["DRINASConfig"]["DRINasAdminConfig"])
check("admin username kept (not a secret)",
      admin_config["UserName"] == "admin", admin_config.get("UserName"))
check("Password redacted",
      admin_config["Password"] == "[redacted]",
      admin_config.get("Password"))
check(
    "ValidPassword redacted",
    admin_config["ValidPassword"] == "[redacted]",
    admin_config.get("ValidPassword"),
)
check("EncryptedPassword redacted",
      admin_config["EncryptedPassword"] == "[redacted]",
      admin_config.get("EncryptedPassword"))
check("no admin secret survives anywhere in the result",
      not any(s in repr(admin_config) for s in FIXTURE_SECRETS), admin_config)

share_config = (config_cmd.parse_config_result(SHARE_CONFIG_REPLY)
                ["DRINASConfig"]["DRIShareConfig"])
shares = share_config["Shares"]["Share"]
check(
    "repeated <Share> becomes a list",
    isinstance(shares, list) and len(shares) == 2,
    type(shares).__name__,
)
check(
    "share names parsed",
    [s["ShareName"] for s in shares] == ["Alpha", "Beta"],
    [s.get("ShareName") for s in shares],
)
check("a password NESTED inside a share is redacted too",
      "leakme" not in repr(share_config), share_config)

print("\n== the two redaction holes found by audit, 2026-07-26 ==")
# Hole 1: this firmware sometimes returns its result as an XML document
# *escaped into a text node* (&lt;Password&gt;, not <Password>) -- which
# is exactly how eCmdGetSysInfo replies. _strip_secrets matches on
# element tags, so against that shape it had nothing to match and the
# whole document walked straight out. eCmdGetAdminConfig is in
# nasd_sweep's allowlist, so the next sweep would have written a live
# admin password to disk.
PLANTED_SECRET = "hunter2-REAL-SECRET"


def escaped_result(inner_xml: str) -> bytes:
    return (b"<TMCmd><CmdID>30</CmdID><ResultDetails>"
            + html.escape(inner_xml).encode()
            + b"</ResultDetails></TMCmd>\x00")


escaped_admin_result = config_cmd.parse_config_result(escaped_result(
    f"<DRINasAdminConfig><UserName>admin</UserName>"
    f"<Password>{PLANTED_SECRET}</Password></DRINasAdminConfig>"))
check("secret inside an ESCAPED inner document is redacted",
      PLANTED_SECRET not in repr(escaped_admin_result), escaped_admin_result)

check(
    "...also when the inner document carries its own <?xml?> declaration",
    PLANTED_SECRET
    not in repr(
        config_cmd.parse_config_result(
            escaped_result(
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<DRINasAdminConfig><Password>{PLANTED_SECRET}</Password>"
                "</DRINasAdminConfig>"
            )
        )
    ),
)

check("...also when the secret is nested several levels down",
      PLANTED_SECRET not in repr(config_cmd.parse_config_result(escaped_result(
          f"<Shares><Share><ShareName>Docs</ShareName>"
          f"<SharePassword>{PLANTED_SECRET}</SharePassword>"
          "</Share></Shares>"))))

check(
    "XML-shaped text we CAN'T parse is redacted rather than trusted",
    PLANTED_SECRET
    not in repr(
        config_cmd.parse_config_result(
            escaped_result(f"<Password>{PLANTED_SECRET}")
        )
    ),
)

# Hole 2: a credential element whose value lived in CHILD elements
# rather than its own text survived, because the old code replaced .text
# (empty here) and then deliberately skipped descending.
child_element_reply = (b"<TMCmd><ResultDetails><DRINasAdminConfig>"
                       b"<UserName>admin</UserName>"
                       b"<Password><Value>" + PLANTED_SECRET.encode()
                       + b"</Value></Password>"
                       b"</DRINasAdminConfig></ResultDetails></TMCmd>\x00")
check("a credential element's CHILD elements are redacted too",
      PLANTED_SECRET not in repr(
          config_cmd.parse_config_result(child_element_reply)),
      config_cmd.parse_config_result(child_element_reply))

# ...without the fix becoming so blunt it eats real data.
sysinfo_like = config_cmd.parse_config_result(
    escaped_result(
        "<SysInfo><UpTime>1209645</UpTime><Temperature>36</Temperature>"
        "</SysInfo>"
    )
)
check(
    "a non-credential escaped document is NOT redacted away",
    "36" in repr(sysinfo_like) and "1209645" in repr(sysinfo_like),
    sysinfo_like,
)

plain_network_result = config_cmd.parse_config_result(
    b"<TMCmd><ResultDetails><DRINasNetworkConfig><NasName>MyDrobo</NasName>"
    b"<IPConfig><IP>10.0.0.5</IP></IPConfig></DRINasNetworkConfig>"
    b"</ResultDetails></TMCmd>\x00")
check("ordinary config is untouched by the new pass",
      plain_network_result["DRINasNetworkConfig"]["NasName"] == "MyDrobo"
      and plain_network_result["DRINasNetworkConfig"]["IPConfig"]["IP"]
      == "10.0.0.5", plain_network_result)

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
check("empty payload gives an empty dict",
      config_cmd.parse_config_result(b"\x00") == {})

print("\n== frame construction matches the observed wire format ==")
frame = config_cmd._build_frame(config_cmd.TYPE_COMMAND, b"abc")
check("frame starts with DRINETTM", frame[:8] == b"DRINETTM", frame[:8])
check("type byte placed correctly",
      frame[8] == config_cmd.TYPE_COMMAND, frame[8])
check("length is big-endian",
      struct.unpack(">I", frame[12:16])[0] == 3, frame[12:16].hex())

login_payload = config_cmd._login_payload("drb000000SAMPLE")
check("login payload is 220 bytes", len(login_payload) == 220,
      len(login_payload))
check("serial at offset 0", login_payload[:15] == b"drb000000SAMPLE",
      login_payload[:15])
check("serial repeated at offset 20",
      login_payload[20:35] == b"drb000000SAMPLE", login_payload[20:35])
check("rest is NUL padding", set(login_payload[35:]) == {0})
try:
    config_cmd._login_payload("x" * 40)
    check("over-long serial rejected", False)
except config_cmd.ConfigError:
    check("over-long serial rejected", True)

print("\n== safety guards ==")
check(
    "eCmdGetConfig has a confirmed id",
    commands.COMMANDS.get("eCmdGetConfig") == 30,
    commands.COMMANDS.get("eCmdGetConfig"),
)
check("the dangerous commands are still refused everywhere",
      {"eCmdRunCommand", "eCmdWriteHostBuffer"} <= commands.DO_NOT_SEND)
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", section="nonsense")
    check("unknown section rejected before connecting", False)
except config_cmd.ConfigError as exc:
    check("unknown section rejected before connecting",
          "unknown section" in str(exc))


# --------------------------------------------------------------------------
# End to end against a fake Drobo -- proves login + command
# + parse together
# --------------------------------------------------------------------------
print("\n== end to end against a fake device ==")


def fake_drobo(reply: bytes, port_holder: list, login_ok=True):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port_holder.append(server_socket.getsockname()[1])
    server_socket.listen(1)

    def serve_one_connection():
        try:
            connection, _ = server_socket.accept()
            connection.settimeout(5)
            # login frame
            header = connection.recv(16)
            payload_length = struct.unpack(">I", header[12:16])[0]
            connection.recv(payload_length)
            connection.sendall(config_cmd._build_frame(
                config_cmd.TYPE_LOGIN_OK if login_ok else 0x99, b""))
            if not login_ok:
                connection.close()
                server_socket.close()
                return
            header = connection.recv(16)
            payload_length = struct.unpack(">I", header[12:16])[0]
            connection.recv(payload_length)
            connection.sendall(
                config_cmd._build_frame(config_cmd.TYPE_RESULT, reply))
            connection.close()
        except OSError:
            pass
        finally:
            server_socket.close()

    threading.Thread(target=serve_one_connection, daemon=True).start()


ports = []
fake_drobo(ADMIN_CONFIG_REPLY, ports)
admin_fetch_result = config_cmd.get_config("127.0.0.1", "drb000000SAMPLE",
                                           section="admin",
                                           port=ports[0], timeout=5)
fetched_admin_config = admin_fetch_result["DRINASConfig"]["DRINasAdminConfig"]
check("end-to-end fetch works", fetched_admin_config["UserName"] == "admin",
      fetched_admin_config)
check("end-to-end result carries NO credential",
      not any(s in repr(admin_fetch_result) for s in FIXTURE_SECRETS),
      admin_fetch_result)

ports = []
fake_drobo(b"", ports)
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", port=ports[0],
                          timeout=5)
    check("empty device reply raises", False)
except config_cmd.ConfigError:
    check("empty device reply raises ConfigError", True)

ports = []
fake_drobo(NETWORK_CONFIG_REPLY, ports, login_ok=False)
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", port=ports[0],
                          timeout=5)
    check("refused login raises", False)
except config_cmd.ConfigError as exc:
    check("refused login raises ConfigError",
          "login refused" in str(exc), str(exc))

# --------------------------------------------------------------------------
# A dribbling peer cannot hold the command channel open for ever
# --------------------------------------------------------------------------
print("\n== a dribbling peer cannot hold the command channel open ==")
# This is the defect the bounded-read fix closed on the status channel
# (esatm._receive_exactly), still live here until now. _receive_exactly
# had only a PER-RECV socket timeout, and a socket timeout is not a
# bound: a peer that sends one byte just inside every window keeps the
# loop running for as long as it likes, and the header can declare a
# payload of up to MAX_PAYLOAD_BYTES to make "as long as it likes" mean
# hours. On THIS channel that is worse than a stalled read, because a
# write left waiting on a result frame that never finishes arriving is
# an outcome-unknown write.
#
# Everything below is a socketpair or 127.0.0.1:
# loopback only, no hardware.

# A well-formed header declaring the largest payload the module will
# accept -- exactly what a peer wanting to stall would send.
HEADER_DECLARING_MAX_PAYLOAD = (config_cmd.SIGNATURE
                                + bytes([config_cmd.TYPE_RESULT, 0x01])
                                + b"\x00\x00"
                                + struct.pack(">I",
                                              config_cmd.MAX_PAYLOAD_BYTES))
READ_BUDGET_SECONDS = 0.4        # the deadline under test
# generous: this asserts "bounded at all", not a stopwatch
ELAPSED_CEILING_SECONDS = 5.0


def dribble(sending_socket, data, gap_seconds=0.02):
    """Send `data` a byte at a time --
    the peer a per-recv timeout can't catch.
    """
    def send_one_byte_at_a_time():
        try:
            for i in range(len(data)):
                sending_socket.sendall(data[i:i + 1])
                time.sleep(gap_seconds)
        except OSError:
            pass  # the reader gave up and closed, which is the point
    threading.Thread(target=send_one_byte_at_a_time, daemon=True).start()


for _label, _sent_at_once, _trickled_bytes in (
        ("payload", HEADER_DECLARING_MAX_PAYLOAD, b"x" * 4096),
        ("header", b"", HEADER_DECLARING_MAX_PAYLOAD + b"x" * 64)):
    reader_socket, writer_socket = socket.socketpair()
    if _sent_at_once:
        writer_socket.sendall(_sent_at_once)
    dribble(writer_socket, _trickled_bytes)
    _started_at = time.monotonic()
    try:
        config_cmd._read_frame(reader_socket,
                               deadline=time.monotonic() + READ_BUDGET_SECONDS)
        check(f"a dribbled {_label} gives up", False)
    except (config_cmd.ConfigError, OSError):
        # ConfigError once the budget is spent, or the socket timeout
        # the shrinking deadline puts on the last recv -- which one you
        # get depends on the scheduler, so this asserts that it stopped,
        # not how.
        check(f"a dribbled {_label} gives up", True)
    finally:
        _elapsed = time.monotonic() - _started_at
        reader_socket.close()
        writer_socket.close()
    check(f"...and the dribbled {_label} read ends within its budget",
          _elapsed < ELAPSED_CEILING_SECONDS, _elapsed)

# ...without the bound getting in the way of a device
# that simply answers.
reader_socket, writer_socket = socket.socketpair()
writer_socket.sendall(
    config_cmd._build_frame(config_cmd.TYPE_RESULT, NETWORK_CONFIG_REPLY))
frame_type, frame_payload = config_cmd._read_frame(reader_socket,
                                                   deadline=time.monotonic()
                                                   + ELAPSED_CEILING_SECONDS)
check("a normal read still succeeds under a deadline",
      frame_type == config_cmd.TYPE_RESULT
      and frame_payload == NETWORK_CONFIG_REPLY, frame_type)
reader_socket.close()
writer_socket.close()

# And the default is still no bound at all, so every caller outside this
# module -- droboapps, eventlog, identify, sysinfo, drobosync,
# nasd_sweep -- reads exactly as it did before.
reader_socket, writer_socket = socket.socketpair()
writer_socket.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
frame_type, frame_payload = config_cmd._read_frame(reader_socket)
check("a read with no deadline is unchanged",
      frame_type == config_cmd.TYPE_LOGIN_OK and frame_payload == b"",
      frame_type)
check("the deadline stays optional",
      config_cmd._receive_exactly.__defaults__ == (None,),
      config_cmd._receive_exactly.__defaults__)
reader_socket.close()
writer_socket.close()

print("\n-- the error is the one esatm raises for the same situation --")
# Same situation on both channels: the budget is already spent when the
# read begins, so neither module even reaches recv. The class differs
# because each raises its own framing error -- FrameError there,
# ConfigError here, which is what every other bad-frame path in this
# module raises and what _send_section_document reads as "the result
# frame was lost". The WORDING must not differ: one fix, one message,
# whichever channel stalled.
_spent_deadline = time.monotonic() - 1.0
reader_socket, writer_socket = socket.socketpair()
esatm_reader_socket, esatm_writer_socket = socket.socketpair()
config_cmd_error = esatm_error = None
try:
    config_cmd._read_frame(reader_socket, deadline=_spent_deadline)
except config_cmd.ConfigError as exc:
    config_cmd_error = exc
try:
    esatm.read_frame(esatm_reader_socket, deadline=_spent_deadline)
except esatm.FrameError as exc:
    esatm_error = exc
for _socket_to_close in (reader_socket, writer_socket, esatm_reader_socket,
                         esatm_writer_socket):
    _socket_to_close.close()
check("a spent budget raises this module's own framing error",
      type(config_cmd_error) is config_cmd.ConfigError,
      type(config_cmd_error).__name__)
check("esatm raises its own for the same situation",
      type(esatm_error) is esatm.FrameError, type(esatm_error).__name__)
check("...and both say the same thing",
      str(config_cmd_error) == str(esatm_error),
      (str(config_cmd_error), str(esatm_error)))

print("\n== the command path is bounded, not just the helper ==")
# A bounded helper is worth nothing if get_config never passes a
# deadline. Here is the end-to-end version: a device that logs us in,
# takes the command, and then trickles a reply that never finishes.


def fake_dribbler(port_holder):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port_holder.append(server_socket.getsockname()[1])
    server_socket.listen(1)

    def serve_one_connection():
        try:
            connection, _ = server_socket.accept()
            connection.settimeout(5)
            header = connection.recv(16)
            connection.recv(struct.unpack(">I", header[12:16])[0])
            connection.sendall(
                config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
            header = connection.recv(16)
            connection.recv(struct.unpack(">I", header[12:16])[0])
            # a payload that never arrives...
            connection.sendall(HEADER_DECLARING_MAX_PAYLOAD)
            for _ in range(4096):           # ...one byte at a time
                connection.sendall(b"x")
                time.sleep(0.02)
            connection.close()
        except OSError:
            pass
        finally:
            server_socket.close()

    threading.Thread(target=serve_one_connection, daemon=True).start()


ports = []
fake_dribbler(ports)
_started_at = time.monotonic()
try:
    config_cmd.get_config("127.0.0.1", "drb000000SAMPLE", port=ports[0],
                          timeout=0.5)
    check("get_config gives up on a dribbling device", False)
except (config_cmd.ConfigError, OSError):
    check("get_config gives up on a dribbling device", True)
_elapsed = time.monotonic() - _started_at
check("...in bounded time", _elapsed < ELAPSED_CEILING_SECONDS, _elapsed)


print("\n== a bounded read hands the socket back as it found it ==")
# The deadline is implemented by shrinking the socket's OWN timeout as
# the budget runs down, and that shrunken value belongs to the one read.
# This channel, unlike esatm's read-the-greeting-and-close, keeps using
# the socket: get_config, _send_section_document and get_config_element
# all read the login reply and then send the command frame on the same
# socket. A timeout left shrunken would quietly become the budget for
# that sendall, so a device that answered LOGIN_OK late in the budget
# could make the command frame time out on send -- a narrowing of the
# caller's timeout that nobody asked for.
reader_socket, writer_socket = socket.socketpair()
reader_socket.settimeout(5.0)
dribble(writer_socket, config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
config_cmd._read_frame(reader_socket,
                       deadline=time.monotonic() + ELAPSED_CEILING_SECONDS)
check("a completed bounded read leaves the socket's timeout alone",
      reader_socket.gettimeout() == 5.0, reader_socket.gettimeout())
reader_socket.close()
writer_socket.close()

# ...and so does one that runs out of budget: the restore sits in a
# finally, so the failing path cannot be the one that leaks the shrunken
# value.
reader_socket, writer_socket = socket.socketpair()
reader_socket.settimeout(5.0)
# a header promising a payload that never arrives
writer_socket.sendall(HEADER_DECLARING_MAX_PAYLOAD)
try:
    config_cmd._read_frame(reader_socket, deadline=time.monotonic() + 0.2)
except (config_cmd.ConfigError, OSError):
    pass
check("so does one that runs out of budget",
      reader_socket.gettimeout() == 5.0, reader_socket.gettimeout())
reader_socket.close()
writer_socket.close()

# With no deadline the timeout is never touched at all.
reader_socket, writer_socket = socket.socketpair()
reader_socket.settimeout(3.5)
writer_socket.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
config_cmd._read_frame(reader_socket)
check("an unbounded read never touches it",
      reader_socket.gettimeout() == 3.5, reader_socket.gettimeout())
reader_socket.close()
writer_socket.close()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
