"""
The configuration-write routes, through HTTP, against the simulated driver.

/api/config/brightness, /api/config/spindown and /api/backup/plan had no
API-level test at all (found by the 2026-09-01 audit): the SDK paths under
them are covered in sdk/tests, but nothing checked that a bad request body
comes back as a 400 rather than a 500, that the token gate holds, or that a
simulated write reports itself as simulated. That is what this does. Same
shape as test_shares_api.py: a real agent on its own port, plain urllib.
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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the agent/ folder
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "sdk"))

from drobo_agent import api, config, notify  # noqa: E402
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402
from drobo_nasd.droboapps import _DIMMING_MIN, _DIMMING_MAX, _SPINDOWN_MAX  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


tmp = tempfile.mkdtemp(prefix="config-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
cfg_path = os.path.join(tmp, "config.json")
PORT = 7440
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
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"_raw": raw.decode("utf-8", "replace")}


try:
    print("\n== the token gate holds on every write route ==")
    for path, body in (("/api/config/brightness", {"value": _DIMMING_MAX}),
                       ("/api/config/spindown", {"enabled": False}),
                       ("/api/backup/plan", {"source": tmp, "destination": tmp})):
        s, b = call(path, body=body, token="")
        check(f"{path} without a token is 401", s == 401, (s, b))

    print("\n== brightness: bad bodies are 400s, never 500s ==")
    s, b = call("/api/config/brightness", body=b"{not json")
    check("unparseable JSON is 400", s == 400 and "error" in b, (s, b))
    s, b = call("/api/config/brightness", body={})
    check("missing value is 400", s == 400 and "error" in b, (s, b))
    s, b = call("/api/config/brightness", body={"value": True})
    check("a boolean is not a whole number -> 400", s == 400, (s, b))
    s, b = call("/api/config/brightness", body={"value": "50"})
    check("a string is not a whole number -> 400", s == 400, (s, b))
    s, b = call("/api/config/brightness", body={"value": _DIMMING_MAX + 1000})
    check("out of range is refused with a structured error, not a 500",
          s != 500 and "error" in b, (s, b))

    print("\n== brightness: a valid write reports itself as simulated ==")
    s, b = call("/api/config/brightness", body={"value": _DIMMING_MIN})
    check("valid write is 200 ok", s == 200 and b.get("ok") is True, (s, b))
    check("reported as simulated", b.get("simulated") is True, b)
    check("the message says what was set",
          "brightness" in str(b.get("message", "")).lower(), b)

    print("\n== spin-down: the bare-zero refusal reaches the API ==")
    s, b = call("/api/config/spindown", body={"delay": 0})
    check("delay 0 is 400 (the device coerces it; disable must be explicit)",
          s == 400 and "error" in b, (s, b))
    s, b = call("/api/config/spindown", body={"delay": -5})
    check("negative delay is 400", s == 400, (s, b))
    s, b = call("/api/config/spindown", body={"delay": "30"})
    check("string delay is 400", s == 400, (s, b))
    s, b = call("/api/config/spindown", body={})
    check("empty body is 400", s == 400, (s, b))

    print("\n== spin-down: disable and enable both succeed simulated ==")
    s, b = call("/api/config/spindown", body={"enabled": False})
    check("disable is 200 ok simulated",
          s == 200 and b.get("ok") is True and b.get("simulated") is True, (s, b))
    delay = min(30, _SPINDOWN_MAX)
    s, b = call("/api/config/spindown", body={"delay": delay})
    check(f"delay {delay} is 200 ok simulated",
          s == 200 and b.get("ok") is True and b.get("simulated") is True, (s, b))
    s, b = call("/api/spindown")
    check("GET /api/spindown answers 200 afterwards", s == 200, (s, b))

    print("\n== backup/plan: bad plans are structured errors, and a plan copies nothing ==")
    s, b = call("/api/backup/plan", body=b"nope")
    check("unparseable JSON is 400", s == 400 and "error" in b, (s, b))
    # A plan is a dry run that REPORTS refusals rather than erroring: the
    # caller reads `allowed` and `refusals`. So an empty plan is a 200 that
    # says no, and the thing to pin is that it says no.
    s, b = call("/api/backup/plan", body={})
    check("empty source/destination plans as not allowed",
          s == 200 and b.get("allowed") is False and b.get("refusals"), (s, b))
    check("the refusals name both missing paths",
          any("source" in r.lower() for r in b.get("refusals", []))
          and any("destination" in r.lower() for r in b.get("refusals", [])), b)
    src = os.path.join(tmp, "src")
    dst = os.path.join(tmp, "dst")
    os.makedirs(src)
    with open(os.path.join(src, "a.txt"), "w") as fh:
        fh.write("x")
    s, b = call("/api/backup/plan", body={"source": src, "destination": dst})
    check("a real plan is 200", s == 200, (s, b))
    check("planning copies nothing", not os.path.exists(os.path.join(dst, "a.txt")))
finally:
    httpd.shutdown()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
