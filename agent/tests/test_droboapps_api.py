"""
End-to-end test of GET /api/spindown and POST /api/droboapps/action, against
the simulated Drobo.

    py test_droboapps_api.py

Separate from test_agent.py for the same reason test_identify_api.py is: these
endpoints' interesting branch is the simulated one, and turning
`simulate_config` on in that suite would change how every config read it makes
behaves. So this spins up its own agent on its own port.

WHAT IS ACTUALLY BEING PROVEN

Not that a Drobo spins its drives down or that a DroboApp starts -- nothing here
can know that. What this proves is the contract the web page and the Windows
app are built against: that the spin-down read reports BOTH halves of the
state (the delay alone cannot say whether the feature is on), that an app
action's 200 carries the device's own re-read as the read-back, that every
refusal is a 400 with the reason in plain English before anything is sent, and
that the simulator is no more permissive than the SDK write it stands in for.
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
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk")))

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


tmp = tempfile.mkdtemp(prefix="droboapps-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
cfg_path = os.path.join(tmp, "config.json")
PORT = 7438
BASE = f"http://127.0.0.1:{PORT}"

with open(cfg_path, "w") as fh:
    json.dump({
        "agent": {"bind": "127.0.0.1", "port": PORT, "poll_seconds": 1},
        "drobo": {"driver": "mock", "simulate_config": True},
        "photos": {"enabled": False},
    }, fh)

cfg = config.load(cfg_path)
TOKEN = cfg["agent"]["token"]

mon = Monitor(cfg, notify.build(cfg["notify"]))
photos = PhotoStore(cfg["photos"])
photos.load()
mon.start()
httpd = api.serve(cfg, mon, photos)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(1.0)


def call(path, body=None, method=None, token=TOKEN):
    headers = {"X-Agent-Token": token} if token else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


driver = mon.driver
check("the simulated driver is the one under test",
      getattr(driver, "simulate_config", False) is True, type(driver).__name__)

try:
    print("\n== GET /api/spindown: both halves of the state ==")
    s, b = call("/api/spindown")
    check("spindown 200", s == 200, (s, b))
    check("reported as simulated", b.get("simulated") is True, b)
    check("enabled by default (the mock mirrors the real unit)",
          b.get("enabled") is True, b)
    check("delay is the real unit's default, 14", b.get("delay") == 14, b)
    check("the unconfirmed unit is admitted, not papered over",
          "never says what the unit is" in b.get("delay_note", ""), b.get("delay_note"))

    print("\n== the spindown read reflects the spindown write ==")
    s, b = call("/api/config/spindown", body={"enabled": False})
    check("disable accepted", s == 200 and b.get("ok"), (s, b))
    s, b = call("/api/spindown")
    check("read-back after disable: enabled false", b.get("enabled") is False, b)
    check("read-back after disable: delay zeroed (both halves move together)",
          b.get("delay") == 0, b)

    s, b = call("/api/config/spindown", body={"delay": 30})
    check("a new delay accepted", s == 200 and b.get("ok"), (s, b))
    s, b = call("/api/spindown")
    check("read-back after set: enabled true again", b.get("enabled") is True, b)
    check("read-back after set: delay 30", b.get("delay") == 30, b)

    print("\n== POST /api/droboapps/action: Start, with the re-read as read-back ==")
    s, b = call("/api/droboapps", token=TOKEN)
    pix = [a for a in b.get("apps", []) if a["name"] == "AlphaApp"]
    check("AlphaApp is in the simulated app list and stopped",
          pix and pix[0]["running"] is False, b.get("apps"))

    s, b = call("/api/droboapps/action", body={"name": "AlphaApp", "action": "Start"})
    check("Start 200 with ok", s == 200 and b.get("ok") is True, (s, b))
    check("reported as simulated", b.get("simulated") is True, b)
    check("read_back names the app and reports it running",
          b.get("read_back", {}).get("name") == "AlphaApp"
          and b.get("read_back", {}).get("running") is True, b.get("read_back"))
    check("the full re-read app list rides along (so a UI needs no extra fetch)",
          any(a["name"] == "AlphaApp" and a["running"]
              for a in b.get("apps", [])), b.get("apps"))
    check("the message reports the device's answer",
          "running" in b.get("message", ""), b.get("message"))

    s, b = call("/api/droboapps")
    check("a later /api/droboapps read agrees",
          [a["running"] for a in b["apps"] if a["name"] == "AlphaApp"] == [True], b)

    s, b = call("/api/droboapps/action", body={"name": "AlphaApp", "action": "Stop"})
    check("Stop 200 and read_back stopped",
          s == 200 and b.get("read_back", {}).get("running") is False, (s, b))

    print("\n== refusals are the caller's fault, said before anything is sent ==")
    s, b = call("/api/droboapps/action", body={"name": "AlphaApp", "action": "start"})
    check("lowercase 'start' refused with 400", s == 400, (s, b))
    check("-- naming the case-sensitivity", "case-sensitive" in b.get("error", ""), b)

    s, b = call("/api/droboapps/action", body={"name": "AlphaApp", "action": "Restart"})
    check("an uncaptured verb is refused with 400", s == 400, (s, b))

    s, b = call("/api/droboapps/action", body={"name": "NotInstalled", "action": "Start"})
    check("an unknown app is refused with 400", s == 400, (s, b))
    check("-- and the error lists what IS installed",
          "AlphaApp" in b.get("error", ""), b.get("error"))

    s, b = call("/api/droboapps/action", body={"action": "Start"})
    check("a missing name is refused with 400", s == 400, (s, b))

    s, b = call("/api/droboapps/action", body=b"{not json")
    check("malformed JSON is refused with 400", s == 400, (s, b))
    check("-- and says so", "bad JSON" in b.get("error", ""), b)

    s, b = call("/api/droboapps")
    check("no refused request changed anything",
          [a["running"] for a in b["apps"] if a["name"] == "AlphaApp"] == [False], b)

    print("\n== auth: 401s are clean even with a request body ==")
    # The 401 path must DRAIN the body before answering (see do_POST) --
    # without that, Windows aborts the keep-alive socket and the client sees
    # a connection error instead of this parseable answer.
    s, b = call("/api/droboapps/action",
                body={"name": "AlphaApp", "action": "Start"}, token="")
    check("unauthenticated POST answers a clean 401", s == 401, (s, b))
    check("-- with the reason in the body", "X-Agent-Token" in b.get("error", ""), b)
    s, b = call("/api/droboapps/action",
                body={"name": "AlphaApp", "action": "Start"}, token="wrong")
    check("wrong-token POST answers a clean 401", s == 401, (s, b))
    s, b = call("/api/spindown", token="")
    check("unauthenticated GET /api/spindown answers 401", s == 401, (s, b))
    s, b = call("/api/droboapps")
    check("...and none of those reached the driver",
          [a["running"] for a in b["apps"] if a["name"] == "AlphaApp"] == [False], b)

    print("\n== the write outcomes are not a share-only vocabulary ==")
    # Share writes get the same treatment in test_shares_api.py. This proves it
    # is _config_write's contract rather than something bolted onto shares:
    # spin-down, a General-block write, reports the same four outcomes -- and
    # the awkward one still moves the device while the answer stays silent.
    call("/api/config/spindown", body={"delay": 14})
    call("/api/mock/write/hangs_up", method="POST")
    s, b = call("/api/config/spindown", body={"delay": 21})
    check("an unacknowledged spin-down write is a success",
          s == 200 and b.get("ok") is True, (s, b))
    check("-- with the same outcome label the share writes use",
          b.get("outcome") == "verified_unacknowledged", b)
    check("-- and acknowledged false", b.get("acknowledged") is False, b)
    s, b = call("/api/spindown")
    check("-- and the device really did take the new delay", b.get("delay") == 21, b)

    call("/api/mock/write/unconfirmable", method="POST")
    s, b = call("/api/config/spindown", body={"delay": 30})
    check("an unsettleable spin-down write is 502 and labelled unknown",
          s == 502 and b.get("outcome") == "unknown", (s, b))
    check("-- worded as 'may have applied', never as 'nothing happened'",
          "may well have been" in b.get("error", ""), b.get("error"))
    s, b = call("/api/spindown")
    check("-- while the device did in fact take it, which is why it says that",
          b.get("delay") == 30, b)

    call("/api/mock/write/refused", method="POST")
    s, b = call("/api/config/spindown", body={"delay": 45})
    check("a refused spin-down write is 502 and labelled failed",
          s == 502 and b.get("outcome") == "failed", (s, b))
    s, b = call("/api/spindown")
    check("-- and the delay did not move", b.get("delay") == 30, b)
    call("/api/mock/reset", method="POST")

    print("\n== the simulator mutates per-instance state only ==")
    call("/api/droboapps/action", body={"name": "AlphaApp", "action": "Start"})
    fresh = MockDriver({"simulate_config": True})
    check("a fresh MockDriver still has AlphaApp stopped",
          [a["running"] for a in fresh.get_droboapps()["apps"]
           if a["name"] == "AlphaApp"] == [False])
    check("the class-level pristine table is untouched",
          [a["running"] for a in MockDriver._MOCK_DROBOAPPS["apps"]
           if a["name"] == "AlphaApp"] == [False])
finally:
    httpd.shutdown()
    mon.stop()
    httpd.server_close()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
