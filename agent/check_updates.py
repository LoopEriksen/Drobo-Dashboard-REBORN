#!/usr/bin/env python3
"""
Check for a newer Drobo Dashboard REBORN, the way the Start Menu
shortcut does.

    py check_updates.py           # uses config.json beside this file
    py check_updates.py --config other.json

This is what windows/installer/Check-ForUpdates.bat runs, BY PATH. It
used to run `python -m drobo_agent.updates` from inside this folder,
which works with an ordinary `py` and fails in every installed copy: the
bundled embeddable Python ships a python314._pth, which runs the
interpreter isolated with safe_path on, so the working directory is
never on sys.path and the import fails with ModuleNotFoundError.
run_agent.py survives that only because it puts its own folder on
sys.path first; this file does the same, so the shortcut no longer
depends on where it was started from.

The check itself is drobo_agent/updates.py, exactly as the app's own
check is. Exit status is its answer -- 0 up to date or switched off, 1
an update is available, 2 could not tell -- plus one of this file's own:

    3  the check could not run at all (a missing file, a broken
       config.json, a bug). Python's own exit status for a crash is 1,
       which would read as "an update is available", so a failure is
       given a number of its own.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXIT_COULD_NOT_RUN = 3


def main(argv: list[str] | None = None) -> int:
    try:
        from drobo_agent import updates
        return updates.main(argv)
    except SystemExit as exc:
        # config.load() stops on a config.json that is not valid JSON by
        # raising SystemExit with its explanation, which Python would
        # turn into exit status 1. argparse's own exits carry a number;
        # let those through as they are.
        if exc.code is None or isinstance(exc.code, int):
            raise
        print(exc.code, file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    except Exception:
        traceback.print_exc()
        return EXIT_COULD_NOT_RUN


if __name__ == "__main__":
    raise SystemExit(main())
