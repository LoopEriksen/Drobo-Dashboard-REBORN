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


def read_share_access_codes(host: str) -> dict:
    """{"share\\tuser":  code} straight from the device. Read-only."""
    identity = esatm.parse_identity(
        esatm.fetch_greeting(host, 5000, timeout=8))
    esa_id = identity.get("esa_id") or ""
    if not esa_id:
        raise SystemExit("the Drobo did not report a serial; cannot log in")

    device_config = config_cmd.get_config(host, esa_id, "shares", timeout=8)
    share_config = ((device_config.get("DRINASConfig") or {})
                    .get("DRIShareConfig") or {})
    raw_shares = (share_config.get("Shares") or {}).get("Share") or []
    shares = raw_shares if isinstance(raw_shares, list) else [raw_shares]

    codes = {}
    for share in shares:
        name = (share.get("ShareName") or "").strip()
        users = (share.get("ShareUsers") or {}).get("ShareUser") or []
        users = users if isinstance(users, list) else [users]
        for share_user in users:
            codes[f"{name}\t"
                  f"{(share_user.get('ShareUsername') or '').strip()}"] = \
                str(share_user.get("ShareUserAccess", "")).strip()
    return codes


def print_access_codes(codes: dict) -> None:
    if not codes:
        print("  (no shares reported)")
        return
    width = max(len(k.split("\t")[0]) for k in codes)
    print(f"  {'SHARE'.ljust(width)}  {'USER'.ljust(16)}  CODE")
    for key in sorted(codes):
        share, user = key.split("\t")
        print(f"  {share.ljust(width)}  {user.ljust(16)}  {codes[key]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "step", choices=["before", "after", "show"],
        help="'before', then change a permission in Drobo Dashboard, "
             "then 'after'")
    parser.add_argument(
        "--host", default="",
        help="Drobo address (default: from agent/config.json)")
    args = parser.parse_args(argv)

    host = args.host
    if not host:
        config_path = os.path.join(_AGENT_DIR, "config.json")
        try:
            with open(config_path, encoding="utf-8") as config_file:
                host = json.load(config_file)["drobo"].get("host", "")
        except (OSError, KeyError, ValueError):
            pass
    if not host or host == "auto":
        return int(bool(print("No Drobo address. Pass --host <address>.")))

    try:
        codes = read_share_access_codes(host)
    except OSError as exc:
        print(f"Could not reach the Drobo at {host}: {exc}")
        print("Check you're on the same network as it, then try again.")
        return 1

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot_path = os.path.join(SNAPSHOT_DIR, "before.json")

    if args.step == "show":
        print("\nAccess codes right now:\n")
        print_access_codes(codes)
        return 0

    if args.step == "before":
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

    # after
    if not os.path.exists(snapshot_path):
        print("No 'before' snapshot. Run:  py tools/access_codes.py before")
        return 1
    with open(snapshot_path, encoding="utf-8") as snapshot_file:
        before_codes = json.load(snapshot_file)

    changed = [(k, before_codes.get(k), codes.get(k))
               for k in sorted(set(before_codes) | set(codes))
               if before_codes.get(k) != codes.get(k)]

    if not changed:
        print("\nNothing changed. Did the change get applied in Dashboard?")
        print("Current codes:\n")
        print_access_codes(codes)
        return 0

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
