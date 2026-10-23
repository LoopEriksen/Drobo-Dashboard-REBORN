"""
The configuration-write routes, through HTTP,
against the simulated driver.

/api/config/brightness, /api/config/spindown and /api/backup/plan had no
API-level test at all (found by the 2026-09-01 audit): the SDK paths
under them are covered in sdk/tests, but nothing checked that a bad
request body comes back as a 400 rather than a 500, that the token gate
holds, or that a simulated write reports itself as simulated. That is
what this does. Same shape as test_shares_api.py: a real agent on its
own port, plain urllib.
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

AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)  # the agent/ folder
sys.path.insert(0, AGENT_DIR)
sys.path.insert(0, os.path.join(AGENT_DIR, "..", "sdk"))

from drobo_agent import api, config, notify  # noqa: E402
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402
from drobo_nasd.droboapps import (  # noqa: E402
    _DIMMING_MIN,
    _DIMMING_MAX,
    _SPINDOWN_MAX,
)  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


temp_dir = tempfile.mkdtemp(prefix="config-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left
# this folder behind in TEMP.
atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
config_path = os.path.join(temp_dir, "config.json")
PORT = 7440
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
        raw = http_error.read()
        try:
            return http_error.code, json.loads(raw)
        except ValueError:
            return http_error.code, {"_raw": raw.decode("utf-8", "replace")}


try:
    print("\n== the token gate holds on every write route ==")
    for path, request_body in (("/api/config/brightness",
                                {"value": _DIMMING_MAX}),
                               ("/api/config/spindown", {"enabled": False}),
                               ("/api/backup/plan",
                                {"source": temp_dir,
                                 "destination": temp_dir})):
        status, reply = call(path, body=request_body, token="")
        check(f"{path} without a token is 401", status == 401, (status, reply))

    print("\n== brightness: bad bodies are 400s, never 500s ==")
    status, reply = call("/api/config/brightness", body=b"{not json")
    check("unparseable JSON is 400", status == 400 and "error" in reply,
          (status, reply))
    status, reply = call("/api/config/brightness", body={})
    check("missing value is 400", status == 400 and "error" in reply,
          (status, reply))
    status, reply = call("/api/config/brightness", body={"value": True})
    check("a boolean is not a whole number -> 400", status == 400,
          (status, reply))
    status, reply = call("/api/config/brightness", body={"value": "50"})
    check("a string is not a whole number -> 400", status == 400,
          (status, reply))
    status, reply = call("/api/config/brightness",
                         body={"value": _DIMMING_MAX + 1000})
    check("out of range is refused with a structured error, not a 500",
          status != 500 and "error" in reply, (status, reply))

    print("\n== brightness: a valid write reports itself as simulated ==")
    status, reply = call("/api/config/brightness",
                         body={"value": _DIMMING_MIN})
    check("valid write is 200 ok", status == 200 and reply.get("ok") is True,
          (status, reply))
    check("reported as simulated", reply.get("simulated") is True, reply)
    check("the message says what was set",
          "brightness" in str(reply.get("message", "")).lower(), reply)

    print("\n== spin-down: the bare-zero refusal reaches the API ==")
    status, reply = call("/api/config/spindown", body={"delay": 0})
    check("delay 0 is 400 (the device coerces it; disable must be explicit)",
          status == 400 and "error" in reply, (status, reply))
    status, reply = call("/api/config/spindown", body={"delay": -5})
    check("negative delay is 400", status == 400, (status, reply))
    status, reply = call("/api/config/spindown", body={"delay": "30"})
    check("string delay is 400", status == 400, (status, reply))
    status, reply = call("/api/config/spindown", body={})
    check("empty body is 400", status == 400, (status, reply))

    print("\n== spin-down: disable and enable both succeed simulated ==")
    status, reply = call("/api/config/spindown", body={"enabled": False})
    check(
        "disable is 200 ok simulated",
        status == 200
        and reply.get("ok") is True
        and reply.get("simulated") is True,
        (status, reply),
    )
    delay = min(30, _SPINDOWN_MAX)
    status, reply = call("/api/config/spindown", body={"delay": delay})
    check(
        f"delay {delay} is 200 ok simulated",
        status == 200
        and reply.get("ok") is True
        and reply.get("simulated") is True,
        (status, reply),
    )
    status, reply = call("/api/spindown")
    check("GET /api/spindown answers 200 afterwards", status == 200,
          (status, reply))

    print(
        "\n== backup/plan: bad plans are structured errors, "
        "and a plan copies nothing =="
    )
    status, reply = call("/api/backup/plan", body=b"nope")
    check("unparseable JSON is 400", status == 400 and "error" in reply,
          (status, reply))
    # A plan is a dry run that REPORTS refusals rather than erroring:
    # the caller reads `allowed` and `refusals`. So an empty plan is a
    # 200 that says no, and the thing to pin is that it says no.
    status, reply = call("/api/backup/plan", body={})
    check("empty source/destination plans as not allowed",
          status == 200 and reply.get("allowed") is False
          and reply.get("refusals"), (status, reply))
    check(
        "the refusals name both missing paths",
        any(
            "source" in refusal.lower()
            for refusal in reply.get("refusals", [])
        )
        and any(
            "destination" in refusal.lower()
            for refusal in reply.get("refusals", [])
        ),
        reply,
    )
    source_dir = os.path.join(temp_dir, "src")
    destination_dir = os.path.join(temp_dir, "dst")
    os.makedirs(source_dir)
    with open(os.path.join(source_dir, "a.txt"), "w") as source_file:
        source_file.write("x")
    status, reply = call("/api/backup/plan",
                         body={"source": source_dir,
                               "destination": destination_dir})
    check("a real plan is 200", status == 200, (status, reply))
    check(
        "planning copies nothing",
        not os.path.exists(os.path.join(destination_dir, "a.txt")),
    )
finally:
    http_server.shutdown()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
