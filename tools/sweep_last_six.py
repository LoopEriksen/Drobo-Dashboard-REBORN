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


def check_status_port(host: str) -> tuple[bool, str]:
    """Is the device still greeting on the status port?"""
    try:
        identity = esatm.parse_identity(
            esatm.fetch_greeting(host, 5000, timeout=8))
        return True, identity.get("name", "?")
    except OSError as exc:
        return False, str(exc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", nargs="?", default="")
    parser.add_argument("--timeout", type=float, default=6.0)
    args = parser.parse_args(argv)

    host = args.host
    if not host:
        try:
            host = json.load(open(
                os.path.join(_TOOLS_DIR, "..", "agent", "config.json"),
                encoding="utf-8"))["drobo"]["host"]
        except (OSError, KeyError, ValueError):
            pass
    if not host or host == "auto":
        print("No Drobo address.")
        return 1

    is_healthy, device_or_error = check_status_port(host)
    if not is_healthy:
        print(f"Drobo not reachable before we start: {device_or_error}")
        return 1
    print(f"Starting against {device_or_error}. Status port healthy.\n")

    results = []
    for i, (command_name, reason) in enumerate(ORDER, 1):
        command_id = commands.COMMANDS[command_name]
        is_refused = commands.is_dangerous(command_name)
        print(f"[{i}/{len(ORDER)}] {command_name} (id {command_id})")
        print(f"        {reason}")
        if is_refused:
            # Should be impossible -- none of the six is in DO_NOT_SEND
            # -- but this is the one script that deliberately sends
            # things previously held back, so it re-checks rather than
            # trusting the list.
            print("        REFUSED by the guard -- skipping.\n")
            results.append({"name": command_name,
                            "status": "refused-by-guard"})
            continue

        started_at = time.monotonic()
        record = nasd_sweep.send_one_command(host,
                                             nasd_sweep.DEFAULT_COMMAND_PORT,
                                             _startup_esa_id,
                                             command_id, args.timeout)
        elapsed = time.monotonic() - started_at
        record["name"], record["cmd_id"] = command_name, command_id
        results.append(record)

        detail_suffix = ""
        if record["status"] == "answered":
            detail_suffix = f", {len(record.get('tags', []))} fields"
            if record.get("escaped_document"):
                detail_suffix += " [escaped document]"
        if record.get("detail"):
            detail_suffix += f" ({str(record['detail'])[:50]})"
        print(f"        -> {record['status']}{detail_suffix}   "
              f"[{elapsed:.1f}s]")

        is_healthy, device_or_error = check_status_port(host)
        print(
            "        device after: "
            f"{'OK' if is_healthy else 'NOT ANSWERING -- ' + device_or_error}"
            "\n")
        if not is_healthy:
            print("!! The device stopped greeting. Stopping here rather than "
                  "sending anything else.")
            break
        time.sleep(1.0)

    print("== summary ==")
    for record in results:
        print(f"  {record['name']:24} {record['status']}")

    report_path = os.path.join(tempfile.gettempdir(), "drobo-last-six.json")
    json.dump({"host": host, "results": results},
              open(report_path, "w", encoding="utf-8"), indent=1)
    print(f"\nRedacted report: {report_path}")
    return 0


if __name__ == "__main__":
    # _startup_esa_id is read once, up front, and shared by every
    # command below.
    _startup_host = (sys.argv[1]
                     if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
                     else "")
    if not _startup_host:
        try:
            _startup_host = json.load(open(
                os.path.join(_TOOLS_DIR, "..", "agent", "config.json"),
                encoding="utf-8"))["drobo"]["host"]
        except (OSError, KeyError, ValueError):
            _startup_host = ""
    _startup_esa_id = ""
    if _startup_host:
        try:
            _startup_esa_id = esatm.parse_identity(
                esatm.fetch_greeting(_startup_host, 5000, timeout=8)
            ).get("esa_id", "")
        except OSError:
            pass
    raise SystemExit(main())
