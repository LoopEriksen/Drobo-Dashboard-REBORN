"""
Tests for the first write this project ever sends, and the two reads
that landed alongside it.

    py test_identify.py

The Identify tests carry more weight than their size suggests. This
project was read-only against the hardware for its entire life;
eCmdIdentify is the single, deliberate exception. What is being tested
here is not really "does the command work" -- it is "is the boundary
still exactly where we put it".

Runs entirely offline against a fake device. No hardware, no network.
"""
import os
import html
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import droboapps  # noqa: E402
from drobo_nasd import identify  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------------
print("== the boundary: what may be written, and what may not ==")

check("eCmdIdentify is approved", commands.is_safe_write("eCmdIdentify"))
check("eCmdIdentify is not refused", not commands.is_dangerous("eCmdIdentify"))
check("exactly one approved write so far",
      commands.SAFE_WRITES == frozenset({"eCmdIdentify"}),
      sorted(commands.SAFE_WRITES))
check("approved and refused sets are disjoint",
      commands.DO_NOT_SEND.isdisjoint(commands.SAFE_WRITES))

# The whole point. If a future change quietly promotes one of these into
# SAFE_WRITES, this is where it gets caught.
for name in ("eCmdFormatDevice", "eCmdInstallFirmware", "eCmdReset",
             "eCmdRestart", "eCmdShutdown", "eCmdSetDimming", "eCmdRename",
             "eCmdDeleteLUN", "eCmdRepair"):
    check(f"{name} may still not be written", not commands.is_safe_write(name))


# ---------------------------------------------------------------------------
print("\n== a fake Drobo, to prove the exchange ==")


def fake_device(reply_type, reply_body, port_holder, login_ok=True):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port_holder.append(server_socket.getsockname()[1])
    server_socket.listen(1)
    received_frames = {}

    def serve_one_connection():
        try:
            connection, _ = server_socket.accept()
            connection.settimeout(5)
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
            received_frames["command"] = connection.recv(payload_length)
            connection.sendall(config_cmd._build_frame(reply_type, reply_body))
            connection.close()
        except OSError:
            pass
        finally:
            server_socket.close()

    threading.Thread(target=serve_one_connection, daemon=True).start()
    return received_frames


def send_identify(**identify_options):
    """One identify against a throwaway fake device; returns (result,
    bytes)."""
    ports = []
    received_frames = fake_device(config_cmd.TYPE_RESULT, b"", ports)
    result = identify.identify(
        "127.0.0.1",
        "drb000000SAMPLE",
        port=ports[0],
        timeout=5,
        **identify_options,
    )
    return result, received_frames.get("command",
                                       b"").decode("utf-8", "replace")


result, sent_command = send_identify()
check("identify reports it was sent", result["sent"] is True, result)
check("it used the id from the table, not a literal",
      result["command_id"] == commands.COMMANDS["eCmdIdentify"], result)
check("the wire carries the right command id",
      f"<CmdID>{commands.COMMANDS['eCmdIdentify']}</CmdID>" in sent_command,
      sent_command[:160])
check("the device serial is included", "drb000000SAMPLE" in sent_command)

# An empty reply is success here. Identify's result is a blinking light,
# not a document -- so unlike the read commands, no payload must NOT
# read as failure.
check("an empty reply is still success",
      result["device_replied"] is False, result)


# ---------------------------------------------------------------------------
print(
    "\n== the interval: on the wire 2026-07-25, re-found in Dashboard's "
    "binary 2026-08-06 =="
)
#
# This replaces an assertion that used to read "params are sent EMPTY --
# never guessed", and the replacement is deliberate rather than a
# relaxation.
#
# Empty Params was the honest answer while the parameter was unknown:
# the firmware names command 26 but not its arguments. It was believed
# to be why nothing ever blinked, until the 2026-08-07 hardware test
# sent 15 and nothing blinked either. `IdentifyInterval` was recovered
# from Drobo Dashboard.exe 3.5.0, where it sits in the run of <Params>
# tag names. It is also CONFIRMED on the wire: the 2026-07-25 capture of
# the original Dashboard's Blink Lights carries
# <IdentifyInterval>900</IdentifyInterval>. (This comment said "no
# capture of the button exists" until 2026-09-26. One did.)
#
# The default checked below is 15, this project's own guess, NOT the
# captured
# 900. It changes only after 900 has been sent while someone watches the
# unit. What is still open is the UNIT, and these tests are careful not
# to pretend otherwise. They check that the number we chose is
# transmitted exactly as given; none of them claims it means minutes.

check("the interval element is the one from Dashboard's binary",
      identify.INTERVAL_TAG == "IdentifyInterval", identify.INTERVAL_TAG)
check("the default is still this project's guess, 15 (Dashboard sent 900)",
      identify.DEFAULT_INTERVAL == 15, identify.DEFAULT_INTERVAL)
check(
    "by default the interval is on the wire",
    "<Params><IdentifyInterval>15</IdentifyInterval></Params>" in sent_command,
    sent_command[:200],
)
check("the interval is reported back to the caller",
      result["interval"] == 15, result)

custom_interval_result, custom_interval_command = send_identify(interval=45)
check("a caller-chosen interval is transmitted verbatim",
      "<IdentifyInterval>45</IdentifyInterval>" in custom_interval_command,
      custom_interval_command[:200])
check("...and echoed in the result", custom_interval_result["interval"] == 45,
      custom_interval_result)

stop_interval_result, stop_interval_command = send_identify(
    interval=identify.STOP_INTERVAL)
check("zero -- the stop guess -- is sent, not silently dropped",
      "<IdentifyInterval>0</IdentifyInterval>" in stop_interval_command,
      stop_interval_command[:200])
check("zero is reported as zero, not as 'nothing asked for'",
      stop_interval_result["interval"] == 0, stop_interval_result)

# The control case. If the device blinks with an interval and not
# without one, THAT is the measurement that upgrades this from inference
# to fact -- so the old behaviour has to stay reachable on purpose.
no_interval_result, no_interval_command = send_identify(interval=None)
check("interval=None still sends the historical empty Params",
      "<Params></Params>" in no_interval_command, no_interval_command[:200])
check("...and carries no interval element at all",
      "IdentifyInterval" not in no_interval_command, no_interval_command[:200])
check("None is reported as None", no_interval_result["interval"] is None,
      no_interval_result)


print("\n== bad intervals are refused before a socket is opened ==")
# Refused early and locally: a typo should not become a connection to
# storage hardware that is then logged and abandoned half-done.
for bad_interval, reason in ((-1, "negative"), (-900, "very negative"),
                             (identify.MAX_INTERVAL + 1,
                              "beyond the sanity limit"),
                             ("15", "a string"), (15.0, "a float"),
                             (True, "a bool")):
    try:
        identify.identify("127.0.0.1", "drb000000SAMPLE",
                          port=1, timeout=1, interval=bad_interval)
        check(f"{reason} interval is refused", False, repr(bad_interval))
    except identify.IdentifyError:
        check(f"{reason} interval is refused", True)
    except OSError:
        # Reaching a connect() means validation let it through.
        check(f"{reason} interval is refused BEFORE connecting", False,
              repr(bad_interval))

check("the sanity limit itself is allowed",
      identify._interval_params_xml(identify.MAX_INTERVAL).endswith(
          f"<IdentifyInterval>{identify.MAX_INTERVAL}"
          "</IdentifyInterval></Params>"))
check("True is rejected even though bool is an int subclass",
      isinstance(True, int))

ports = []
fake_device(config_cmd.TYPE_RESULT, b"", ports, login_ok=False)
try:
    identify.identify("127.0.0.1", "drb000000SAMPLE", port=ports[0], timeout=5)
    check("a refused login raises", False)
except identify.IdentifyError as exc:
    check("a refused login raises IdentifyError",
          "login refused" in str(exc), str(exc))

ports = []
fake_device(0x99, b"", ports)
try:
    identify.identify("127.0.0.1", "drb000000SAMPLE", port=ports[0], timeout=5)
    check("an unexpected reply type raises", False)
except identify.IdentifyError:
    check("an unexpected reply type raises IdentifyError", True)


# ---------------------------------------------------------------------------
print("\n== DroboApps: what's installed on the Drobo ==")


def apps_reply(inner_xml):
    return (b"<TMCmd><CmdID>77</CmdID><ResultDetails>"
            + html.escape(inner_xml).encode()
            + b"</ResultDetails></TMCmd>\x00")


parsed_apps = droboapps.parse_droboapps(apps_reply(
    "<DroboApps><SDKVersion>2.1</SDKVersion>"
    "<App><Name>betaapp</Name><Version>2.0.1</Version>"
    "<Description>Example service.</Description><Stopped>0</Stopped></App>"
    "<App><Name>AlphaApp</Name><Version>1.0.0.1</Version>"
    "<Description>Example web app.</Description><Stopped>1</Stopped>"
    "<WebUI>WebUI</WebUI></App>"
    "<App><Name></Name><Version>9</Version></App>"
    "</DroboApps>"))

check("sdk version read", parsed_apps["sdk_version"] == "2.1", parsed_apps)
check("nameless entries are dropped", len(parsed_apps["apps"]) == 2,
      parsed_apps["apps"])
apps_by_name = {app["name"]: app for app in parsed_apps["apps"]}
check("apps come back sorted by name",
      [app["name"] for app in parsed_apps["apps"]] == ["AlphaApp", "betaapp"],
      parsed_apps["apps"])

# The device says "Stopped"; people say "running". The flip happens
# once, in the parser, so nothing above it has to remember the polarity
# is inverted.
check("Stopped=0 becomes running=True",
      apps_by_name["betaapp"]["running"] is True)
check("Stopped=1 becomes running=False",
      apps_by_name["AlphaApp"]["running"] is False)
check("a web interface is noticed",
      apps_by_name["AlphaApp"]["has_web_ui"] is True)
check("...and its absence is not invented",
      apps_by_name["betaapp"]["has_web_ui"] is False)

check("no apps is an empty list, not an error",
      droboapps.parse_droboapps(b"\x00") == {"sdk_version": "", "apps": []})
try:
    droboapps.parse_droboapps(b"<not xml\x00")
    check("malformed XML raises", False)
except Exception as exc:
    check("malformed XML raises", type(exc).__name__ ==
          "SysInfoError", type(exc).__name__)


# ---------------------------------------------------------------------------
print("\n== LED brightness ==")


def parse_brightness(value):
    return droboapps.parse_dimming(
        f"<TMCmd><ResultDetails>{value}</ResultDetails></TMCmd>".encode()
        + b"\x00"
    )


check("the live device's reading parses", parse_brightness(59) == 59)
# The distinction that matters: lights-off is a real setting, and must
# not be confused with having no reading at all.
check("zero is a brightness, not a missing value", parse_brightness(0) == 0)
check("...and is not None", parse_brightness(0) is not None)
check("empty means no reading", parse_brightness("") is None)
check("negative is rejected", parse_brightness(-5) is None)
check("non-numeric is rejected", parse_brightness("bright") is None)
check("no payload at all means no reading",
      droboapps.parse_dimming(b"\x00") is None)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
