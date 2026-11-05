#!/usr/bin/env python3
"""
test_nasd_sweep.py - offline self-test for nasd_sweep.py, for
sweep_remainder.py and sweep_last_six.py, which are built on it, and for
access_codes.py. No hardware needed.

Two things matter most here, because they are the two safety guarantees
the task depends on:

  1. ALLOWLIST must never overlap commands.DO_NOT_SEND, every name in
     it must resolve to a confirmed numeric id, and
     assert_allowlist_safe() must actually catch it when a candidate
     list breaks either rule (not just "happen to pass" on the current
     list).
  2. Redaction must fire on every response BEFORE anything is returned
     to a caller that might print/log/write it -- proven against a
     response shaped like the real device's admin-config answer, which
     is known to contain plaintext credentials.

The rest exercises send_one_command() and run_sweep() end to end against
an in-process fake Drobo (same technique as
sdk/tests/test_config_cmd.py), including the three-consecutive-timeouts
abort rule.

    py tools/test_nasd_sweep.py
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import io
import os
import runpy
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "sdk")))

import nasd_sweep  # noqa: E402
from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import esatm  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, failure_detail="") -> None:
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------------
# 1. The allowlist safety guard --
# the most important thing in this file.
# ---------------------------------------------------------------------------
print("== allowlist safety ==")

check("the real ALLOWLIST is disjoint from DO_NOT_SEND",
      set(nasd_sweep.ALLOWLIST).isdisjoint(commands.DO_NOT_SEND),
      set(nasd_sweep.ALLOWLIST) & commands.DO_NOT_SEND)

check("every ALLOWLIST name resolves to a confirmed id",
      all(command_name in commands.COMMANDS
          for command_name in nasd_sweep.ALLOWLIST),
      [command_name for command_name in nasd_sweep.ALLOWLIST
       if command_name not in commands.COMMANDS])

check("ALLOWLIST has no duplicate names",
      len(set(nasd_sweep.ALLOWLIST)) == len(nasd_sweep.ALLOWLIST))

check("ALLOWLIST is exactly 39 names "
      "(the reviewed set -- change deliberately)",
      len(nasd_sweep.ALLOWLIST) == 39, len(nasd_sweep.ALLOWLIST))

# assert_allowlist_safe() must have already run at import time without
# raising -- if it had raised, importing nasd_sweep above would have
# failed and this file would never have reached this line.
check("module import succeeded, i.e. assert_allowlist_safe() "
      "passed on the real list", True)

# Now prove the guard actually catches bad input, not just that the
# current list happens to be fine.
try:
    nasd_sweep.assert_allowlist_safe(["eCmdRunCommand"])
    check("guard rejects a DO_NOT_SEND name", False)
except nasd_sweep.SweepSafetyError:
    check("guard rejects a DO_NOT_SEND name", True)

try:
    nasd_sweep.assert_allowlist_safe(["eCmdWriteHostBuffer"])
    check("guard rejects a second DO_NOT_SEND name (write-host-buffer)", False)
except nasd_sweep.SweepSafetyError:
    check("guard rejects a second DO_NOT_SEND name (write-host-buffer)", True)

# eCmdForceSingleInitiator is a documented near-miss (see the module
# docstring): its name sounds like a query but it is a write, and it is
# NOT in commands.DO_NOT_SEND, so assert_allowlist_safe() would happily
# accept a list containing it -- the guard only enforces
# DO_NOT_SEND-disjointness and valid ids, it is not a semantic safety
# oracle. The actual protection is that a human reviewed ALLOWLIST and
# left it out. Prove that property directly instead of expecting the
# guard to catch it.
check("the near-miss eCmdForceSingleInitiator is simply not in ALLOWLIST",
      "eCmdForceSingleInitiator" not in nasd_sweep.ALLOWLIST)
check("the near-miss eCmdGetNextUniqueLUNID is simply not in ALLOWLIST",
      "eCmdGetNextUniqueLUNID" not in nasd_sweep.ALLOWLIST)

# The second sweep runs its list through the same guard before it sends
# anything. When eCmdGetNASUsernamePassword joined DO_NOT_SEND
# (2026-11-02) it had to leave that list, or the guard would stop the
# whole sweep.
import sweep_remainder  # noqa: E402

try:
    nasd_sweep.assert_allowlist_safe(sweep_remainder.REMAINDER)
    check("sweep_remainder's list passes the allowlist guard", True)
except nasd_sweep.SweepSafetyError as exc:
    check("sweep_remainder's list passes the allowlist guard", False, exc)
check("eCmdGetNASUsernamePassword is recorded as not tested, with why",
      "eCmdGetNASUsernamePassword" in sweep_remainder.NOT_TESTED
      and "eCmdGetNASUsernamePassword" not in sweep_remainder.REMAINDER)

try:
    nasd_sweep.assert_allowlist_safe(["eCmdTotallyMadeUp"])
    check("guard rejects a name with no confirmed id", False)
except nasd_sweep.SweepSafetyError:
    check("guard rejects a name with no confirmed id", True)

try:
    nasd_sweep.assert_allowlist_safe(["eCmdGetVersion", "eCmdGetVersion"])
    check("guard rejects a duplicate name", False)
except nasd_sweep.SweepSafetyError:
    check("guard rejects a duplicate name", True)

try:
    nasd_sweep.assert_allowlist_safe(["eCmdGetVersion", "eCmdGetConfig"])
    check("guard accepts a genuinely safe list", True)
except nasd_sweep.SweepSafetyError as exc:
    check("guard accepts a genuinely safe list", False, exc)


# ---------------------------------------------------------------------------
# 2. Redaction fires before anything is handed back.
# ---------------------------------------------------------------------------
print("\n== redaction ==")

ADMIN_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<TMCmd><CmdID>80</CmdID><ResultDetails><DRINASConfig><DRINasAdminConfig>
<UserName>admin</UserName><Password>hunter2</Password>
<ValidPassword>1</ValidPassword>
<EncryptedPassword>QUJDREVGRw==</EncryptedPassword>
</DRINasAdminConfig></DRINASConfig></ResultDetails></TMCmd>\x00"""

SECRETS = ("hunter2", "QUJDREVGRw==")

record = nasd_sweep._redact_and_summarize(ADMIN_BODY)
check("redacted record parses successfully",
      "parse_error" not in record, record)
check("no secret value survives anywhere in the redacted record",
      not any(s in repr(record) for s in SECRETS), record)
check("the field NAME 'Password' is still visible "
      "(names are safe, values are not)",
      "Password" in record.get("tags", []), record.get("tags"))
check("username (not a secret) survives",
      "admin" in repr(record.get("redacted_value")),
      record.get("redacted_value"))

EMPTY_BODY = b"\x00"
empty_record = nasd_sweep._redact_and_summarize(EMPTY_BODY)
check("an empty body is reported as empty, not as a parse error",
      empty_record.get("empty") is True, empty_record)

MALFORMED_BODY = b"<not xml\x00"
bad_record = nasd_sweep._redact_and_summarize(MALFORMED_BODY)
check("malformed XML is reported, not raised",
      "parse_error" in bad_record, bad_record)


# ---------------------------------------------------------------------------
# 3. End-to-end against an in-process fake Drobo (same technique as
# sdk/tests/test_config_cmd.py's fake_drobo).
# ---------------------------------------------------------------------------
print("\n== end to end against a fake device ==")

TEST_ESA_ID = "drb000000SAMPLE"


def fake_drobo_answers(reply_body: bytes, port_holder: list) -> None:
    """Accept one connection, do a normal login, answer the one command
    with reply_body, then close."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port_holder.append(server.getsockname()[1])
    server.listen(1)

    def serve_once():
        try:
            connection, _ = server.accept()
            connection.settimeout(5)
            header = connection.recv(16)
            payload_length = struct.unpack(">I", header[12:16])[0]
            connection.recv(payload_length)
            connection.sendall(
                config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
            header = connection.recv(16)
            payload_length = struct.unpack(">I", header[12:16])[0]
            connection.recv(payload_length)
            connection.sendall(
                config_cmd._build_frame(config_cmd.TYPE_RESULT, reply_body))
            connection.close()
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve_once, daemon=True).start()


def fake_drobo_refuses_login(port_holder: list) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port_holder.append(server.getsockname()[1])
    server.listen(1)

    def serve_once():
        try:
            connection, _ = server.accept()
            connection.settimeout(5)
            header = connection.recv(16)
            payload_length = struct.unpack(">I", header[12:16])[0]
            connection.recv(payload_length)
            # not TYPE_LOGIN_OK
            connection.sendall(config_cmd._build_frame(0x99, b""))
            connection.close()
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve_once, daemon=True).start()


def fake_drobo_login_ok_then_silent(port_holder: list) -> None:
    """Login succeeds, but the command is never answered -- proves the
    per-command timeout path."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port_holder.append(server.getsockname()[1])
    server.listen(1)

    def serve_once():
        try:
            connection, _ = server.accept()
            connection.settimeout(5)
            header = connection.recv(16)
            payload_length = struct.unpack(">I", header[12:16])[0]
            connection.recv(payload_length)
            connection.sendall(
                config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
            # Read the command, but never reply --
            # the client must time out.
            connection.recv(16)
            time.sleep(5)
            connection.close()
        except OSError:
            pass
        finally:
            server.close()

    threading.Thread(target=serve_once, daemon=True).start()


ports: list[int] = []
fake_drobo_answers(ADMIN_BODY, ports)
result = nasd_sweep.send_one_command("127.0.0.1", ports[0], TEST_ESA_ID,
                                     commands.COMMANDS["eCmdGetAdminConfig"],
                                     timeout=5)
check("answered status recorded", result["status"] == "answered", result)
check("no secret leaks through send_one_command either",
      not any(s in repr(result) for s in SECRETS), result)

ports = []
fake_drobo_answers(b"\x00", ports)
result = nasd_sweep.send_one_command("127.0.0.1", ports[0], TEST_ESA_ID,
                                     commands.COMMANDS["eCmdGetFanInfo"],
                                     timeout=5)
check("empty body -> status 'empty'", result["status"] == "empty", result)

ports = []
fake_drobo_refuses_login(ports)
result = nasd_sweep.send_one_command("127.0.0.1", ports[0], TEST_ESA_ID,
                                     commands.COMMANDS["eCmdGetVersion"],
                                     timeout=5)
check("refused login -> status 'refused', phase 'login'",
      result["status"] == "refused" and result.get("phase") == "login", result)

ports = []
fake_drobo_login_ok_then_silent(ports)
result = nasd_sweep.send_one_command("127.0.0.1", ports[0], TEST_ESA_ID,
                                     commands.COMMANDS["eCmdGetSysInfo"],
                                     timeout=0.5)
check("no command reply -> status 'timeout', phase 'command'",
      result["status"] == "timeout" and result.get("phase") == "command",
      result)

# connect() to a closed port -> refused at the connect phase,
# not a timeout.
result = nasd_sweep.send_one_command("127.0.0.1", 1, TEST_ESA_ID,
                                     commands.COMMANDS["eCmdGetVersion"],
                                     timeout=1)
check("connection refused outright -> status 'refused', phase 'connect'",
      result["status"] == "refused" and result.get("phase") == "connect",
      result)


# ---------------------------------------------------------------------------
# 4. run_sweep() stops after three consecutive timeouts.
# ---------------------------------------------------------------------------
print("\n== three-consecutive-timeouts abort ==")


def fake_drobo_login_hangs(port_holder: list) -> None:
    """Never answers the login at all -- every command sent to this
    server times out at the login phase."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port_holder.append(server.getsockname()[1])
    server.listen(5)

    def serve_forever():
        try:
            while True:
                connection, _ = server.accept()
                # Accept and go silent forever;
                # never close, never answer.
        except OSError:
            pass

    threading.Thread(target=serve_forever, daemon=True).start()


ports = []
fake_drobo_login_hangs(ports)
outcome = nasd_sweep.run_sweep(
    "127.0.0.1", TEST_ESA_ID, port=ports[0], timeout=0.3, pause=0.05,
    names=["eCmdGetVersion", "eCmdGetConfig", "eCmdGetSysInfo",
           "eCmdGetDevices", "eCmdGetFanInfo"])
check("sweep aborts after exactly 3 attempts",
      outcome["sent"] == 3, outcome["sent"])
check("sweep reports aborted=True", outcome["aborted"] is True, outcome)
check("all 3 recorded attempts are timeouts",
      all(r["status"] == "timeout" for r in outcome["results"]),
      outcome["results"])


# ---------------------------------------------------------------------------
# 5. main() settles everything local before the sweep sends a byte.
# ---------------------------------------------------------------------------
print("\n== main() checks the report path and the greeting first ==")


class _CallSpy:
    """Stands in for a function, records each call, returns `result`."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _run_main_offline(argv, fetch_esa_id=None):
    """nasd_sweep.main(argv) with every device contact replaced.

    Returns (exit code or the exception raised, run_sweep spy,
    _fetch_esa_id spy). fetch_esa_id, when given, replaces the stub so
    the real greeting read can be exercised against a patched esatm.
    """
    sweep_spy = _CallSpy({"results": [], "aborted": False, "sent": 0,
                          "planned": 0})
    fetch_spy = _CallSpy(TEST_ESA_ID)
    saved = (nasd_sweep._fetch_esa_id, nasd_sweep.run_sweep,
             nasd_sweep.verify_device_healthy)
    nasd_sweep._fetch_esa_id = fetch_esa_id or fetch_spy
    nasd_sweep.run_sweep = sweep_spy
    nasd_sweep.verify_device_healthy = _CallSpy(
        {"healthy": True, "detail": "stubbed"})
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = nasd_sweep.main(argv)
    except (SystemExit, Exception) as exc:
        # SystemExit is the intended stop; anything else is the fault
        # (a traceback), kept as the outcome so the check can say so.
        outcome = exc
    finally:
        (nasd_sweep._fetch_esa_id, nasd_sweep.run_sweep,
         nasd_sweep.verify_device_healthy) = saved
    return outcome, sweep_spy, fetch_spy


_main_scratch_dir = tempfile.mkdtemp(prefix="nasd-sweep-main-")
atexit.register(shutil.rmtree, _main_scratch_dir, ignore_errors=True)
_saved_working_dir = os.getcwd()
os.chdir(_main_scratch_dir)
try:
    outcome, sweep_spy, _ = _run_main_offline(
        ["127.0.0.1", "-o", "bare.json"])
    check("-o with a bare file name writes the report and exits 0",
          outcome == 0 and os.path.isfile("bare.json"), outcome)
finally:
    os.chdir(_saved_working_dir)

_regular_file_path = os.path.join(_main_scratch_dir, "a-file")
with open(_regular_file_path, "w", encoding="utf-8") as regular_file:
    regular_file.write("not a folder")
outcome, sweep_spy, fetch_spy = _run_main_offline(
    ["127.0.0.1", "-o", os.path.join(_regular_file_path, "x.json")])
check("an -o whose folder cannot exist stops before contacting anything",
      outcome not in (0, None) and not sweep_spy.calls
      and not fetch_spy.calls,
      (outcome, sweep_spy.calls, fetch_spy.calls))

outcome, sweep_spy, fetch_spy = _run_main_offline(
    ["127.0.0.1", "-o", _main_scratch_dir])
check("an -o that names a folder stops before contacting anything",
      outcome == 2 and not sweep_spy.calls and not fetch_spy.calls,
      (outcome, sweep_spy.calls, fetch_spy.calls))


def _greeting_raising(error):
    """A stand-in for esatm.fetch_greeting that raises `error`."""
    def fetch_greeting(*args, **kwargs):
        raise error
    return fetch_greeting


_greeting_failures = [
    ("a refused connect", _greeting_raising(ConnectionRefusedError(
        111, "Connection refused"))),
    ("a frame with a bad signature", _greeting_raising(
        esatm.FrameError("bad signature"))),
    ("a greeting that is not XML", lambda *args, **kwargs: b"<broken\x00"),
]
_saved_fetch_greeting = esatm.fetch_greeting
for failure_label, fake_fetch_greeting in _greeting_failures:
    esatm.fetch_greeting = fake_fetch_greeting
    try:
        outcome, sweep_spy, _ = _run_main_offline(
            ["127.0.0.1", "-o",
             os.path.join(_main_scratch_dir, "greeting.json")],
            fetch_esa_id=nasd_sweep._fetch_esa_id)
    finally:
        esatm.fetch_greeting = _saved_fetch_greeting
    check(f"{failure_label} on the first greeting ends in a plain message",
          isinstance(outcome, SystemExit) and isinstance(outcome.code, str)
          and "greeting" in outcome.code and not sweep_spy.calls,
          repr(outcome))
check("a run stopped at the greeting leaves no empty report behind",
      not os.path.exists(os.path.join(_main_scratch_dir, "greeting.json")))


# ---------------------------------------------------------------------------
# 6. sweep_last_six.py: the serial comes from the host it sends to, and
# --help contacts nothing. No sockets: the greeting and the send are
# both replaced.
# ---------------------------------------------------------------------------
print("\n== sweep_last_six: arguments first, then the same host's serial ==")

import sweep_last_six  # noqa: E402

_SERIAL_BY_HOST = {"127.0.0.2": "drb000000HOSTB", "127.0.0.3": ""}


def _fake_greeting_for(greeting_hosts):
    """A stand-in for esatm.fetch_greeting that logs the host asked.

    Each host's synthetic greeting carries that host's serial, so a
    login made with another host's serial shows up in the send spy.
    """
    def fetch_greeting(host, *args, **kwargs):
        greeting_hosts.append(host)
        serial = _SERIAL_BY_HOST.get(host, "drb000000OTHER")
        return (f"<ESATMUpdate><mESAID>{serial}</mESAID>"
                f"<mName>Sample</mName></ESATMUpdate>").encode() + b"\x00"
    return fetch_greeting


def _run_last_six_as_script(argv):
    """Run sweep_last_six.py as __main__, the way a shell starts it.

    runpy executes a fresh copy of the file, so anything patched on the
    imported module does not reach it; only shared modules (esatm,
    nasd_sweep, time) do.
    """
    sys.argv = ["sweep_last_six.py"] + argv
    runpy.run_path(sweep_last_six.__file__, run_name="__main__")


def _skip_pause(seconds):
    """Stand-in for time.sleep, so the pauses are not waited out."""


def _run_last_six(argv, as_script):
    """Run sweep_last_six with argv; (outcome, greeting hosts, sends).

    as_script runs the file as __main__ (runpy, sys.argv set), which is
    how it is started from a shell; otherwise main(argv) is called.
    time.sleep is replaced rather than PAUSE_BETWEEN_COMMANDS_SECONDS,
    because the runpy copy keeps its own 1-second pause.
    """
    greeting_hosts = []
    send_spy = _CallSpy({"status": "answered", "tags": []})
    if as_script:
        run_sweep = functools.partial(_run_last_six_as_script, argv)
    else:
        run_sweep = functools.partial(sweep_last_six.main, argv)
    saved = (esatm.fetch_greeting, nasd_sweep.send_one_command, time.sleep,
             tempfile.tempdir, sys.argv)
    esatm.fetch_greeting = _fake_greeting_for(greeting_hosts)
    nasd_sweep.send_one_command = send_spy
    time.sleep = _skip_pause
    tempfile.tempdir = _main_scratch_dir
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_sweep()
    except (SystemExit, Exception) as exc:
        outcome = exc
    finally:
        (esatm.fetch_greeting, nasd_sweep.send_one_command, time.sleep,
         tempfile.tempdir, sys.argv) = saved
    return outcome, greeting_hosts, send_spy.calls


def _exit_code(outcome):
    """The exit code a SystemExit carries, or the outcome itself."""
    return outcome.code if isinstance(outcome, SystemExit) else outcome


for help_argv in (["--help"], ["127.0.0.1", "--help"]):
    outcome, greeting_hosts, sends = _run_last_six(help_argv, as_script=True)
    check(f"`sweep_last_six.py {' '.join(help_argv)}` prints help and "
          "contacts nothing",
          _exit_code(outcome) == 0 and not greeting_hosts and not sends,
          (repr(outcome), greeting_hosts))


def _sends_used(sends, host):
    """True when every send went to `host` with `host`'s own serial."""
    return bool(sends) and all(
        args[0] == host and args[2] == _SERIAL_BY_HOST[host]
        for args, _ in sends)


outcome, _, sends = _run_last_six(["127.0.0.2"], as_script=False)
check("main([host]) called directly runs, with that host's serial",
      outcome == 0 and _sends_used(sends, "127.0.0.2"), repr(outcome))

outcome, _, sends = _run_last_six(["--timeout", "1", "127.0.0.2"],
                                  as_script=True)
check("an option before the host still logs in with that host's serial",
      _exit_code(outcome) == 0 and _sends_used(sends, "127.0.0.2"),
      (repr(outcome), [args[:3] for args, _ in sends][:1]))

outcome, _, sends = _run_last_six(["127.0.0.3"], as_script=False)
check("a host that reports no serial stops before any command is sent",
      outcome == 1 and not sends, (repr(outcome), len(sends)))


# ---------------------------------------------------------------------------
# 7. access_codes.py: a failure exits non-zero.
# ---------------------------------------------------------------------------
print("\n== access_codes: failures exit non-zero ==")

import access_codes  # noqa: E402

_saved_host_lookup = access_codes._host_from_agent_config
access_codes._host_from_agent_config = lambda: ""
try:
    with contextlib.redirect_stdout(io.StringIO()):
        outcome = access_codes.main(["show"])
finally:
    access_codes._host_from_agent_config = _saved_host_lookup
check("no Drobo address (no --host, none configured) exits 1",
      outcome == 1, outcome)


# ---------------------------------------------------------------------------
# 8. A greeting or config reply that is not a Drobo's ends in a message
# and exit 1 in access_codes and the two later sweeps, not a traceback.
# ---------------------------------------------------------------------------
print("\n== the read-only tools survive a bad greeting or config reply ==")

import sweep_remainder  # noqa: E402


def _quietly(function, *args):
    """(return value or the exception raised, printed output)."""
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            outcome = function(*args)
    except (SystemExit, Exception) as exc:
        outcome = exc
    return outcome, printed.getvalue()


def _bad_greeting_checks(failure_label):
    """Every tool's greeting path, under whichever fake is installed."""
    outcome, printed = _quietly(access_codes.main,
                                ["show", "--host", "127.0.0.1"])
    check(f"access_codes: {failure_label} -> exit 1, read (not reach)",
          outcome == 1 and "Could not read" in printed
          and "same network" not in printed, (repr(outcome), printed))
    outcome, _ = _quietly(sweep_last_six.main, ["127.0.0.1"])
    check(f"sweep_last_six: {failure_label} -> exit 1", outcome == 1,
          repr(outcome))
    outcome, _ = _quietly(sweep_last_six._read_esa_id, "127.0.0.1")
    check(f"sweep_last_six: {failure_label} -> no serial", outcome == "",
          repr(outcome))
    outcome, printed = _quietly(sweep_remainder._fetch_identity,
                                "127.0.0.1")
    check(f"sweep_remainder: {failure_label} -> no identity, read error",
          outcome is None and "Could not read" in printed,
          (repr(outcome), printed))
    outcome, printed = _quietly(sweep_remainder._verify_still_healthy,
                                "127.0.0.1")
    check(f"sweep_remainder: {failure_label} -> health check says so",
          outcome is None and "!! status port greeting could not be read"
          in printed, (repr(outcome), printed))


_bad_greetings = [
    ("a frame with a bad signature",
     _greeting_raising(esatm.FrameError("bad signature"))),
    ("a greeting that is not XML", lambda *args, **kwargs: b"<broken\x00"),
]
for failure_label, fake_fetch_greeting in _bad_greetings:
    esatm.fetch_greeting = fake_fetch_greeting
    try:
        _bad_greeting_checks(failure_label)
    finally:
        esatm.fetch_greeting = _saved_fetch_greeting


def _config_read_fails(*args, **kwargs):
    """A stand-in for config_cmd.get_config whose reply was unusable."""
    raise config_cmd.ConfigError("reply did not parse")


_saved_get_config = config_cmd.get_config
esatm.fetch_greeting = _fake_greeting_for([])
config_cmd.get_config = _config_read_fails
try:
    outcome, printed = _quietly(access_codes.main,
                                ["show", "--host", "127.0.0.2"])
finally:
    esatm.fetch_greeting = _saved_fetch_greeting
    config_cmd.get_config = _saved_get_config
check("access_codes: a config read that fails (ConfigError) -> exit 1",
      outcome == 1 and "Could not read the share settings" in printed,
      (repr(outcome), printed))


print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
