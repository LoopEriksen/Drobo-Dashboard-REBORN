#!/usr/bin/env python3
"""
sweep_last_six.py -- the six commands left untested, sent one at a time.

The maintainer's instruction was explicit: test them if they will not
make any lasting change to the data on the Drobo. By that standard all
six qualify -- none of them writes to the disk pack. What each one DOES
risk is different from what it risks to the data, so each is named
honestly below.

    eCmdIdentify        Flashes the front lights. Nothing else. The
                        maintainer needs to be looking at the unit for
                        the result to mean anything -- the device will
                        not tell us it worked.

    eCmdGetUpdate       Asks the Drobo to check for firmware updates,
                        which means it dials a vendor server that no
                        longer exists. No data risk. The risk is the
                        command port hanging while the device waits for
                        a reply that never comes, on a port already
                        known to tire.

    eCmdModeSense       SCSI MODE SENSE. In SCSI this is a read -- it
                        returns mode pages -- so with an empty
                        parameter block it either reports or errors.
                        Called "raw passthrough" earlier out of
                        caution, which was right before anyone checked
                        what the command actually is.

    eCmdDoPoll          Makes the device re-read its own state. A
                        refresh, not a write. The unknown is scope, not
                        permanence.

    eCmdCacheBattery    Either reports battery health or starts a
                        battery self-test. Neither touches data, and a
                        5N has no cache battery for a self-test to run
                        on.

    eCmdAuthenticateUser
                        Validates a username and password. Sent with an
                        empty parameter block it cannot be a login
                        attempt with real credentials -- there are none
                        to send. Worth noting the one non-data risk: if
                        the device counts failed authentications,
                        repeated calls could lock an account. So it is
                        sent ONCE.

METHOD
------
One at a time, in that order, with the status port checked between every
single command rather than only at the end. If the device stops
greeting, the run stops immediately -- the point is to notice trouble at
the command that caused it, not three commands later.

Identify is sent LAST, so that if anything does upset the device, it
happened before the one command whose result the maintainer has to
observe by eye.

    py tools/sweep_last_six.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_TOOLS_DIR, "..", "sdk")))
sys.path.insert(0, _TOOLS_DIR)

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import esatm  # noqa: E402
import nasd_sweep  # noqa: E402

#: Order matters. Identify last -- see the module docstring.
ORDER: list[tuple[str, str]] = [
    ("eCmdModeSense", "SCSI MODE SENSE -- a read in SCSI terms"),
    ("eCmdCacheBattery",
     "battery status, or possibly a self-test; a 5N has no battery"),
    ("eCmdDoPoll", "makes the device re-read its own state"),
    ("eCmdAuthenticateUser",
     "sent ONCE -- repeated failures could lock an account"),
    ("eCmdGetUpdate", "dials a dead vendor server; may hang"),
    ("eCmdIdentify", "flashes the lights -- WATCH THE UNIT"),
]


# nasd's status channel, where the unprompted greeting arrives.
STATUS_PORT = 5000
# How long to wait for that greeting before calling the device gone.
GREETING_TIMEOUT_SECONDS = 8
# A breather between commands, on a command port already known to tire.
PAUSE_BETWEEN_COMMANDS_SECONDS = 1.0


def check_status_port(host: str) -> tuple[bool, str]:
    """Is the device still greeting on the status port?

    Asked before the first command and after every one, so trouble is
    noticed at the command that caused it. Returns (True, device name)
    or (False, the error).
    """
    try:
        identity = esatm.parse_identity(
            esatm.fetch_greeting(host, STATUS_PORT,
                                 timeout=GREETING_TIMEOUT_SECONDS))
        return True, identity.get("name", "?")
    except OSError as exc:
        return False, str(exc)


def _host_from_agent_config():
    """The Drobo address in agent/config.json, or "" if it has none."""
    try:
        return json.load(open(
            os.path.join(_TOOLS_DIR, "..", "agent", "config.json"),
            encoding="utf-8"))["drobo"]["host"]
    except (OSError, KeyError, ValueError):
        return ""


def _read_esa_id(host) -> str:
    """The serial from `host`'s status greeting, or "" if unreadable.

    The command channel's login carries the serial, so main reads it
    once, from the same host the commands go to, and hands it to every
    send.
    """
    if not host:
        return ""
    try:
        return esatm.parse_identity(
            esatm.fetch_greeting(host, STATUS_PORT,
                                 timeout=GREETING_TIMEOUT_SECONDS)
        ).get("esa_id", "")
    except OSError:
        return ""


def _detail_suffix(record: dict) -> str:
    """What follows the status on a command's result line."""
    detail_suffix = ""
    if record["status"] == "answered":
        detail_suffix = f", {len(record.get('tags', []))} fields"
        if record.get("escaped_document"):
            detail_suffix += " [escaped document]"
    if record.get("detail"):
        detail_suffix += f" ({str(record['detail'])[:50]})"
    return detail_suffix


def _send_and_print(host: str, esa_id: str, command_name: str,
                    command_id: int, timeout_seconds: float) -> dict:
    """Send one command through nasd_sweep and print how it went.

    nasd_sweep.send_one_command does the login, the empty-parameter
    request and the redaction, so this script adds nothing to the
    bytes that go out.
    """
    started_at = time.monotonic()
    record = nasd_sweep.send_one_command(host,
                                         nasd_sweep.DEFAULT_COMMAND_PORT,
                                         esa_id,
                                         command_id, timeout_seconds)
    elapsed_seconds = time.monotonic() - started_at
    record["name"], record["cmd_id"] = command_name, command_id
    print(f"        -> {record['status']}{_detail_suffix(record)}   "
          f"[{elapsed_seconds:.1f}s]")
    return record


def _device_still_greeting(host: str) -> bool:
    """Check the status port after a command, and say what it showed."""
    is_healthy, device_name_or_error = check_status_port(host)
    print(
        "        device after: "
        f"{'OK' if is_healthy else 'NOT ANSWERING -- ' + device_name_or_error}"
        "\n")
    if not is_healthy:
        print("!! The device stopped greeting. Stopping here rather than "
              "sending anything else.")
    return is_healthy


def _send_in_order(host: str, esa_id: str,
                   timeout_seconds: float) -> list[dict]:
    """Send the six in ORDER, stopping the moment the device goes quiet.

    The guard is asked again for each command even though none of the
    six is in DO_NOT_SEND: this is the one script that deliberately
    sends things previously held back, so it re-checks rather than
    trusting the list.
    """
    results = []
    for position, (command_name, reason) in enumerate(ORDER, 1):
        command_id = commands.COMMANDS[command_name]
        is_blocked_by_guard = commands.is_dangerous(command_name)
        print(f"[{position}/{len(ORDER)}] {command_name} (id {command_id})")
        print(f"        {reason}")
        if is_blocked_by_guard:
            print("        REFUSED by the guard -- skipping.\n")
            results.append({"name": command_name,
                            "status": "refused-by-guard"})
            continue

        results.append(_send_and_print(host, esa_id, command_name,
                                       command_id, timeout_seconds))
        if not _device_still_greeting(host):
            break
        time.sleep(PAUSE_BETWEEN_COMMANDS_SECONDS)
    return results


def _write_report(host: str, results: list[dict]) -> None:
    """Print one line per command, then save the (redacted) records.

    The report goes to the temp folder, outside the repository, because
    it names the host. The file is closed when written; it used to be
    left open for the garbage collector.
    """
    print("== summary ==")
    for record in results:
        print(f"  {record['name']:24} {record['status']}")

    report_path = os.path.join(tempfile.gettempdir(), "drobo-last-six.json")
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump({"host": host, "results": results}, report_file, indent=1)
    print(f"\nRedacted report: {report_path}")


def main(argv=None) -> int:
    """Check the device, send the six one at a time, write a report.

    The arguments are parsed before anything is contacted, so --help
    reaches no device, and the serial is read from the host the
    commands go to. It used to be read by the __main__ block from
    sys.argv[1] before parsing: `HOST --help` read a greeting, an
    option before the host took the serial from agent/config.json's
    address (or none) while sending to HOST, and calling main()
    directly raised NameError.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("host", nargs="?", default="")
    parser.add_argument("--timeout", type=float, default=6.0)
    args = parser.parse_args(argv)

    host = args.host
    if not host:
        host = _host_from_agent_config()
    if not host or host == "auto":
        print("No Drobo address.")
        return 1

    is_healthy, device_name_or_error = check_status_port(host)
    if not is_healthy:
        print(f"Drobo not reachable before we start: {device_name_or_error}")
        return 1
    esa_id = _read_esa_id(host)
    if not esa_id:
        # _read_esa_id gives "" for a greeting that could not be read as
        # well as for one without a serial, so the message covers both.
        print("Could not read the Drobo's serial; cannot log in.")
        return 1
    print(f"Starting against {device_name_or_error}. Status port healthy.\n")

    results = _send_in_order(host, esa_id, args.timeout)
    _write_report(host, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
