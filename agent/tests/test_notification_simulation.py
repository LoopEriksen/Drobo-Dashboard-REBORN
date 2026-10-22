"""
End-to-end simulation of every desktop-notification failure category.

    py test_notification_simulation.py

WHY THIS EXISTS ALONGSIDE test_agent.py AND THE C# NOTIFIER TESTS
------------------------------------------------------------------
Both halves are well tested in isolation: test_agent.py proves the agent raises
the right alert for each fault, and the C# AlertNotifier tests prove the four
notification rules hold for a hand-typed list of keys. What neither proves is
the SEAM: that the keys the *running agent actually emits* under each simulated
failure are exactly the keys the notifier routes into a category. The notifier
swallows any key it does not recognise -- by design, quiet is the safe way to
be wrong -- which means a new alert type added to the agent would be silently
un-notifiable, and the C# test would still pass because it only checks the keys
it already knows about.

So this test does two things the others cannot:

  1. Runs a REAL mock agent, injects every fault the API supports (drive
     fail/warn/pull, cache battery, PSU), and asserts each produces an alert
     whose key the notifier maps to the intended category, and that it clears
     on reset (the precondition for the "again if it returns" rule).
  2. Drives the categories the API cannot inject -- temperature, capacity,
     unreachable, pack health -- through monitor._evaluate_alerts() with
     constructed snapshots, and routes THOSE keys through the same
     category mapping.

The category mapping is a Python port of AlertNotifier.CategoryFor kept in
lockstep by test #3 below, which asserts the C# source still contains every
prefix rule this port relies on. Change one, the other test fails.
Test #3 also reads the ids of the C# Categories list, which the
app's Settings switches are built from, and requires them to be
exactly the categories this port routes to.
"""
import atexit
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk")))

from drobo_agent import api, config, notify  # noqa: E402
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402
from drobo_agent.models import (  # noqa: E402
    Snapshot, DeviceInfo, DriveSlot, Capacity, PackHealth,
    SEVERITY_CRITICAL, SEVERITY_WARNING,
)

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------------
# A faithful port of AlertNotifier.CategoryFor (windows/.../AlertNotifier.cs).
# Test #3 asserts the C# still contains each rule, so this cannot drift silently.
# ---------------------------------------------------------------------------
def category_for(key: str) -> str:
    if not key:
        return ""
    if key.endswith("-cleared"):
        return ""
    if key.startswith("drive-"):
        return "drives"
    if key.startswith("temp-") or key.startswith("chassis-temp-"):
        return "temperature"
    if key.startswith("capacity-"):
        return "capacity"
    if key.startswith("battery-"):
        return "battery"
    if key == "unreachable":
        return "unreachable"
    if key in ("double-degraded", "relayout") or key.startswith("pack-status-"):
        return "pack"
    if key.startswith("power-") or key.startswith("fan-"):
        return "hardware"
    return ""


# Every category category_for() can return. Test #3 requires these to be
# exactly the ids of AlertNotifier.Categories in the C# app.
CATEGORIES = {"drives", "temperature", "capacity", "battery",
              "unreachable", "pack", "hardware"}

# ---------------------------------------------------------------------------
# Part 1: a live mock agent, faults injected through the real API
# ---------------------------------------------------------------------------
print("== starting a real mock agent on a spare port ==")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
tmp = tempfile.mkdtemp(prefix="notif-sim-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
cfg_path = os.path.join(tmp, "config.json")
with open(cfg_path, "w") as fh:
    json.dump({
        "agent": {"bind": "127.0.0.1", "port": PORT, "poll_seconds": 1},
        # Explicit mock -- never inherit the driver default, which is the real one.
        "drobo": {"driver": "mock"},
        "photos": {"enabled": False},
        "alerts": {"resend_seconds": 2},
    }, fh)
cfg = config.load(cfg_path)
TOKEN = cfg["agent"]["token"]
BASE = f"http://127.0.0.1:{PORT}"

mon = Monitor(cfg, notify.build(cfg["notify"]))
photos = PhotoStore(cfg["photos"]); photos.load()
mon.start()
httpd = api.serve(cfg, mon, photos)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(1.5)


def call(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-Agent-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def active_keys():
    _, b = call("/api/alerts")
    return {a["key"] for a in b.get("alerts", [])}


def wait_for(pred, timeout=6.0):
    """Poll until pred() is truthy or the monitor has had several poll cycles."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.3)
    return pred()


check("agent is up and answering", call("/api/ping")[0] == 200)
baseline = active_keys()
check("healthy mock starts with no drive/battery/PSU alerts",
      not any(k.startswith(("drive-", "battery-")) for k in baseline), baseline)

# Every fault the API can inject, with the alert key it must produce and the
# notifier category that key must land in.
FAULTS = [
    ("drive fail (bay 3)",     "/api/mock/fail/3",         "drive-failed-3",  "drives",  SEVERITY_CRITICAL),
    ("drive warn (bay 2)",     "/api/mock/warn/2",         "drive-warning-2", "drives",  SEVERITY_WARNING),
    ("cache battery failed",   "/api/mock/battery/failed", "battery-failed",  "battery", SEVERITY_CRITICAL),
    ("cache battery warning",  "/api/mock/battery/warning","battery-warning", "battery", SEVERITY_WARNING),
]

for label, path, key, want_cat, want_sev in FAULTS:
    print(f"\n== simulate: {label} ==")
    st, _ = call(path, "POST")
    check(f"injection accepted ({path})", st == 200, st)
    check(f"alert '{key}' is raised", wait_for(lambda: key in active_keys()), active_keys())
    _, b = call("/api/alerts")
    row = next((a for a in b.get("alerts", []) if a["key"] == key), None)
    check(f"severity is {want_sev}", row is not None and row["severity"] == want_sev, row)
    check(f"notifier routes '{key}' -> '{want_cat}'", category_for(key) == want_cat, category_for(key))
    call("/api/mock/reset", "POST")
    check(f"'{key}' clears on reset (so 'again if it returns' can work)",
          wait_for(lambda: key not in active_keys()), active_keys())

# A pulled drive is a distinct physical event. The mock removes the drive, so
# there is no failed-drive alert to raise -- but the array is now short a disk.
# Prove exactly what the app WOULD say, rather than assuming.
print("\n== simulate: drive pulled (bay 4) ==")
call("/api/mock/pull/4", "POST")
time.sleep(2.5)
_, status = call("/api/status")
bay4 = next((d for d in status.get("drives", []) if d.get("bay") == 4), None)
check("the pulled bay reads as absent/empty in status",
      bay4 is not None and not bay4.get("present", True), bay4)
pulled_keys = active_keys()
pulled_notifiable = {k for k in pulled_keys if category_for(k)}
print(f"      alerts while pulled: {sorted(pulled_keys) or 'none'}")
print(f"      -> notifiable categories: {sorted({category_for(k) for k in pulled_notifiable}) or 'NONE'}")
call("/api/mock/reset", "POST")
time.sleep(1.5)

print("\n== simulate: PSU fault ==")
call("/api/mock/power/failed", "POST")
time.sleep(2.5)
_, status = call("/api/status")
psu_keys = active_keys()
psu_state = (status.get("power") or {}).get("state")
check("PSU fault is visible in status", psu_state in ("failed", "warning"), status.get("power"))
check("PSU fault raises 'power-failed'", "power-failed" in psu_keys, sorted(psu_keys))
check("notifier routes 'power-failed' -> 'hardware'", category_for("power-failed") == "hardware")
call("/api/mock/reset", "POST")
check("'power-failed' clears on reset", wait_for(lambda: "power-failed" not in active_keys()), active_keys())

httpd.shutdown()
mon.stop()

# ---------------------------------------------------------------------------
# Part 2: categories the API cannot inject, driven through the monitor directly
# ---------------------------------------------------------------------------
print("\n== the categories with no injection endpoint: temperature, capacity, unreachable, pack ==")


def make_snap(**over):
    """Same shape test_agent.py uses -- the models' real field names."""
    base = dict(
        reachable=True,
        source="sim",
        device=DeviceInfo(name="Sim", model="Drobo 5N"),
        capacity=Capacity(),
        drives=[DriveSlot(bay=1, present=True, state="ok", capacity_bytes=1)],
        pack_health=PackHealth(),
    )
    base.update(over)
    return Snapshot(**base)


mon2 = Monitor(cfg, notify.build(cfg["notify"]))


def keys_for(snap):
    with mon2._lock:
        return {a.key: a for a in mon2._evaluate_alerts(snap)}


acfg = cfg["alerts"]

hot = keys_for(make_snap(device=DeviceInfo(name="Sim", model="Drobo 5N",
                                           temperature_c=acfg["temperature_critical_c"] + 1)))
check("chassis over critical -> chassis-temp-critical", "chassis-temp-critical" in hot, list(hot))
check("...routes to 'temperature'", category_for("chassis-temp-critical") == "temperature")

warm = keys_for(make_snap(device=DeviceInfo(name="Sim", model="Drobo 5N",
                                            temperature_c=acfg["temperature_warning_c"] + 1)))
check("chassis over warning -> chassis-temp-warning", "chassis-temp-warning" in warm, list(warm))
check("...routes to 'temperature'", category_for("chassis-temp-warning") == "temperature")

# Capacity: usable 100, used 98 -> 98%. The mock has no pack_health thresholds,
# so the config defaults (0.85 warning / 0.95 critical) apply.
full = keys_for(make_snap(capacity=Capacity(usable_bytes=100, used_bytes=98)))
check("98% used -> capacity-critical", "capacity-critical" in full, list(full))
check("...routes to 'capacity'", category_for("capacity-critical") == "capacity")

warnfull = keys_for(make_snap(capacity=Capacity(usable_bytes=100, used_bytes=88)))
check("88% used -> capacity-warning", "capacity-warning" in warnfull, list(warnfull))

down = keys_for(make_snap(reachable=False, error="connection refused"))
check("unreachable device -> 'unreachable' alert", "unreachable" in down, list(down))
check("...routes to 'unreachable'", category_for("unreachable") == "unreachable")

dd = keys_for(make_snap(pack_health=PackHealth(double_degraded_count=1)))
check("double-degraded -> alert", "double-degraded" in dd, list(dd))
check("...routes to 'pack'", category_for("double-degraded") == "pack")

errs = keys_for(make_snap(drives=[DriveSlot(bay=1, present=True, state="ok",
                                            capacity_bytes=1, error_count=3)]))
check("drive error_count > 0 -> drive-errors-1", "drive-errors-1" in errs, list(errs))
check("...routes to 'drives'", category_for("drive-errors-1") == "drives")

# ---------------------------------------------------------------------------
# Part 3: EVERY key the monitor can emit must land in a category -- and this
# is derived from monitor.py's SOURCE, not a hand-typed list, so a new alert
# added tomorrow fails here instead of being silently un-notifiable.
# ---------------------------------------------------------------------------
print("\n== every alert key the monitor source can emit maps to a notifier category ==")
mon_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drobo_agent", "monitor.py"), encoding="utf-8").read()
# Literal keys and f-string prefixes: seen["key"] and seen[f"prefix-{...}"]
literal = set(re.findall(r'seen\["([a-z0-9-]+)"\]', mon_src))
fstring = set(re.findall(r'seen\[f"([a-z0-9-]+)\{', mon_src))
emitted = literal | {p + "X" for p in fstring}  # give prefixes a fake suffix
check("found alert keys in monitor source", len(emitted) >= 10, sorted(emitted))
unrouted = sorted(k for k in emitted if not category_for(k) and not k.endswith("-cleared"))
check("no monitor alert key is silently un-notifiable", not unrouted, unrouted)

# The Python port must match the C# it mirrors. Assert every prefix rule this
# test relies on is literally present in AlertNotifier.cs.
cs_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "windows", "DroboDashboardReborn", "AlertNotifier.cs")
cs = open(cs_path, encoding="utf-8").read()
for rule in ('"drive-"', '"temp-"', '"chassis-temp-"', '"capacity-"', '"battery-"',
             '"unreachable"', '"double-degraded"', '"relayout"', '"pack-status-"', '"-cleared"',
             '"power-"', '"fan-"'):
    check(f"C# notifier still contains the {rule} rule this port mirrors", rule in cs)

# The categories themselves must match too. The app builds its Settings
# switches from AlertNotifier.Categories and loads the saved settings
# only for the ids in it, and a category with no entry there counts as
# off. So a category this port routes to but the C# list has dropped
# could never be switched on, and its alerts would never notify.
categories_block = re.search(r"\bCategories\s*=\s*\{(.*?)\};", cs, re.S)
cs_category_ids = (
    re.findall(r'\(\s*"([a-z-]+)"\s*,', categories_block.group(1))
    if categories_block else [])
check("found the category ids of the C# Categories list",
      bool(cs_category_ids), cs_category_ids)
missing_from_cs = sorted(CATEGORIES - set(cs_category_ids))
check("every category this port routes to is a row of the C# list",
      not missing_from_cs, missing_from_cs)
unknown_to_port = sorted(set(cs_category_ids) - CATEGORIES)
check("the C# list has no category this port never routes to",
      not unknown_to_port, unknown_to_port)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
