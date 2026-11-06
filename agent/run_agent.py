#!/usr/bin/env python3
"""
Drobo Agent - the always-on piece.

Watches the Drobo, keeps history, raises alerts, receives photo backups,
and serves all of it as JSON for the Windows and iOS apps.

    py run_agent.py          # uses config.json beside this file
    py run_agent.py --config other.json
    py run_agent.py --once   # take one reading, print it, exit

Nothing here is Drobo-specific:
all the hardware knowledge lives in drobo_agent/drivers.py.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drobo_agent import api, config, notify, pairing  # noqa: E402
from drobo_agent.monitor import (  # noqa: E402
    CANNOT_READ_DROBO_ERRORS, Monitor, why_drobo_cannot_be_read)
from drobo_agent.photos import PhotoStore  # noqa: E402

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.json")


#: How long --open waits before opening the browser, so the server has
#: bound its port before the browser knocks.
BROWSER_OPEN_DELAY_SECONDS = 1.0


def main(argv: list[str] | None = None) -> int:
    """
    Start the agent and serve until stopped (Ctrl+C or SIGTERM).

    --once is the exception: it connects explicitly (Monitor.start(),
    which would otherwise do it, is never called), prints one reading
    and exits, without the photo store or the HTTP server -- a quick
    "can it see my Drobo?" with nothing left running. When it can read
    no Drobo it says why and exits 1 (see _print_one_reading).
    """
    args = _parse_args(argv)
    loaded_config = config.load(args.config)

    notifier = notify.build(loaded_config["notify"])
    monitor = Monitor(loaded_config, notifier)

    if args.once:
        return _print_one_reading(monitor)

    photo_store = PhotoStore(loaded_config["photos"])
    photo_store.load()
    # Beside the config file, never on the Drobo: see pairing.py.
    paired_devices = pairing.PairedDevices(
        pairing.path_beside_config(args.config))

    monitor.start()
    http_server = api.build_http_server(loaded_config, monitor, photo_store,
                                        paired_devices)

    dashboard_url = _print_banner(loaded_config["agent"])
    if args.open:
        _open_browser_soon(
            f"{dashboard_url}?token={loaded_config['agent']['token']}")

    _serve_until_stopped(http_server, monitor, photo_store)
    return 0


def _print_one_reading(monitor: Monitor) -> int:
    """
    --once: connect, print one reading as JSON, and return the exit
    status -- 0 with a reading, 1 when no Drobo could be read.

    No Drobo to read is answered in sentences on stderr, with the
    reasons and advice Monitor.start gives, because --once is the "can
    it see my Drobo?" check docs/testers-tasks.md hands a tester, and
    what it prints is what gets pasted into an issue. Until 2026-11-06
    --once called connect() with nothing around it, so each of those
    failures ended in a Python traceback. Monitor.start then starts
    anyway; --once has no poll loop to hand over to, so it stops.
    Anything else connect() raises is a bug and still surfaces as one.
    """
    try:
        monitor.driver.connect()
    except CANNOT_READ_DROBO_ERRORS as exc:
        print("[agent] no reading taken: "
              + why_drobo_cannot_be_read(monitor.driver, exc),
              file=sys.stderr, flush=True)
        return 1
    print(json.dumps(monitor.poll_once().to_dict(), indent=2))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The command line: --config, --once and --open."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--once", action="store_true",
                        help="one reading, then exit")
    parser.add_argument("--open", action="store_true",
                        help="open the dashboard in your browser once it's up")
    return parser.parse_args(argv)


def _print_banner(agent_settings: dict) -> str:
    """
    Say where the agent is listening, and return the dashboard's URL.
    An all-interfaces bind is shown as 127.0.0.1, the address a browser
    on this PC can actually open.
    """
    bind_address = agent_settings["bind"]
    agent_port = agent_settings["port"]
    display_host = ("127.0.0.1" if bind_address in ("0.0.0.0", "")
                    else bind_address)
    print(f"[agent] listening on http://{display_host}:{agent_port}",
          flush=True)
    # The address only, never the token. This line used to end in
    # ?token=<the real token>, and it lands in the console window that
    # Run-Agent.bat tells a tester to copy into a public issue when the
    # agent stops. The 2026-09-08 privacy audit fixed the request log
    # and missed this banner, which was then seen printing a live token
    # on 2026-09-14. The page asks for the token once and keeps it for
    # the session; --open, below, still hands it straight to the browser
    # without printing it.
    print(f"[agent] open http://{display_host}:{agent_port}/ - the page asks "
          f"once for the access token, which is in this agent's "
          f"config.json", flush=True)
    if bind_address == "0.0.0.0":
        print("[agent] bound to all interfaces - "
              "LAN only, never port-forward this",
              flush=True)
    return f"http://{display_host}:{agent_port}/"


def _open_browser_soon(url: str) -> None:
    """
    Open `url` in the default browser after BROWSER_OPEN_DELAY_SECONDS,
    on a timer thread, so serving is not held up waiting for it.
    """
    # Give the server a moment to bind before the browser knocks.
    threading.Timer(BROWSER_OPEN_DELAY_SECONDS,
                    lambda: webbrowser.open(url)).start()


def _serve_until_stopped(http_server, monitor, photo_store) -> None:
    """
    Serve requests until Ctrl+C or SIGTERM, then stop the monitor, save
    the photo index and close the server, whichever way serving ended.
    """
    shutdown_requested = threading.Event()

    def shutdown(_signum=None, _frame=None):
        # Handed to a thread: shutdown() waits for serve_forever() to
        # return, and a signal handler runs on the very thread that is
        # inside serve_forever(), so calling it here would deadlock.
        if shutdown_requested.is_set():
            return
        shutdown_requested.set()
        print("\n[agent] shutting down ...", flush=True)
        threading.Thread(target=http_server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (AttributeError, ValueError):
        pass  # not available on every Windows console

    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        shutdown()
    finally:
        monitor.stop()
        photo_store.flush()
        http_server.server_close()
        print("[agent] stopped", flush=True)


if __name__ == "__main__":
    sys.exit(main())
