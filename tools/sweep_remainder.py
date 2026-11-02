#!/usr/bin/env python3
"""
sweep_remainder.py --
test the safe commands the first sweep never tried.

WHY A SECOND SWEEP
------------------
Of the 103 commands in the firmware table:

    52  refused by commands.DO_NOT_SEND -- format, firmware flash,
        reset, repair, every eCmdSet*, and since 2026-11-02 the
        credential read below. These are not "untested", they are
        permanently excluded, and building them would be actively
        wrong.
    39  covered by tools/nasd_sweep.py
    12  safe by the guard's reckoning, but never sent by the first sweep

This handles those -- or rather, the six of them that can be tested
without either risking the device or needing the maintainer standing in
front of it. Until 2026-11-02 it was thirteen and seven: the seventh,
eCmdGetNASUsernamePassword, was sent by this sweep on 2026-07-27 and
answered empty, and it has been in DO_NOT_SEND since.

WHAT IS TESTED HERE, AND WHY EACH IS SAFE
-----------------------------------------
  eCmdDeviceLogin / eCmdDeviceLogout
      We already log in on every command, but via the LOGIN FRAME TYPE,
      which is a different mechanism to these table commands. Whether
      they do anything at all is genuinely unknown, and finding out
      costs nothing -- worst case is an extra session that the socket
      close ends anyway.

  eCmdGetSavedFileContent, eCmdDownloadConfigFile,
  eCmdDownloadAllConfigFiles, eCmdGetAppDataFile
      File reads. They need a filename we do not have, so they will
      almost certainly refuse -- but HOW they refuse is the information
      wanted. An error naming a missing parameter would tell us the
      parameter format, which is the thing blocking a configuration
      backup. Reads only; none of them writes.

  eCmdGetNASUsernamePassword -- NO LONGER SENT (since 2026-11-02)
      Was included deliberately: the name says it hands back a
      credential, which is exactly why it mattered. If it answered on a
      plain login, anyone on the LAN could read the share passwords off
      this device. Sent once, 2026-07-27, through the same redaction as
      everything else: it answered empty. It is now in
      commands.DO_NOT_SEND, so it has left this list (the guard below
      would otherwise stop the whole sweep) and is in NOT_TESTED.

DELIBERATELY NOT TESTED, WITH REASONS
-------------------------------------
  eCmdIdentify        A write. Built and ready, but it flashes
                      lights -- there is no point sending it unless
                      someone is looking at the unit. The maintainer's
                      call, not something to slip into a sweep.
  eCmdGetUpdate       Would make the Drobo dial a vendor server that
                      no longer exists. A hang on a device that
                      already tires under load is a bad trade for
                      knowing it times out.
  eCmdModeSense       Raw SCSI passthrough to a storage controller. No.
  eCmdDoPoll          Makes the device go and do work on command,
                      with unknown scope. The status greeting already
                      refreshes on its own.
  eCmdCacheBattery    The name is not unambiguously a read -- it could
                      as easily start a battery self-test as report
                      one. A 5N has no cache battery anyway, so there
                      is nothing to learn.
  eCmdAuthenticateUser
                      Needs a real username and password, and would send
                      them over an unencrypted protocol. Not ours to
                      try.

Same discipline as the first sweep: names resolved to ids at runtime,
never hardcoded; an empty <Params> because we never guess parameters; a
fresh connection per command with a pause between; and a hard stop after
three consecutive timeouts, because the command port is known to tire.

    py tools/sweep_remainder.py 10.0.0.5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from xml.etree import ElementTree

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_TOOLS_DIR, "..", "sdk")))
sys.path.insert(0, _TOOLS_DIR)

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import esatm  # noqa: E402
# Reuse its send/redact/report machinery.
import nasd_sweep  # noqa: E402

#: The six. See the module docstring for why each one is safe, and why
#: the others are absent.
REMAINDER: list[str] = [
    "eCmdDeviceLogin",
    "eCmdDeviceLogout",
    "eCmdGetSavedFileContent",
    "eCmdDownloadConfigFile",
    "eCmdDownloadAllConfigFiles",
    "eCmdGetAppDataFile",
]

#: Excluded from this sweep on purpose, with the reason, so the list of
#: what remains untested is explicit rather than something you work out
#: by subtracting two other lists.
NOT_TESTED: dict[str, str] = {
    "eCmdIdentify": "a write; pointless unless someone is watching the unit",
    "eCmdGetUpdate": "would dial a dead vendor server and likely hang",
    "eCmdModeSense": "raw SCSI passthrough to a storage controller",
    "eCmdDoPoll": "makes the device do work of unknown scope on command",
    "eCmdCacheBattery": "name is not unambiguously a read; may be a self-test",
    "eCmdAuthenticateUser":
        "needs real credentials over an unencrypted protocol",
    "eCmdGetNASUsernamePassword":
        "now in DO_NOT_SEND; sent once 2026-07-27, answered empty",
}


# nasd's status channel, where the unprompted greeting arrives.
STATUS_PORT = 5000
# How long to wait for that greeting.
GREETING_TIMEOUT_SECONDS = 8
# Per command, covering the connect and each read.
COMMAND_TIMEOUT_SECONDS = 6.0
# A breather between commands, because the command port tires.
PAUSE_BETWEEN_COMMANDS_SECONDS = 0.5
# This many timeouts in a row stop the sweep.
MAX_CONSECUTIVE_TIMEOUTS = 3

# What a status greeting that arrived but cannot be read raises: a
# frame that is not a Drobo's (FrameError) or XML that does not parse
# (ParseError). No connection at all is an OSError, caught on its own
# so the message can say which. Catching only OSError used to let these
# two end the run in a traceback.
UNREADABLE_GREETING_ERRORS = (esatm.FrameError, ElementTree.ParseError)


def _parse_arguments(argv) -> argparse.Namespace:
    """Read the command line (the module docstring is the --help)."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", nargs="?", default="")
    parser.add_argument("--out", default="")
    return parser.parse_args(argv)


def _host_from_agent_config():
    """The Drobo address in agent/config.json, or "" if it has none."""
    try:
        agent_config = json.load(open(
            os.path.join(_TOOLS_DIR, "..", "agent", "config.json"),
            encoding="utf-8"))
        return agent_config["drobo"].get("host", "")
    except (OSError, KeyError, ValueError):
        return ""


def _fetch_identity(host: str) -> dict | None:
    """The identity in `host`'s status greeting, or None (said why).

    The command login carries the serial, so the sweep cannot start
    without this.
    """
    try:
        return esatm.parse_identity(
            esatm.fetch_greeting(host, STATUS_PORT,
                                 timeout=GREETING_TIMEOUT_SECONDS))
    except OSError as exc:
        print(f"Could not reach the Drobo at {host}: {exc}")
        return None
    except UNREADABLE_GREETING_ERRORS as exc:
        print(f"Could not read the Drobo's greeting at {host}: {exc}")
        return None


def _print_record_line(record: dict) -> None:
    """Print one command's outcome under its "[n/N] name" line."""
    status = record["status"]
    detail_suffix = ""
    if status == "answered":
        detail_suffix = f", {len(record.get('tags', []))} fields"
        if record.get("escaped_document"):
            detail_suffix += "  [escaped inner document]"
    if record.get("detail"):
        detail_suffix += f"  ({str(record['detail'])[:60]})"
    print(f"    -> {status}{detail_suffix}", flush=True)


def _sweep(host: str, esa_id: str) -> list[dict]:
    """Send REMAINDER one fresh login at a time, and return the records.

    nasd_sweep.send_one_command does the login, the empty-parameter
    request and the redaction, so the bytes are the first sweep's.
    MAX_CONSECUTIVE_TIMEOUTS in a row stop it, because the command port
    is known to tire.
    """
    results = []
    consecutive_timeouts = 0
    for position, command_name in enumerate(REMAINDER, 1):
        command_id = commands.COMMANDS[command_name]
        print(f"[{position}/{len(REMAINDER)}] {command_name} "
              f"(id {command_id}) ...", flush=True)
        record = nasd_sweep.send_one_command(
            host, nasd_sweep.DEFAULT_COMMAND_PORT, esa_id, command_id,
            COMMAND_TIMEOUT_SECONDS)
        record["name"], record["cmd_id"] = command_name, command_id
        results.append(record)
        _print_record_line(record)

        if record["status"] == "timeout":
            consecutive_timeouts += 1
        else:
            consecutive_timeouts = 0
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            print("\n!! three consecutive timeouts -- stopping. "
                  "The device may be tiring; not sending the rest.")
            break
        time.sleep(PAUSE_BETWEEN_COMMANDS_SECONDS)
    return results


def _verify_still_healthy(host: str) -> None:
    """Say whether the status greeting still arrives after the sweep."""
    print("\nVerifying the device is still healthy ...")
    try:
        esatm.parse_identity(
            esatm.fetch_greeting(host, STATUS_PORT,
                                 timeout=GREETING_TIMEOUT_SECONDS))
        print("  status port OK -- greeting still arriving.")
    except OSError as exc:
        print(f"  !! status port did not answer: {exc}")
    except UNREADABLE_GREETING_ERRORS as exc:
        print(f"  !! status port greeting could not be read: {exc}")


def _print_summary(results: list[dict]) -> None:
    """Count the records by status, then list what stays untested.

    The untested list is printed every time so the gap is never
    something a reader has to work out.
    """
    print("\n== summary ==")
    for status_name in ("answered", "empty", "refused", "timeout"):
        status_count = sum(1 for record in results
                           if record["status"] == status_name)
        if status_count:
            print(f"  {status_name:9} {status_count}")

    print("\n== still untested, deliberately ==")
    for command_name, reason in NOT_TESTED.items():
        print(f"  {command_name:24} {reason}")


def _write_report(report_path: str, host: str, results: list[dict]) -> None:
    """Write the (already redacted) records as JSON, and say where."""
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump({"host": host, "results": results}, report_file, indent=1)
    print(f"\nRedacted report: {report_path}")
    print("(outside the repo -- not meant to be committed)")


def main(argv=None) -> int:
    """Check the list, read the serial, sweep, check health, report.

    The module docstring says why each of the six is safe to send.
    """
    args = _parse_arguments(argv)

    host = args.host
    if not host:
        host = _host_from_agent_config()
    if not host or host == "auto":
        print("No Drobo address. Pass one, e.g.  "
              "py tools/sweep_remainder.py 10.0.0.5")
        return 1

    # Same guard the first sweep uses: nothing refused may ever be in
    # the list.
    nasd_sweep.assert_allowlist_safe(REMAINDER)
    print(f"Safety check passed: {len(REMAINDER)} commands, "
          "none of them refused.\n")

    identity = _fetch_identity(host)
    if identity is None:
        return 1
    esa_id = identity.get("esa_id") or ""
    if not esa_id:
        print("The Drobo did not report a serial; cannot log in.")
        return 1
    print(f"Talking to {identity.get('name')} ({identity.get('model')}), "
          f"firmware {identity.get('firmware')}\n")

    results = _sweep(host, esa_id)
    _verify_still_healthy(host)
    _print_summary(results)

    report_path = args.out or os.path.join(
        tempfile.gettempdir(), "drobo-sweep-remainder.json")
    _write_report(report_path, host, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
