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
import errno
import http.client
import json
import os
import signal
import sys
import threading
import urllib.request
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

#: The exit status when this agent closed because another one already
#: answers on its port. Distinct from 1, every other startup failure,
#: so Run-Agent.bat can close its window quietly instead of showing
#: "The agent stopped" over an agent that is in fact running.
EXIT_ALREADY_RUNNING = 3

#: How long the "is that another agent?" question waits for /api/ping
#: on the taken port. A running agent answers on loopback in
#: milliseconds, but one that is itself still starting holds its port
#: while it looks for the Drobo, and answers only once that is done --
#: a few seconds, longer on a PC whose network is still coming up. Two
#: launches a moment apart meet exactly that agent, and a shorter wait
#: would call it "another program". Only a program that accepts and
#: then says nothing makes this agent wait the whole time.
AGENT_PING_TIMEOUT_SECONDS = 15.0

#: The bind failures that mean "something already has this port":
#: EADDRINUSE everywhere, and on Windows WSAEADDRINUSE (10048) and
#: WSAEACCES (10013), which a port held exclusively by another program,
#: or reserved by Windows itself, gives instead. Any other bind failure
#: (an agent.bind address this PC does not have, say) is not this, and
#: still surfaces as itself.
PORT_TAKEN_ERRNOS = frozenset({errno.EADDRINUSE})
PORT_TAKEN_WINERRORS = frozenset({10048, 10013})


def main(argv: list[str] | None = None) -> int:
    """
    Start the agent and serve until stopped (Ctrl+C or SIGTERM).

    --once is the exception: it connects explicitly (Monitor.start(),
    which would otherwise do it, is never called), prints one reading
    and exits, without the photo store or the HTTP server -- a quick
    "can it see my Drobo?" with nothing left running. When it can read
    no Drobo it says why and exits 1 (see _print_one_reading).

    The port is claimed before the monitor starts, not after as it was
    until 2026-11-20. A second agent, started while the first was still
    running, then closes with one sentence before it has looked for
    the Drobo at all, instead of running discovery and reading the
    Drobo first and only then failing to bind (see
    _explain_taken_port). The listening socket queues connections
    while the monitor starts, and serving begins once it has.
    """
    args = _parse_args(argv)
    loaded_config = config.load(args.config)

    notifier = notify.build(loaded_config["notify"])
    monitor = Monitor(loaded_config, notifier, config_path=args.config)

    if args.once:
        return _print_one_reading(monitor)

    photo_store = PhotoStore(loaded_config["photos"])
    photo_store.load()
    # Beside the config file, never on the Drobo: see pairing.py.
    paired_devices = pairing.PairedDevices(
        pairing.path_beside_config(args.config))

    try:
        http_server = api.build_http_server(loaded_config, monitor,
                                            photo_store, paired_devices)
    except OSError as exc:
        if not _port_is_taken(exc):
            raise
        return _explain_taken_port(loaded_config["agent"], args.config)
    _start_monitor_or_release_port(monitor, http_server)

    dashboard_url = _print_banner(loaded_config["agent"],
                                  config.display_path(args.config))
    if args.open:
        _open_browser_soon(
            f"{dashboard_url}?token={loaded_config['agent']['token']}")

    _serve_until_stopped(http_server, monitor, photo_store)
    return 0


def _print_one_reading(monitor: Monitor) -> int:
    """
    --once: connect, print one reading as JSON, and return the exit
    status -- 0 with a reading (one marked unreachable included), 1
    when connecting found no Drobo it could read.

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


def _print_banner(agent_settings: dict, config_file_display: str) -> str:
    """
    Say where the agent is listening, and where its token is, and
    return the dashboard's URL. An all-interfaces bind is shown as
    127.0.0.1, the address a browser on this PC can actually open.
    The config file is named as config.display_path writes it: an
    installed copy keeps it in %LOCALAPPDATA%, not beside the agent.
    """
    bind_address = agent_settings["bind"]
    agent_port = agent_settings["port"]
    display_host = _local_host_for(bind_address)
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
          f"once for the access token, which is in {config_file_display}",
          flush=True)
    if bind_address == "0.0.0.0":
        print("[agent] bound to all interfaces - "
              "LAN only, never port-forward this",
              flush=True)
    return f"http://{display_host}:{agent_port}/"


def _explain_taken_port(agent_settings: dict, config_path: str) -> int:
    """
    Say in one sentence why the agent cannot have its port, and return
    the exit status.

    Until 2026-11-20 a taken port ended the agent in a raw OSError
    traceback ("[Errno 98] Address already in use") under
    Run-Agent.bat's "The agent stopped" footer, and the usual cause
    was harmless: the agent window stays open after the app closes, so
    the next launch from the Start Menu started a second agent beside
    the first. Which sentence is printed is decided by asking
    /api/ping on that port whether the program holding it is an agent.
    """
    agent_port = int(agent_settings["port"])
    ping_host = _local_host_for(agent_settings["bind"])
    if _agent_answers_on(ping_host, agent_port):
        print(f"[agent] An agent is already running on port {agent_port}; "
              f"this one will close.", file=sys.stderr, flush=True)
        return EXIT_ALREADY_RUNNING
    print(f"[agent] Another program is using port {agent_port}; change "
          f"agent.port in {os.path.abspath(config_path)}.",
          file=sys.stderr, flush=True)
    return 1


def _start_monitor_or_release_port(monitor: Monitor, http_server) -> None:
    """
    Monitor.start(), closing the already-bound server if it raises: an
    identity mismatch ends the agent with SystemExit, and Ctrl+C during
    discovery with KeyboardInterrupt, and neither may leave the port
    held by a socket nobody will serve.
    """
    try:
        monitor.start()
    except BaseException:
        http_server.server_close()
        raise


def _port_is_taken(bind_error: OSError) -> bool:
    """
    True when a bind failed because something already has the port,
    by errno or, on Windows, by the WSA code.
    """
    if bind_error.errno in PORT_TAKEN_ERRNOS:
        return True
    return getattr(bind_error, "winerror", None) in PORT_TAKEN_WINERRORS


def _agent_answers_on(host: str, port: int) -> bool:
    """
    True when http://host:port/api/ping answers as a Drobo agent.

    Through an opener with no proxy handler: urllib otherwise takes the
    proxy from the environment, or on Windows from the system settings,
    and a loopback question must never be sent to a proxy. Any failure
    to get that answer -- refused, silent, not HTTP, not JSON, some
    other service's JSON -- means "not an agent", which is what the
    caller needs to tell the two sentences apart.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ping_url = f"http://{host}:{port}/api/ping"
    try:
        with opener.open(ping_url,
                         timeout=AGENT_PING_TIMEOUT_SECONDS) as response:
            reply = json.loads(response.read())
    except (OSError, ValueError, http.client.HTTPException):
        return False
    return isinstance(reply, dict) and reply.get("agent") == "drobo-agent"


def _local_host_for(bind_address: str) -> str:
    """
    The address a program on this PC uses to reach an agent bound to
    `bind_address`: an all-interfaces bind is reached at 127.0.0.1,
    and a named address at itself.
    """
    if bind_address in ("0.0.0.0", ""):
        return "127.0.0.1"
    return bind_address


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
