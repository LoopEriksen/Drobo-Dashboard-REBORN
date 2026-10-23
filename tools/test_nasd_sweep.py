#!/usr/bin/env python3
"""
test_nasd_sweep.py - offline self-test for nasd_sweep.py.
No hardware needed.

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

import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "sdk")))

import nasd_sweep  # noqa: E402
from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402

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


print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
