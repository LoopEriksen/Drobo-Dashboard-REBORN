"""
End-to-end test of Identify through the agent,
against the simulated Drobo.

    py test_identify_api.py

WHY THIS IS A SEPARATE FILE FROM test_agent.py

test_agent.py runs its Monitor with the default driver and
`simulate_config` off, which is right for what it tests. Identify's
interesting branch is the simulated one, and turning `simulate_config`
on there would change how every config read in that suite behaves. So
this spins up its own agent on its own port instead of bending the other
one.

WHAT IS ACTUALLY BEING PROVEN

Not "does the Drobo blink" -- nothing here can know that. What this
proves is that the value a caller asks for arrives intact at the driver,
that the simulated path enforces the same rules as the hardware path,
and that a bad interval is reported as the caller's mistake rather than
as a fault in somebody's storage array.

That last one matters more than it looks. `IdentifyParameterError`
subclasses `IdentifyError`, so an `except IdentifyError` written before
it existed will swallow it and answer 502 -- telling the operator their
Drobo refused a command when in fact they made a typo. The ordering test
below is the guard against that regression.
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
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402
from drobo_nasd import identify  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


temp_dir = tempfile.mkdtemp(prefix="identify-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left
# this folder behind in TEMP.
atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
config_path = os.path.join(temp_dir, "config.json")
PORT = 7437
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


def post(path):
    request = urllib.request.Request(BASE_URL + path, data=b"",
                                     headers={"X-Agent-Token": TOKEN},
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as http_error:
        return http_error.code, json.loads(http_error.read())


driver = monitor.driver
check("the simulated driver is the one under test",
      getattr(driver, "simulate_config", False) is True, type(driver).__name__)

try:
    print("\n== the interval reaches the driver ==")

    status, reply = post("/api/identify")
    check(
        "a plain identify succeeds",
        status == 200 and reply.get("ok"),
        (status, reply),
    )
    check("it is reported as simulated", reply.get("simulated") is True, reply)
    check("the default interval is applied, not left empty",
          reply.get("interval") == identify.DEFAULT_INTERVAL, reply)
    check("the driver actually received it",
          driver._identify_last_interval == identify.DEFAULT_INTERVAL,
          driver._identify_last_interval)
    # The reply credited the original Dashboard with the value just
    # sent until 2026-10-29. It sent 900; the default is 15.
    message = reply.get("message", "")
    check("the reply does not credit the original Dashboard with 15",
          "used this value" not in message
          and "sent 900" in message
          and f"interval {identify.DEFAULT_INTERVAL}." in message,
          message)

    status, reply = post("/api/identify?interval=45")
    check("a chosen interval is accepted", status == 200, (status, reply))
    check("...and arrives at the driver unchanged",
          driver._identify_last_interval == 45, driver._identify_last_interval)

    # Zero is the stop guess, and it is falsy -- exactly the value an
    # `if interval:` somewhere in the chain would quietly turn into
    # "unset".
    status, reply = post("/api/identify?interval=0")
    check(
        "zero is accepted rather than treated as absent",
        status == 200,
        (status, reply),
    )
    check("zero arrives as zero, not as the default",
          driver._identify_last_interval == 0, driver._identify_last_interval)
    check("...and is reported back as zero", reply.get("interval") == 0, reply)

    # The control case: what this module sent for months.
    status, reply = post("/api/identify?interval=none")
    check("'none' is accepted", status == 200, (status, reply))
    check(
        "'none' reaches the driver as None, not the string",
        driver._identify_last_interval is None,
        driver._identify_last_interval,
    )

    print("\n== a bad interval is the caller's fault, not the Drobo's ==")
    identify_count_before = driver._identify_count
    for bad_interval, reason in (("-1", "negative"), ("abc", "not a number"),
                                 (str(identify.MAX_INTERVAL + 1),
                                  "beyond the sanity limit")):
        status, reply = post(f"/api/identify?interval={bad_interval}")
        check(f"{reason} is refused with 400", status == 400,
              (bad_interval, status, reply))
        check(f"{reason} says why", "error" in reply, reply)
    check("no refused request reached the driver",
          driver._identify_count == identify_count_before,
          (identify_count_before, driver._identify_count))

    print("\n== the simulator is not more permissive than the hardware ==")
    # If these two ever disagree, the simulator starts certifying
    # intervals that a real Drobo would reject.
    for value in (-1, identify.MAX_INTERVAL + 1, "15", 15.0, True):
        sdk_refuses = False
        try:
            identify.validate_interval(value)
        except identify.IdentifyParameterError:
            sdk_refuses = True
        check(f"the SDK refuses {value!r}", sdk_refuses)
    for value in (None, 0, 15, identify.MAX_INTERVAL):
        try:
            identify.validate_interval(value)
            check(f"the SDK allows {value!r}", True)
        except identify.IdentifyParameterError as exc:
            check(f"the SDK allows {value!r}", False, str(exc))

    check("IdentifyParameterError is catchable as IdentifyError",
          issubclass(identify.IdentifyParameterError, identify.IdentifyError))
    check("...and as ValueError, for callers that never heard of this module",
          issubclass(identify.IdentifyParameterError, ValueError))
finally:
    http_server.shutdown()
    monitor.stop()
    http_server.server_close()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
