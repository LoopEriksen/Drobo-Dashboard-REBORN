"""
End-to-end test of GET /api/spindown and POST /api/droboapps/action,
against the simulated Drobo.

    py test_droboapps_api.py

Separate from test_agent.py for the same reason test_identify_api.py is:
these endpoints' interesting branch is the simulated one, and turning
`simulate_config` on in that suite would change how every config read it
makes behaves. So this spins up its own agent on its own port.

WHAT IS ACTUALLY BEING PROVEN

Not that a Drobo spins its drives down or that a DroboApp starts --
nothing here can know that. What this proves is the contract the web
page and the Windows app are built against: that the spin-down read
reports BOTH halves of the state (the delay alone cannot say whether the
feature is on), that an app action's 200 carries the device's own
re-read as the read-back, that every refusal is a 400 with the reason in
plain English before anything is sent, and that the simulator is no more
permissive than the SDK write it stands in for.
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk"
        )
    ),
)

from drobo_agent import api, config, notify  # noqa: E402
from drobo_agent.drivers import MockDriver  # noqa: E402
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


temp_dir = tempfile.mkdtemp(prefix="droboapps-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left
# this folder behind in TEMP.
atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
config_path = os.path.join(temp_dir, "config.json")
PORT = 7438
BASE_URL = f"http://127.0.0.1:{PORT}"

with open(config_path, "w") as config_file:
    json.dump({
        "agent": {"bind": "127.0.0.1", "port": PORT, "poll_seconds": 1},
        "drobo": {"driver": "mock", "simulate_config": True},
        "photos": {"enabled": False},
    }, config_file)

agent_config = config.load(config_path)
TOKEN = agent_config["agent"]["token"]

monitor = Monitor(agent_config, notify.build(agent_config["notify"]))
photo_store = PhotoStore(agent_config["photos"])
photo_store.load()
monitor.start()
http_server = api.serve(agent_config, monitor, photo_store)
threading.Thread(target=http_server.serve_forever, daemon=True).start()
time.sleep(1.0)


def call(path, body=None, method=None, token=TOKEN):
    headers = {"X-Agent-Token": token} if token else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as http_error:
        return http_error.code, json.loads(http_error.read())


driver = monitor.driver
check("the simulated driver is the one under test",
      getattr(driver, "simulate_config", False) is True, type(driver).__name__)

try:
    print("\n== GET /api/spindown: both halves of the state ==")
    status, reply = call("/api/spindown")
    check("spindown 200", status == 200, (status, reply))
    check("reported as simulated", reply.get("simulated") is True, reply)
    check("enabled by default (the mock mirrors the real unit)",
          reply.get("enabled") is True, reply)
    check("delay is the real unit's default, 14", reply.get("delay") == 14,
          reply)
    check(
        "the unconfirmed unit is admitted, not papered over",
        "never says what the unit is" in reply.get("delay_note", ""),
        reply.get("delay_note"),
    )

    print("\n== the spindown read reflects the spindown write ==")
    status, reply = call("/api/config/spindown", body={"enabled": False})
    check("disable accepted", status == 200 and reply.get("ok"),
          (status, reply))
    status, reply = call("/api/spindown")
    check(
        "read-back after disable: enabled false",
        reply.get("enabled") is False,
        reply,
    )
    check("read-back after disable: delay zeroed (both halves move together)",
          reply.get("delay") == 0, reply)

    status, reply = call("/api/config/spindown", body={"delay": 30})
    check("a new delay accepted", status == 200 and reply.get("ok"),
          (status, reply))
    status, reply = call("/api/spindown")
    check(
        "read-back after set: enabled true again",
        reply.get("enabled") is True,
        reply,
    )
    check("read-back after set: delay 30", reply.get("delay") == 30, reply)

    print(
        "\n== POST /api/droboapps/action: Start, with the re-read as "
        "read-back =="
    )
    status, reply = call("/api/droboapps", token=TOKEN)
    alpha_app_rows = [app for app in reply.get("apps", []) if app["name"]
                      == "AlphaApp"]
    check("AlphaApp is in the simulated app list and stopped",
          alpha_app_rows and alpha_app_rows[0]["running"] is False,
          reply.get("apps"))

    status, reply = call(
        "/api/droboapps/action", body={"name": "AlphaApp", "action": "Start"}
    )
    check("Start 200 with ok", status == 200 and reply.get("ok") is True,
          (status, reply))
    check("reported as simulated", reply.get("simulated") is True, reply)
    check(
        "read_back names the app and reports it running",
        reply.get("read_back", {}).get("name") == "AlphaApp"
        and reply.get("read_back", {}).get("running") is True,
        reply.get("read_back"),
    )
    check(
        "the full re-read app list rides along (so a UI needs no extra fetch)",
        any(
            app["name"] == "AlphaApp" and app["running"]
            for app in reply.get("apps", [])
        ),
        reply.get("apps"),
    )
    check("the message reports the device's answer",
          "running" in reply.get("message", ""), reply.get("message"))

    status, reply = call("/api/droboapps")
    check(
        "a later /api/droboapps read agrees",
        [app["running"] for app in reply["apps"] if app["name"] == "AlphaApp"]
        == [True],
        reply,
    )

    status, reply = call(
        "/api/droboapps/action", body={"name": "AlphaApp", "action": "Stop"}
    )
    check("Stop 200 and read_back stopped",
          status == 200 and reply.get("read_back", {}).get("running") is False,
          (status, reply))

    print(
        "\n== refusals are the caller's fault, said before anything is sent =="
    )
    status, reply = call(
        "/api/droboapps/action", body={"name": "AlphaApp", "action": "start"}
    )
    check("lowercase 'start' refused with 400", status == 400, (status, reply))
    check(
        "-- naming the case-sensitivity",
        "case-sensitive" in reply.get("error", ""),
        reply,
    )

    status, reply = call(
        "/api/droboapps/action", body={"name": "AlphaApp", "action": "Restart"}
    )
    check("an uncaptured verb is refused with 400", status == 400,
          (status, reply))

    status, reply = call(
        "/api/droboapps/action",
        body={"name": "NotInstalled", "action": "Start"},
    )
    check("an unknown app is refused with 400", status == 400, (status, reply))
    check("-- and the error lists what IS installed",
          "AlphaApp" in reply.get("error", ""), reply.get("error"))

    status, reply = call("/api/droboapps/action", body={"action": "Start"})
    check("a missing name is refused with 400", status == 400, (status, reply))

    status, reply = call("/api/droboapps/action", body=b"{not json")
    check("malformed JSON is refused with 400", status == 400, (status, reply))
    check("-- and says so", "bad JSON" in reply.get("error", ""), reply)

    status, reply = call("/api/droboapps")
    check(
        "no refused request changed anything",
        [app["running"] for app in reply["apps"] if app["name"] == "AlphaApp"]
        == [False],
        reply,
    )

    print("\n== auth: 401s are clean even with a request body ==")
    # The 401 path must DRAIN the body before answering (see do_POST) --
    # without that, Windows aborts the keep-alive socket and the client
    # sees a connection error instead of this parseable answer.
    status, reply = call("/api/droboapps/action",
                         body={"name": "AlphaApp", "action": "Start"},
                         token="")
    check("unauthenticated POST answers a clean 401", status == 401,
          (status, reply))
    check(
        "-- with the reason in the body",
        "X-Agent-Token" in reply.get("error", ""),
        reply,
    )
    status, reply = call("/api/droboapps/action",
                         body={"name": "AlphaApp", "action": "Start"},
                         token="wrong")
    check("wrong-token POST answers a clean 401", status == 401,
          (status, reply))
    status, reply = call("/api/spindown", token="")
    check("unauthenticated GET /api/spindown answers 401", status == 401,
          (status, reply))
    status, reply = call("/api/droboapps")
    check(
        "...and none of those reached the driver",
        [app["running"] for app in reply["apps"] if app["name"] == "AlphaApp"]
        == [False],
        reply,
    )

    print("\n== the write outcomes are not a share-only vocabulary ==")
    # Share writes get the same treatment in test_shares_api.py. This
    # proves it is _config_write's contract rather than something bolted
    # onto shares: spin-down, a General-block write, reports the same
    # four outcomes -- and the awkward one still moves the device while
    # the answer stays silent.
    call("/api/config/spindown", body={"delay": 14})
    call("/api/mock/write/hangs_up", method="POST")
    status, reply = call("/api/config/spindown", body={"delay": 21})
    check("an unacknowledged spin-down write is a success",
          status == 200 and reply.get("ok") is True, (status, reply))
    check("-- with the same outcome label the share writes use",
          reply.get("outcome") == "verified_unacknowledged", reply)
    check("-- and acknowledged false", reply.get("acknowledged") is False,
          reply)
    status, reply = call("/api/spindown")
    check(
        "-- and the device really did take the new delay",
        reply.get("delay") == 21,
        reply,
    )

    call("/api/mock/write/unconfirmable", method="POST")
    status, reply = call("/api/config/spindown", body={"delay": 30})
    check("an unsettleable spin-down write is 502 and labelled unknown",
          status == 502 and reply.get("outcome") == "unknown", (status, reply))
    check("-- worded as 'may have applied', never as 'nothing happened'",
          "may well have been" in reply.get("error", ""), reply.get("error"))
    status, reply = call("/api/spindown")
    check("-- while the device did in fact take it, which is why it says that",
          reply.get("delay") == 30, reply)

    call("/api/mock/write/refused", method="POST")
    status, reply = call("/api/config/spindown", body={"delay": 45})
    check("a refused spin-down write is 502 and labelled failed",
          status == 502 and reply.get("outcome") == "failed", (status, reply))
    status, reply = call("/api/spindown")
    check("-- and the delay did not move", reply.get("delay") == 30, reply)
    call("/api/mock/reset", method="POST")

    print("\n== the simulator mutates per-instance state only ==")
    call("/api/droboapps/action", body={"name": "AlphaApp", "action": "Start"})
    fresh_driver = MockDriver({"simulate_config": True})
    check("a fresh MockDriver still has AlphaApp stopped",
          [app["running"] for app in fresh_driver.get_droboapps()["apps"]
           if app["name"] == "AlphaApp"] == [False])
    check("the class-level pristine table is untouched",
          [app["running"] for app in MockDriver._MOCK_DROBOAPPS["apps"]
           if app["name"] == "AlphaApp"] == [False])
finally:
    http_server.shutdown()
    monitor.stop()
    http_server.server_close()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
