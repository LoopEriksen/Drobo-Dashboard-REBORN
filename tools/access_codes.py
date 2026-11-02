#!/usr/bin/env python3
"""
access_codes.py -- work out what ShareUserAccess numbers actually mean.

THE QUESTION
------------
A Drobo stores share permissions as a bare integer. The live 5N uses 0
and 1; the firmware never says what they mean, and sweeping every binary
in the rootfs for `read list` / `write list` / `valid users` finds them
ONLY in Samba's own libraries -- no Drobo binary mentions them. So the
mapping is computed at runtime and cannot be recovered by reading the
firmware.

Guessing is not acceptable here. Telling someone a share is "read-only"
when it is actually writable is worse than telling them nothing, so
`drobo_nasd/shareedit.py` refuses to invent a code and this project
shows raw numbers until somebody measures what they mean.

HOW THIS MEASURES IT
--------------------
The original Drobo Dashboard 3.5.0 still installs, still talks to the
device, and shows permissions in plain words. So it becomes the
reference:

    1.  py tools/access_codes.py before
    2.  In Drobo Dashboard, set a share's permission to something you
        can name ("Read only" for a user, say) and apply it.
    3.  py tools/access_codes.py after

Step 3 prints exactly which numbers changed, so the word you chose in
Dashboard is pinned to the number the device stores.

This tool only READS. It never sends a write of any kind -- the changing
is done by Dashboard, which is software Drobo shipped for the job. That
is the whole point of doing it this way: a measurement that costs no
risk.

    py tools/access_codes.py before
    py tools/access_codes.py after
    py tools/access_codes.py show      # just print the current codes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from xml.etree import ElementTree

# Two different siblings, for two different reasons: the protocol comes
# from the SDK, and the Drobo's address comes from the agent's own
# config file.
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_SDK_DIR = os.path.abspath(os.path.join(_TOOLS_DIR, "..", "sdk"))
_AGENT_DIR = os.path.abspath(os.path.join(_TOOLS_DIR, "..", "agent"))
sys.path.insert(0, _SDK_DIR)

from drobo_nasd import config_cmd, esatm  # noqa: E402

# Snapshots live outside the repo -- they carry share and user names.
SNAPSHOT_DIR = os.path.join(tempfile.gettempdir(), "drobo-access-codes")


# nasd's status channel, where the greeting with the serial arrives.
STATUS_PORT = 5000
# Used for the greeting and the config read alike.
DEVICE_TIMEOUT_SECONDS = 8
# The USER column's width in the printed table.
USER_COLUMN_WIDTH = 16

# What reading the greeting and the shares config can raise: no
# connection (OSError), a frame that is not a Drobo's (FrameError), XML
# that does not parse (ParseError), or a config reply the SDK could not
# use (ConfigError). Catching only OSError used to let the rest end the
# run in a traceback.
DEVICE_READ_ERRORS = (OSError, esatm.FrameError, ElementTree.ParseError,
                      config_cmd.ConfigError)


def _as_list(value) -> list:
    """`value` as a list: the config parser gives one child bare."""
    return value if isinstance(value, list) else [value]


def read_share_access_codes(host: str) -> dict:
    """{"share\\tuser": code} straight from the device. Read-only.

    Keyed by share and user joined with a tab because that is the
    snapshot format the "before" file stores and "after" compares
    against, one entry per user on each share.
    """
    identity = esatm.parse_identity(
        esatm.fetch_greeting(host, STATUS_PORT,
                             timeout=DEVICE_TIMEOUT_SECONDS))
    esa_id = identity.get("esa_id") or ""
    if not esa_id:
        raise SystemExit("the Drobo did not report a serial; cannot log in")

    device_config = config_cmd.get_config(host, esa_id, "shares",
                                          timeout=DEVICE_TIMEOUT_SECONDS)
    share_config = ((device_config.get("DRINASConfig") or {})
                    .get("DRIShareConfig") or {})
    shares = _as_list((share_config.get("Shares") or {}).get("Share") or [])

    codes_by_share_user = {}
    for share in shares:
        share_name = (share.get("ShareName") or "").strip()
        share_users = _as_list(
            (share.get("ShareUsers") or {}).get("ShareUser") or [])
        for share_user in share_users:
            user_name = (share_user.get("ShareUsername") or "").strip()
            codes_by_share_user[f"{share_name}\t{user_name}"] = str(
                share_user.get("ShareUserAccess", "")).strip()
    return codes_by_share_user


def print_access_codes(codes: dict) -> None:
    """Print the codes as a SHARE / USER / CODE table, sorted by key.

    The share column is as wide as the longest share name, so the
    codes line up for comparing by eye.
    """
    if not codes:
        print("  (no shares reported)")
        return
    width = max(len(key.split("\t")[0]) for key in codes)
    print(f"  {'SHARE'.ljust(width)}  {'USER'.ljust(USER_COLUMN_WIDTH)}  CODE")
    for key in sorted(codes):
        share, user = key.split("\t")
        print(f"  {share.ljust(width)}  {user.ljust(USER_COLUMN_WIDTH)}  "
              f"{codes[key]}")


def _parse_arguments(argv) -> argparse.Namespace:
    """Read the command line (the module docstring is the --help)."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "step", choices=["before", "after", "show"],
        help="'before', then change a permission "
             "in Drobo Dashboard, then 'after'")
    parser.add_argument(
        "--host", default="",
        help="Drobo address (default: from agent/config.json)")
    return parser.parse_args(argv)


def _host_from_agent_config():
    """The Drobo address in agent/config.json, or "" if it has none."""
    config_path = os.path.join(_AGENT_DIR, "config.json")
    try:
        with open(config_path, encoding="utf-8") as config_file:
            return json.load(config_file)["drobo"].get("host", "")
    except (OSError, KeyError, ValueError):
        return ""


def _save_before(snapshot_path: str, codes: dict) -> int:
    """Store the codes as the "before" snapshot; say what to do next."""
    with open(snapshot_path, "w", encoding="utf-8") as snapshot_file:
        json.dump(codes, snapshot_file, indent=1)
    print("\nSaved. Access codes right now:\n")
    print_access_codes(codes)
    print("\nNow, in Drobo Dashboard 3.5.0:")
    print("  1. Open the share you want to test.")
    print("  2. Set a user's permission to something you can name")
    print("     -- 'Read only', 'Read/Write', or 'No access'.")
    print("  3. Apply it, and note down which word you chose.")
    print("\nThen run:  py tools/access_codes.py after")
    return 0


def _print_changes(changed: list[tuple]) -> None:
    """Say, for each changed share/user, what it was and what it is now.

    This is the measurement itself: the word chosen in Dashboard
    against the number the device now stores.
    """
    print("\nWhat changed:\n")
    for key, before, after in changed:
        share, user = key.split("\t")
        if before is None:
            print(f"  {user} was ADDED to {share} with code {after}")
        elif after is None:
            print(f"  {user} was REMOVED from {share} (had code {before})")
        else:
            print(f"  {user} on {share}:  code {before}  ->  {after}")

    print("\nSo the permission you chose in Dashboard is stored as the code")
    print("above. Note which word you picked -- the mapping belongs in")
    print("sdk/drobo_nasd/shares.py, so the app can stop showing raw numbers.")


def _compare_with_before(snapshot_path: str, codes: dict) -> int:
    """Compare the codes now with the "before" snapshot, and say how."""
    if not os.path.exists(snapshot_path):
        print("No 'before' snapshot. Run:  py tools/access_codes.py before")
        return 1
    with open(snapshot_path, encoding="utf-8") as snapshot_file:
        before_codes = json.load(snapshot_file)

    changed = [(key, before_codes.get(key), codes.get(key))
               for key in sorted(set(before_codes) | set(codes))
               if before_codes.get(key) != codes.get(key)]

    if not changed:
        print("\nNothing changed. Did the change get applied in Dashboard?")
        print("Current codes:\n")
        print_access_codes(codes)
        return 0

    _print_changes(changed)
    return 0


def _print_read_failure(host: str, error: Exception) -> None:
    """Say why the codes could not be read, and what to try.

    Only a network error (OSError) means the Drobo was not reached, so
    only that one gets the network advice. A frame, XML or config reply
    that could not be used came over a connection that was made; it
    used to be reported as "Could not reach the Drobo" too.
    """
    if isinstance(error, OSError):
        print(f"Could not reach the Drobo at {host}: {error}")
        print("Check you're on the same network as it, then try again.")
        return
    print(f"Could not read the share settings from the Drobo at {host}: "
          f"{error}")


def main(argv=None) -> int:
    """Read the codes, then show them, save them, or compare them.

    Only reads from the device; the change being measured is made in
    Drobo Dashboard between "before" and "after".
    """
    args = _parse_arguments(argv)

    host = args.host
    if not host:
        host = _host_from_agent_config()
    if not host or host == "auto":
        print("No Drobo address. Pass --host <address>.")
        return 1

    try:
        codes = read_share_access_codes(host)
    except DEVICE_READ_ERRORS as exc:
        _print_read_failure(host, exc)
        return 1

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot_path = os.path.join(SNAPSHOT_DIR, "before.json")

    if args.step == "show":
        print("\nAccess codes right now:\n")
        print_access_codes(codes)
        return 0

    if args.step == "before":
        return _save_before(snapshot_path, codes)

    return _compare_with_before(snapshot_path, codes)


if __name__ == "__main__":
    raise SystemExit(main())
