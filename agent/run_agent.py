#!/usr/bin/env python3
"""
Drobo Agent - the always-on piece.

Watches the Drobo, keeps history, raises alerts, receives photo backups,
and serves all of it as JSON for the Windows and iOS apps.

    py run_agent.py          # uses config.json beside this file
    py run_agent.py --config other.json
    py run_agent.py --once   # take one reading, print it, exit

Nothing here is Drobo-specific: all the hardware knowledge lives in
drobo_agent/drivers.py.
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

from drobo_agent import api, config, notify  # noqa: E402
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--once", action="store_true",
                        help="one reading, then exit")
    parser.add_argument("--open", action="store_true",
                        help="open the dashboard in your browser once it's up")
    args = parser.parse_args(argv)

    loaded_config = config.load(args.config)

    notifier = notify.build(loaded_config["notify"])
    monitor = Monitor(loaded_config, notifier)

    if args.once:
        monitor.driver.connect()
        print(json.dumps(monitor.poll_once().to_dict(), indent=2))
        return 0

    photo_store = PhotoStore(loaded_config["photos"])
    photo_store.load()

    monitor.start()
    http_server = api.serve(loaded_config, monitor, photo_store)

    bind_address = loaded_config["agent"]["bind"]
    port = loaded_config["agent"]["port"]
    display_host = ("127.0.0.1" if bind_address in ("0.0.0.0", "")
                    else bind_address)
    print(f"[agent] listening on http://{display_host}:{port}", flush=True)
    # The address only, never the token. This line used to end in
    # ?token=<the real token>, and it lands in the console window that
    # Run-Agent.bat tells a tester to copy into a public issue when the
    # agent stops. The 2026-09-08 privacy audit fixed the request log
    # and missed this banner, which was then seen printing a live token
    # on 2026-09-14. The page asks for the token once and keeps it for
    # the session; --open, below, still hands it straight to the browser
    # without printing it.
    print(f"[agent] open http://{display_host}:{port}/ - the page asks "
          f"once for the access token, which is in this agent's "
          f"config.json", flush=True)
    if bind_address == "0.0.0.0":
        print("[agent] bound to all interfaces - LAN only, never "
              "port-forward this",
              flush=True)

    if args.open:
        # Give the server a moment to bind before the browser knocks.
        url = (f"http://{display_host}:{port}/"
               f"?token={loaded_config['agent']['token']}")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    stopping = threading.Event()

    def shutdown(_signum=None, _frame=None):
        if stopping.is_set():
            return
        stopping.set()
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
