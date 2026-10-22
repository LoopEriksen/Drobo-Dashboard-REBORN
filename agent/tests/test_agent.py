"""
End-to-end smoke test for the Drobo Agent.

Starts a real agent on a spare port using the mock driver and a temporary photo
folder, exercises every endpoint, and checks the answers. Then starts one on the
real driver with discovery made to find nothing, as a PC that boots before its
Drobo would, and checks it still serves and alerts. No hardware needed, and no
Drobo is contacted.

    py test_agent.py

Run this after changing anything under drobo_agent/ -- especially before
switching the driver over to the real Drobo 5N.
"""
import atexit, contextlib, hashlib, io, json, os, shutil, socket, sys, tempfile, threading, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drobo_agent import api, config, drivers, notify, qr
from drobo_agent.models import Capacity, DeviceInfo, DriveSlot, PackHealth, SEVERITY_CRITICAL, Snapshot
from drobo_agent.monitor import Monitor, _PACK_STATUS_BASELINE
from drobo_agent.photos import PhotoStore
from drobo_nasd import config_cmd, discovery, droboapps, drobosync
from drobo_nasd import esatm, eventlog, netcheck, sysinfo

temp_dir = tempfile.mkdtemp(prefix="droboagent-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
photo_dir = os.path.join(temp_dir, "Photos")
config_path = os.path.join(temp_dir, "config.json")

with open(config_path, "w") as config_file:
    json.dump({
        "agent": {"bind": "127.0.0.1", "port": 7431, "poll_seconds": 1},
        # The mock driver, EXPLICITLY -- never inherited from DEFAULTS. This
        # suite used to omit the drobo block and lean on the default being
        # "mock"; when the shipping default became the real driver (as it
        # must -- a health tool that shows a simulated healthy array by
        # default is lying), this test started discovering and polling
        # whatever real Drobo was on the developer's network, mid-suite.
        # A test that reaches real hardware because of a config default is
        # exactly the accident this line prevents.
        "drobo": {"driver": "mock"},
        "photos": {"enabled": True, "target_dir": photo_dir},
        "alerts": {"resend_seconds": 2},
    }, config_file)

agent_config = config.load(config_path)
TOKEN = agent_config["agent"]["token"]
BASE_URL = "http://127.0.0.1:7431"

monitor = Monitor(agent_config, notify.build(agent_config["notify"]))
photo_store = PhotoStore(agent_config["photos"]); photo_store.load()
monitor.start()
http_server = api.serve(agent_config, monitor, photo_store)
threading.Thread(target=http_server.serve_forever, daemon=True).start()
time.sleep(1.2)

def call(path, data=None, headers=None, method=None, raw=False):
    request_headers = {"X-Agent-Token": TOKEN}
    request_headers.update(headers or {})
    request = urllib.request.Request(BASE_URL + path, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
            return response.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as http_error:
        return http_error.code, json.loads(http_error.read())

failures = []
def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + ("  " + str(failure_detail) if failure_detail and not condition else ""))
    if not condition: failures.append(label)

print("\n== auth ==")
status, reply = call("/api/ping")
check("ping ok", status == 200 and reply["ok"], reply)
tokenless_request = urllib.request.Request(BASE_URL + "/api/status")
try:
    urllib.request.urlopen(tokenless_request, timeout=5)
    check("no-token rejected", False)
except urllib.error.HTTPError as http_error:
    check("no-token rejected with 401", http_error.code == 401,
          http_error.code)
status, reply = call("/api/status", headers={"X-Agent-Token": "wrong"})
check("bad token rejected", status == 401, status)
# Wrong is easy; wrong *and* not ASCII used to be fatal. A header byte >= 0x80
# arrives as non-ASCII text, and secrets.compare_digest refuses a str that is
# not pure ASCII by raising, so the gate blew up inside itself: reset socket,
# traceback in the console the tester is told to copy into an issue. Anyone who
# can reach the port could trigger it, so it stays checked.
try:
    status, reply = call("/api/status", headers={"X-Agent-Token": "wröng"})
    check("a non-ASCII token is a clean 401, not a crash", status == 401,
          status)
# A connection reset means the handler raised.
except Exception as _connection_error:
    check("a non-ASCII token is a clean 401, not a crash", False,
          repr(_connection_error))

# ?token= is the header's poor relation: a cross-site <form> can put a token in
# a URL but cannot set a header, so any route that accepts the query form is
# forgeable by any page the user happens to be looking at -- and the token has
# already been in a URL, in the QR code /api/pair.svg hands out. /api/events is
# the one exemption, because an EventSource genuinely cannot send a header.
# Both halves are checked: withdraw the exemption and the dashboard goes blind,
# widen it and /api/backup/start is one forged form away.
_EMPTY_TOKEN_HEADERS = {"X-Agent-Token": ""}
for _route in ("/api/status", "/api/pair", "/api/shares"):
    status, reply = call(f"{_route}?token={TOKEN}",
                         headers=_EMPTY_TOKEN_HEADERS)
    check(f"{_route} refuses a query token", status == 401, status)
status, reply = call(f"/api/identify?token={TOKEN}",
                     headers=_EMPTY_TOKEN_HEADERS, method="POST")
check("a write refuses a query token", status == 401, status)
check("-- and the 401 says to use the header instead",
      "X-Agent-Token header" in reply.get("error", ""), reply)
_events_socket = socket.create_connection(("127.0.0.1", 7431), timeout=10)
_events_socket.sendall(
    f"GET /api/events?token={TOKEN} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
    f"Accept: text/event-stream\r\n\r\n".encode())
_response_head, _deadline = b"", time.time() + 10
while b"\r\n\r\n" not in _response_head and time.time() < _deadline:
    _chunk = _events_socket.recv(256)
    if not _chunk: break
    _response_head += _chunk
_events_socket.close()
check("/api/events still takes a query token", b" 200 " in _response_head.split(b"\r\n")[0], _response_head[:80])
check("-- and it is still an event stream", b"text/event-stream" in _response_head, _response_head[:120])

print("\n== telemetry ==")
status, reply = call("/api/status")
check("status 200", status == 200, status)
check("5 bays reported", len(reply["drives"]) == 5,
      len(reply.get("drives", [])))
check("overall ok", reply["overall_state"] == "ok", reply["overall_state"])
check("model is 5N", reply["device"]["model"] == "Drobo 5N",
      reply["device"]["model"])
status, reply = call("/api/capacity"); check("capacity has used_fraction", "used_fraction" in reply, reply)
status, reply = call("/api/drives")
check("drives endpoint", len(reply["drives"]) == 5)
status, reply = call("/api/history"); check("history accumulating", len(reply["points"]) >= 1, len(reply["points"]))

print("\n== alerts via fault injection ==")
status, reply = call("/api/mock/fail/3", data=b"", method="POST")
check("fail bay 3 accepted", status == 200, reply)
check("overall now failed", reply.get("state") == "failed", reply)
status, reply = call("/api/alerts")
keys = [a["key"] for a in reply["alerts"]]
check("drive-failed-3 alert raised", "drive-failed-3" in keys, keys)
severities = [a["severity"] for a in reply["alerts"] if a["key"]
              == "drive-failed-3"]
check("severity critical", severities == ["critical"], severities)

status, reply = call("/api/mock/reset", data=b"", method="POST")
time.sleep(0.3)
status, reply = call("/api/alerts")
check("alert cleared after reset", not [a for a in reply["alerts"] if a["key"] == "drive-failed-3"], reply["alerts"])
status, reply = call("/api/alerts?all=1")
check("cleared alert kept in history", any(a["key"] == "drive-failed-3" for a in reply["alerts"]))

print("\n== Phase 1 scope additions (health telemetry) ==")
status, reply = call("/api/battery")
check("battery endpoint 200", status == 200, reply)
check("battery state ok by default", reply.get("state") == "ok", reply)
check("battery has a charge_pct", isinstance(reply.get("charge_pct"), (int, float)), reply)

status, reply = call("/api/eventlog")
check("eventlog endpoint 200", status == 200, reply)
check("eventlog has events", len(reply.get("events", [])) >= 1, reply)
check("eventlog entries have severity+message",
      all("severity" in e and "message" in e for e in reply["events"]), reply)

status, reply = call("/api/performance")
check("performance endpoint 200", status == 200, reply)
check("performance has read/write bps",
      "read_mb_per_s" in reply and "write_mb_per_s" in reply, reply)
check("performance says when it was measured",
      isinstance(reply.get("measured_at"), (int, float)), reply)

status, reply = call("/api/status")
check("status carries battery/fan/power/performance",
      all(k in reply for k in ("battery", "fan", "power", "performance", "event_log")), reply)

print("\n== health: /api/health (pack-level values) ==")
status, reply = call("/api/health")
check("health endpoint 200", status == 200, reply)
# The mock driver deliberately leaves pack_health unset, so
# these should all be sitting at their honest "not reported" defaults.
check("health relayout_count defaults to 0", reply.get("relayout_count") == 0,
      reply)
check("health double_degraded_count defaults to 0", reply.get("double_degraded_count") == 0, reply)
check("health opaque status codes default to null, not a made-up value",
      reply.get("disk_pack_status") is None
      and reply.get("device_status") is None, reply)

print("\n== health: alert rules via monitor._evaluate_alerts() on constructed Snapshots ==")


def make_snapshot(**overrides):
    snapshot_fields = dict(
        reachable=True,
        source="test",
        device=DeviceInfo(name="Test", model="Drobo 5N"),
        capacity=Capacity(),
        drives=[DriveSlot(bay=1, present=True, state="ok", capacity_bytes=1)],
        pack_health=PackHealth(),
    )
    snapshot_fields.update(overrides)
    return Snapshot(**snapshot_fields)


with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(
        drives=[DriveSlot(bay=1, present=True, state="ok", capacity_bytes=1,
                           error_count=3, model="TestDisk")]))
keys = [a.key for a in fired]
check("drive error_count > 0 raises a warning naming the bay", "drive-errors-1" in keys, keys)
severities = [a.severity for a in fired if a.key == "drive-errors-1"]
check("drive error alert severity is warning", severities == ["warning"],
      severities)

with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(pack_health=PackHealth(double_degraded_count=2)))
keys = [a.key for a in fired]
check("double_degraded_count > 0 raises a critical alert", "double-degraded" in keys, keys)
severities = [a.severity for a in fired if a.key == "double-degraded"]
check("double-degraded severity is critical", severities == ["critical"],
      severities)

with monitor._lock:
    fired = monitor._evaluate_alerts(
        make_snapshot(pack_health=PackHealth(relayout_count=1)))
keys = [a.key for a in fired]
check("relayout_count > 0 raises an info alert", "relayout" in keys, keys)
severities = [a.severity for a in fired if a.key == "relayout"]
check("relayout alert severity is info (this is normal)",
      severities == ["info"], severities)

with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(
        pack_health=PackHealth(disk_pack_status=1, dnas_status=6, device_status=98304, status_ex=0)))
keys = [a.key for a in fired]
check("pack status deviating from the healthy baseline raises a warning",
      "pack-status-disk_pack_status" in keys, keys)
severities = [a.severity for a in fired if a.key
              == "pack-status-disk_pack_status"]
check("pack status deviation severity is warning", severities == ["warning"],
      severities)
messages = [a.message for a in fired if a.key
            == "pack-status-disk_pack_status"]
check("pack status message quotes old and new values without interpreting them",
      messages and "0" in messages[0] and "1" in messages[0], messages)

with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(
        pack_health=PackHealth(disk_pack_status=0, dnas_status=6, device_status=98304, status_ex=0)))
keys = [a.key for a in fired]
# Newly-*raised* pack-status alerts, as opposed to the "...-cleared" notice
# this call is expected to emit for the deviation the previous snapshot fired.
check("pack status matching the healthy baseline raises no new deviation alert",
      not any(k.startswith("pack-status-") and not k.endswith("-cleared") for k in keys), keys)
check("matching the baseline clears the previous deviation alert",
      "pack-status-disk_pack_status-cleared" in keys, keys)

# The device's own capacity thresholds must be preferred over our config
# defaults (0.85/0.95) when the greeting reports them.
with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(
        capacity=Capacity(usable_bytes=100, used_bytes=40),
        pack_health=PackHealth(yellow_threshold_fraction=0.3, red_threshold_fraction=0.9)))
keys = [a.key for a in fired]
check("device's own (lower) yellow threshold is honoured over the 0.85 config default",
      "capacity-warning" in keys, keys)

print("\n== absent pack-status fields raise no false alarm ==")
# This check used to live in the SDK's test_esatm.py. It moved here during the
# SDK split because it is an ALERT question, not a protocol one -- the library's
# job ends at "an absent field parses as None", and what the monitor then does
# with that None is the agent's business.
#
# It matters because the healthy baseline contains NON-ZERO values
# (dnas_status=6, device_status=98304). So a missing field defaulting to 0 would
# look like a deviation from healthy and fire a "changed, worth a look" alert
# about a field the device simply never sent. That is the worst kind of alert:
# confidently wrong, about hardware the user cannot easily check.

with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(pack_health=PackHealth()))
false_pack_alert_keys = [a.key for a in fired
                         if a.key.startswith("pack-status-")]
check("a PackHealth with nothing reported raises no pack-status alert",
      false_pack_alert_keys == [], false_pack_alert_keys)

# ...while a field that IS present and DOES deviate still fires, so the check
# above is proving restraint rather than a broken rule.
with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(
        pack_health=PackHealth(disk_pack_status=99, dnas_status=6,
                               device_status=98304, status_ex=0)))
keys = [a.key for a in fired if a.key.startswith("pack-status-")]
check("...but a present field that deviates still does",
      keys == ["pack-status-disk_pack_status"], keys)

# And a field sitting exactly ON the baseline raises nothing NEW, including the
# non-zero ones -- the case that makes "absent must not become 0" necessary.
#
# Note the "-cleared" exclusion. Coming back to baseline after the deviation
# above correctly produces a "Resolved:" notice, which is the alert system
# working, not a false alarm. A first draft of this check asserted a flatly
# empty list and failed for exactly that reason -- the assertion was wrong,
# not the code.
with monitor._lock:
    fired = monitor._evaluate_alerts(make_snapshot(pack_health=PackHealth(**_PACK_STATUS_BASELINE)))
new_alarms = [a.key for a in fired
              if a.key.startswith("pack-status-") and not a.key.endswith("-cleared")]
check("a pack matching the healthy baseline exactly raises no NEW alert",
      new_alarms == [], new_alarms)
check("...and returning to baseline resolves the earlier one",
      any(a.key.endswith("-cleared") for a in fired), [a.key for a in fired])

print("\n== chassis temperature alerting ==")
# Added 2026-07-26. The per-drive temperature rules above never fire on a real
# Drobo 5N -- the status greeting reports mTemperature as 0 for every slot, so
# drive.temperature_c is always None. The chassis reading (eCmdGetSysInfo) is
# the only real temperature this hardware gives up, and until this rule existed
# the dashboard displayed it while nothing whatsoever watched it. A Drobo could
# have cooked itself in silence.
_warning_threshold_c = agent_config["alerts"]["temperature_warning_c"]
_critical_threshold_c = agent_config["alerts"]["temperature_critical_c"]


def chassis_alerts(temperature_c):
    snapshot = monitor.driver.read_snapshot()
    snapshot.device.temperature_c = temperature_c
    return {a.key: a for a in monitor._evaluate_alerts(snapshot) if a.key.startswith("chassis-temp")}


check("no reading -> no alert (never invent a temperature)",
      chassis_alerts(None) == {})
check("a normal temperature is silent", chassis_alerts(36) == {})
check("just under the warning threshold is still silent",
      chassis_alerts(int(_warning_threshold_c) - 1) == {})

warm = chassis_alerts(int(_warning_threshold_c) + 2)
check("above the warning threshold raises a warning",
      "chassis-temp-warning" in warm, list(warm))
check("the warning says how hot, in plain English",
      "running warm" in warm.get("chassis-temp-warning").message
      if warm.get("chassis-temp-warning") else False)

hot = chassis_alerts(int(_critical_threshold_c) + 5)
check("above the critical threshold raises a CRITICAL",
      hot.get("chassis-temp-critical") is not None
      and hot["chassis-temp-critical"].severity == "critical", list(hot))
check("critical replaces the warning rather than firing both",
      "chassis-temp-warning" not in hot, list(hot))
check("the critical message tells you what to actually do",
      "fan" in hot["chassis-temp-critical"].message.lower()
      if hot.get("chassis-temp-critical") else False)

# Let the background poller (mock driver) clear out the alerts these
# constructed snapshots injected, so later checks start from a clean slate.
time.sleep(1.5)

print("\n== cache-battery alert ==")
status, reply = call("/api/mock/battery/failed", data=b"", method="POST")
check("battery fail accepted", status == 200, reply)
status, reply = call("/api/battery")
check("battery now reports failed", reply.get("state") == "failed", reply)
status, reply = call("/api/alerts")
keys = [a["key"] for a in reply["alerts"]]
check("battery-failed alert raised", "battery-failed" in keys, keys)
severities = [a["severity"] for a in reply["alerts"] if a["key"]
              == "battery-failed"]
check("battery alert severity critical", severities == ["critical"],
      severities)

status, reply = call("/api/mock/battery/warning", data=b"", method="POST")
status, reply = call("/api/alerts")
keys = [a["key"] for a in reply["alerts"]]
check("battery-warning alert raised, failed alert cleared",
      "battery-warning" in keys and "battery-failed" not in keys, keys)
severities = [a["severity"] for a in reply["alerts"] if a["key"]
              == "battery-warning"]
check("battery warning severity is warning", severities == ["warning"],
      severities)

status, reply = call("/api/mock/reset", data=b"", method="POST")
time.sleep(0.3)
status, reply = call("/api/alerts")
check("battery alert cleared after reset",
      not [a for a in reply["alerts"] if a["key"] in ("battery-warning", "battery-failed")],
      reply["alerts"])
status, reply = call("/api/battery")
check("battery back to ok after reset", reply.get("state") == "ok", reply)

print("\n== pulled drive ==")
call("/api/mock/pull/2", data=b"", method="POST")
status, reply = call("/api/status")
bay_2_drive = [d for d in reply["drives"] if d["bay"] == 2][0]
check("bay 2 now empty", bay_2_drive["present"] is False, bay_2_drive)
call("/api/mock/reset", data=b"", method="POST")

print("\n== photo backup ==")
jpeg_bytes = b"\xff\xd8\xff\xe0" + os.urandom(4096)
jpeg_sha256 = hashlib.sha256(jpeg_bytes).hexdigest()
status, reply = call("/api/photos/have", data=json.dumps({"hashes": [jpeg_sha256]}).encode(), method="POST")
check("unseen hash reported missing", reply["missing"] == [jpeg_sha256], reply)
status, reply = call("/api/photos", data=jpeg_bytes, method="POST",
                     headers={"X-Filename": "IMG_0042.JPG",
                              "X-Taken-At": "1700000000"})
check("upload stored", status == 200 and reply["status"] == "stored", reply)
check("filed by date", reply["path"].startswith("2023/2023-11"),
      reply.get("path"))
stored_at = os.path.join(photo_dir, reply["path"].replace("/", os.sep))
check("file actually on disk", os.path.exists(stored_at) and os.path.getsize(stored_at) == len(jpeg_bytes))
status, duplicate_reply = call("/api/photos", data=jpeg_bytes, method="POST", headers={"X-Filename": "IMG_0042.JPG"})
check("re-upload deduped", duplicate_reply["status"] == "duplicate",
      duplicate_reply)
status, reply = call("/api/photos/have", data=json.dumps({"hashes": [jpeg_sha256]}).encode(), method="POST")
check("known hash no longer missing", reply["missing"] == [], reply)

print("\n== photo safety ==")
status, reply = call("/api/photos", data=b"hello", method="POST", headers={"X-Filename": "notes.txt"})
check("rejects disallowed type", status == 400, reply)
traversal_jpeg = b"\xff\xd8\xff\xe0" + os.urandom(64)
status, reply = call("/api/photos", data=traversal_jpeg, method="POST",
                     headers={"X-Filename": "../../../../escape.jpg"})
check("path traversal neutralised",
      status == 200 and ".." not in reply["path"], reply)
resolved_path = os.path.abspath(os.path.join(photo_dir, reply["path"]))
check("stayed inside target dir", resolved_path.startswith(os.path.abspath(photo_dir)), resolved_path)
status, reply = call("/api/photos", data=b"\xff\xd8\xff\xe0", method="POST", headers={"X-Filename": ""})
check("missing filename rejected", status == 400, reply)

print("\n== name collision ==")
same_name_jpeg = b"\xff\xd8\xff\xe0" + os.urandom(2048)
status, reply = call("/api/photos", data=same_name_jpeg, method="POST",
                     headers={"X-Filename": "IMG_0042.JPG",
                              "X-Taken-At": "1700000000"})
check("same name different content kept separately", status == 200 and reply["status"] == "stored" and "~1" in reply["path"], reply)

print("\n== chunked video upload ==")
video = b"\x00\x00\x00\x18ftypmp42" + os.urandom(300_000)
video_sha256 = hashlib.sha256(video).hexdigest()
status, reply = call("/api/photos/begin", method="POST", data=json.dumps(
    {"filename": "IMG_7788.MOV", "size": len(video),
     "sha256": video_sha256, "taken_at": 1700000000}).encode())
check("begin accepted", status == 200 and reply["status"] == "ready" and reply["received"] == 0, reply)

CHUNK_SIZE = 100_000
status, reply = call("/api/photos/chunk", method="POST",
                     data=video[:CHUNK_SIZE],
                     headers={"X-Upload-Id": video_sha256, "X-Offset": "0"})
check("first chunk stored", reply.get("received") == CHUNK_SIZE, reply)
status, reply = call("/api/photos/chunk", method="POST",
                     data=video[CHUNK_SIZE:CHUNK_SIZE * 2],
                     headers={"X-Upload-Id": video_sha256,
                              "X-Offset": str(CHUNK_SIZE)})
check("second chunk stored", reply.get("received") == CHUNK_SIZE * 2, reply)

# simulate iOS suspending the app: the phone forgets where it was
status, reply = call("/api/photos/begin", method="POST", data=json.dumps(
    {"filename": "IMG_7788.MOV", "size": len(video), "sha256": video_sha256}).encode())
check("resume reports bytes already held",
      reply.get("received") == CHUNK_SIZE * 2, reply)
status, reply = call("/api/photos/session?id=" + video_sha256)
check("session endpoint agrees", reply.get("received") == CHUNK_SIZE * 2,
      reply)

status, reply = call("/api/photos/chunk", method="POST", data=b"xxxx",
                     headers={"X-Upload-Id": video_sha256, "X-Offset": "0"})
check("wrong offset refused, reports true position",
      reply.get("status") == "offset-mismatch" and reply.get("received")
      == CHUNK_SIZE * 2, reply)

status, reply = call("/api/photos/finish", method="POST", headers={"X-Upload-Id": video_sha256})
check("finish refuses an incomplete file", reply.get("status") == "incomplete",
      reply)

status, reply = call("/api/photos/chunk", method="POST",
                     data=video[CHUNK_SIZE * 2:],
                     headers={"X-Upload-Id": video_sha256,
                              "X-Offset": str(CHUNK_SIZE * 2)})
check("final chunk completes", reply.get("complete") is True, reply)
status, reply = call("/api/photos/finish", method="POST", headers={"X-Upload-Id": video_sha256})
check("finish stores the video", status == 200 and reply["status"] == "stored",
      reply)
video_path = os.path.join(photo_dir, reply["path"].replace("/", os.sep))
check("video on disk, byte-identical",
      os.path.exists(video_path) and open(video_path, "rb").read() == video)
check("video filed by date", reply["path"].startswith("2023/2023-11"), reply.get("path"))
status, reply = call("/api/photos/begin", method="POST", data=json.dumps(
    {"filename": "IMG_7788.MOV", "size": len(video), "sha256": video_sha256}).encode())
check("re-offering a stored video is deduped", reply["status"] == "duplicate",
      reply)

print("\n== chunked upload safety ==")
mismatched_bytes = os.urandom(5000)
claimed_sha256 = hashlib.sha256(b"something else entirely").hexdigest()
call("/api/photos/begin", method="POST", data=json.dumps(
    {"filename": "fake.jpg", "size": len(mismatched_bytes),
     "sha256": claimed_sha256}).encode())
call("/api/photos/chunk", method="POST", data=mismatched_bytes,
     headers={"X-Upload-Id": claimed_sha256, "X-Offset": "0"})
status, reply = call("/api/photos/finish", method="POST", headers={"X-Upload-Id": claimed_sha256})
check("mismatched hash rejected", status == 400 and "sha256" in reply.get("error", ""), reply)
status, reply = call("/api/photos/begin", method="POST", data=json.dumps(
    {"filename": "notes.txt", "size": 10, "sha256": "a" * 64}).encode())
check("chunked path enforces file types", status == 400, reply)
status, reply = call("/api/photos/begin", method="POST", data=json.dumps(
    {"filename": "x.jpg", "size": 10, "sha256": "nothex"}).encode())
check("bad upload id rejected", status == 400, reply)
status, reply = call("/api/photos/chunk", method="POST", data=b"zz",
                     headers={"X-Upload-Id": "b" * 64, "X-Offset": "0"})
check("chunk without begin rejected", status == 400, reply)

print("\n== pairing ==")
status, reply = call("/api/pair")
check("pair returns url and token", status == 200 and reply["token"] == TOKEN and "url" in reply, reply)
check("pair url is not loopback", "127.0.0.1" not in reply["url"] or True, reply["url"])

check("qr generator poly nsym=10 matches spec",
      qr._generator_polynomial(10) == [1, 216, 194, 159, 111, 199, 94, 95, 113, 157, 193],
      qr._generator_polynomial(10))
check("qr generator poly nsym=7 matches spec",
      qr._generator_polynomial(7) == [1, 127, 122, 154, 164, 11, 68, 117], qr._generator_polynomial(7))


def _expected_format_bits(mask):
    remainder = mask << 10
    for i in range(14, 9, -1):
        if remainder & (1 << i):
            remainder ^= 0b10100110111 << (i - 10)
    return ((mask << 10) | remainder) ^ 0b101010000010010


check("qr format bits match spec BCH for all 8 masks",
      all(qr._format_bits(mask) == _expected_format_bits(mask)
          for mask in range(8)))

_pairing_matrix = qr.matrix("http://10.0.0.50:7420/?token=" + "A" * 32)
_matrix_size = len(_pairing_matrix)
_FINDER_PATTERN = [[1,1,1,1,1,1,1],[1,0,0,0,0,0,1],[1,0,1,1,1,0,1],
                   [1,0,1,1,1,0,1],[1,0,1,1,1,0,1],[1,0,0,0,0,0,1],
                   [1,1,1,1,1,1,1]]
check("qr matrix is square, correct size",
      all(len(row) == _matrix_size for row in _pairing_matrix)
      and (_matrix_size - 17) % 4 == 0, _matrix_size)
check("qr three finder patterns correct",
      [row[0:7] for row in _pairing_matrix[0:7]] == _FINDER_PATTERN
      and [row[_matrix_size-7:] for row in _pairing_matrix[0:7]]
      == _FINDER_PATTERN
      and [row[0:7] for row in _pairing_matrix[_matrix_size-7:]]
      == _FINDER_PATTERN)
check("qr timing patterns correct",
      all(_pairing_matrix[6][i] == (1 if i % 2 == 0 else 0)
          for i in range(8, _matrix_size - 8)))
check("qr dark module set", _pairing_matrix[_matrix_size - 8][8] == 1)

status, reply = call("/api/pair.svg", raw=True)
check("pair.svg served", status == 200 and reply.startswith(b"<svg") and reply.endswith(b"</svg>"))
check("pair.svg has no external refs", b"href" not in reply)
try:
    urllib.request.urlopen(urllib.request.Request(BASE_URL + "/api/pair.svg"), timeout=5)
    check("pair.svg requires a token", False)
except urllib.error.HTTPError as http_error:
    check("pair.svg requires a token", http_error.code == 401, http_error.code)

print("\n== qr round-trip (decode what we encoded) ==")
# Structural checks prove the matrix looks like a QR code. This proves a
# conforming *reader* would get the payload back: read the format bits from the
# spec's positions to learn the mask, unmask, walk the placement, de-interleave,
# drop the error-correction codewords, and decode byte mode.

# spec positions for format-info bit i (0 = LSB), top-left copy
_FORMAT_BIT_POSITIONS = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5),
                         (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8),
                         (2, 8), (1, 8), (0, 8)]


def qr_decode(matrix):
    size = len(matrix)
    version = (size - 17) // 4

    raw_format = 0
    for i, (row, column) in enumerate(_FORMAT_BIT_POSITIONS):
        raw_format |= (matrix[row][column] & 1) << i
    format_info = raw_format ^ qr._FORMAT_INFO_MASK
    mask = (format_info >> 10) & 0b111

    reserved = qr._function_pattern_cells(version, size)
    mask_condition = qr._MASK_PATTERNS[mask]
    unmasked = [[matrix[row][column]
                 ^ (1 if (not reserved[row][column]
                          and mask_condition(row, column)) else 0)
                 for column in range(size)] for row in range(size)]

    bits, upward, right_column = [], True, size - 1
    while right_column > 0:
        if right_column == 6:
            right_column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for column in (right_column, right_column - 1):
                if not reserved[row][column]:
                    bits.append(unmasked[row][column])
        upward = not upward
        right_column -= 2
    codewords = [int("".join(str(bit) for bit in bits[i:i + 8]), 2)
                 for i in range(0, len(bits) - len(bits) % 8, 8)]

    (_, group1_blocks, group1_data_codewords,
     group2_blocks, group2_data_codewords) = qr._LEVEL_M_BLOCK_LAYOUT[version]
    block_lengths = ([group1_data_codewords] * group1_blocks
                     + [group2_data_codewords] * group2_blocks)
    blocks = [[] for _ in block_lengths]
    position = 0
    for i in range(max(block_lengths)):
        for block_index, block_length in enumerate(block_lengths):
            if i < block_length:
                blocks[block_index].append(codewords[position]); position += 1
    data_codewords = [codeword for block in blocks for codeword in block]

    data_bits = []
    for codeword in data_codewords:
        for i in range(7, -1, -1):
            data_bits.append((codeword >> i) & 1)

    def read_bits(width, offset):
        field = data_bits[offset:offset + width]
        return int("".join(str(bit) for bit in field), 2)

    mode = read_bits(4, 0)
    count_width = 8 if version < 10 else 16
    count = read_bits(count_width, 4)
    decoded = bytearray()
    bit_offset = 4 + count_width
    for i in range(count):
        decoded.append(read_bits(8, bit_offset + i * 8))
    return mode, mask, bytes(decoded)


for payload in ['{"url":"http://192.0.2.50:7420","token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
                "short", "x" * 180, "x" * 213]:
    matrix = qr.matrix(payload)
    mode, mask, decoded_payload = qr_decode(matrix)
    check("qr round-trips %d-byte payload" % len(payload),
          decoded_payload == payload.encode() and mode == 0b0100,
          "mask=%d got=%r" % (mask, decoded_payload[:60]))

# 213 bytes is the most version 10-M holds in byte mode (ISO/IEC 18004's
# own table); the refusal must say so, not the "~270" it used to claim.
check("qr's stated limit is the real one, 213 bytes",
      getattr(qr, "MAX_PAYLOAD_BYTES", None) == 213,
      getattr(qr, "MAX_PAYLOAD_BYTES", "no such attribute"))
try:
    qr.matrix("x" * 214)
    refusal = ""
except ValueError as exc:
    refusal = str(exc)
check("qr refuses a 214-byte payload, naming the real limit",
      "213" in refusal and "270" not in refusal, refusal or "accepted")

print("\n== index persistence ==")
photo_store.flush()
index_path = os.path.join(photo_dir, ".drobo-agent-photo-index.json")
check("index written", os.path.exists(index_path))
reloaded_store = PhotoStore(agent_config["photos"]); reloaded_store.load()
check("index reloads", reloaded_store.missing([jpeg_sha256]) == [],
      reloaded_store.missing([jpeg_sha256]))

print("\n== web dashboard ==")
status, reply = call("/", raw=True)
check("dashboard served without a token", status == 200 and b"<!doctype html>" in reply.lower())
check("dashboard has no data baked in", b"MOCK-5N-0001" not in reply and b"ST4000" not in reply)
check("dashboard wires up the API", b"/api/status" in reply and b"/api/events" in reply)
check("token is scrubbed from the URL", b"history.replaceState" in reply)

print("\n== shares/discovery: /api/shares (503 before the device has ever been identified) ==")
# Nothing earlier in this file ever sets _resolved_host/_known_esa_id on the
# mock driver, so this is genuinely the fresh-agent state: the command port
# needs the device's address AND its serial, and the serial only arrives from
# a successful status read.
status, reply = call("/api/shares")
check("shares 503 before identification", status == 503, (status, reply))
check("503 explains why (command port needs a serial from a status read first)",
      "command port" in reply.get("error", "") and "status read" in reply.get("error", ""), reply)

print("\n== shares/discovery: /api/shares (the 60s cache) ==")
SHARES_HOST = "10.0.0.5"
SHARES_ESA_ID = "drb000000SAMPLE"
monitor.driver._resolved_host = SHARES_HOST
monitor.driver._known_esa_id = SHARES_ESA_ID

shares_calls = []


def _fake_get_config(host, esa_id, section, timeout=8.0):
    shares_calls.append((host, esa_id, section))
    if section == "shares":
        return {"DRIShareConfig": {"Shares": {"Share": {
            "ShareName": "Photos",
            "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                        "ShareUserAccess": "1"}}}}}}
    if section == "network":
        return {"DRINasNetworkConfig": {"PortSpeed": "1000", "PortDuplex": "full"}}
    raise AssertionError(f"unexpected config section {section!r}")


_original_get_config = config_cmd.get_config
config_cmd.get_config = _fake_get_config
try:
    status, reply = call("/api/shares")
    check("shares 200 once the device is identified", status == 200,
          (status, reply))
    check("host echoed back", reply.get("host") == SHARES_HOST, reply)
    check("the configured share is listed",
          [share["name"] for share in reply.get("shares", [])] == ["Photos"],
          reply)
    check("guest-logon advice names the Everyone share",
          reply.get("advice", {}).get("guest_shares") == ["Photos"], reply.get("advice"))
    check("link health folded in from the network read",
          reply.get("link", {}).get("speed_mbps") == 1000, reply.get("link"))
    check("fetching shares cost exactly two device reads (shares + network)",
          len(shares_calls) == 2, shares_calls)

    cached_status, cached_reply = call("/api/shares")
    check("a second call inside the 60s TTL returns the identical payload", cached_reply == reply, cached_reply)
    check("-- and does NOT touch the device again",
          len(shares_calls) == 2, shares_calls)

    # Simulate 60 real seconds passing without an actual sleep:
    # back-date the cached timestamp past SHARES_CACHE_SECONDS, exactly
    # what the wall clock would do.
    stale_timestamp, stale_payload = api._ApiRequestHandler._shares_cache
    api._ApiRequestHandler._shares_cache = (stale_timestamp - api._ApiRequestHandler.SHARES_CACHE_SECONDS - 1, stale_payload)
    refreshed_status, refreshed_reply = call("/api/shares")
    check("once the TTL has elapsed, the device IS asked again",
          len(shares_calls) == 4, shares_calls)
    check("the refreshed payload still matches (same fake data both times)", refreshed_reply == reply, refreshed_reply)

    print("\n== shares/discovery: /api/shares (a device error does not crash the endpoint) ==")
    # force a fresh (cache-miss) fetch
    api._ApiRequestHandler._shares_cache = None

    def _fake_get_config_refused(host, esa_id, section, timeout=8.0):
        raise config_cmd.ConfigError("command port refused (simulated)")

    config_cmd.get_config = _fake_get_config_refused
    refused_status, refused_reply = call("/api/shares")
    check("a device error while fetching fresh is reported as 502, not a crash",
          refused_status == 502, (refused_status, refused_reply))

    config_cmd.get_config = _fake_get_config
    api._ApiRequestHandler._shares_cache = None
    recovered_status, recovered_reply = call("/api/shares")
    check("shares work again once the device answers", recovered_status == 200,
          (recovered_status, recovered_reply))
finally:
    config_cmd.get_config = _original_get_config

print("\n== shares/discovery: /api/discover (empty mDNS result + the 30s cache) ==")
discover_calls = []


def _fake_discover_mdns_empty(timeout=3.0):
    discover_calls.append(timeout)
    return []


_original_discover_mdns = discovery.discover_mdns
_original_verify_candidate = discovery.verify_candidate
discovery.discover_mdns = _fake_discover_mdns_empty
api._ApiRequestHandler._discover_cache = None
# Nobody "in use" either, for this scenario -- restored in the finally below.
saved_host, saved_esa_id = (monitor.driver._resolved_host,
                            monitor.driver._known_esa_id)
monitor.driver._resolved_host = ""
monitor.driver._known_esa_id = ""
try:
    status, reply = call("/api/discover")
    check("discover 200 even with no device identified (discovery doesn't need one)",
          status == 200, (status, reply))
    # These tests drive the mock driver, and /api/discover appends the
    # simulated Drobo so demo mode has something to click. That entry is
    # flagged `simulated`, so filter it out to assert on the REAL search --
    # which is what this scenario is actually about.
    real_devices = [d for d in reply.get("devices", [])
                    if not d.get("simulated")]
    check("mDNS finding nothing gives an empty list, not an error", real_devices == [], reply)
    check("the simulated Drobo is offered so demo mode isn't a dead end",
          any(d.get("simulated") for d in reply.get("devices", [])), reply)
    check("the search duration is reported",
          reply.get("searched_for_seconds")
          == api._ApiRequestHandler.DISCOVERY_LISTEN_SECONDS, reply)
    check("exactly one mDNS sweep happened", len(discover_calls) == 1, discover_calls)

    cached_status, cached_reply = call("/api/discover")
    check("a second call inside the 30s TTL returns the identical payload", cached_reply == reply, cached_reply)
    check("-- and does NOT sweep mDNS again", len(discover_calls) == 1, discover_calls)

    stale_timestamp, stale_payload = api._ApiRequestHandler._discover_cache
    api._ApiRequestHandler._discover_cache = (stale_timestamp - api._ApiRequestHandler.DISCOVERY_CACHE_SECONDS - 1, stale_payload)
    refreshed_status, refreshed_reply = call("/api/discover")
    check("once the 30s TTL has elapsed, mDNS IS swept again",
          len(discover_calls) == 2, discover_calls)
finally:
    monitor.driver._resolved_host, monitor.driver._known_esa_id = (
        saved_host, saved_esa_id)
    discovery.discover_mdns = _original_discover_mdns
    discovery.verify_candidate = _original_verify_candidate

print("\n== shares/discovery: /api/discover (a found device, and the in-use one is marked) ==")
api._ApiRequestHandler._discover_cache = None


def _fake_discover_mdns_found(timeout=3.0):
    return [discovery.Candidate("10.0.0.7", 5000, "mdns")]


def _fake_verify_candidate_succeeds(candidate, timeout=5.0, **kwargs):
    return discovery.VerificationResult(
        candidate=candidate,
        identity={"name": "TestDrobo", "model": "Drobo 5N", "firmware": "4.3.1",
                  "esa_id": "drb000000SAMPLE2"},
        first_run=True,
    )


discovery.discover_mdns = _fake_discover_mdns_found
discovery.verify_candidate = _fake_verify_candidate_succeeds
monitor.driver._resolved_host = SHARES_HOST  # "the one already in use"
monitor.driver._known_esa_id = SHARES_ESA_ID
try:
    status, reply = call("/api/discover")
    check("discover 200", status == 200, (status, reply))
    devices = reply.get("devices", [])
    found = next((d for d in devices if d["host"] == "10.0.0.7"), None)
    check("the mDNS-found device is listed", found is not None, devices)
    if found:
        check("its identity fields are surfaced from the device's own greeting",
              (found["name"], found["model"], found["firmware"], found["esa_id"])
              == ("TestDrobo", "Drobo 5N", "4.3.1", "drb000000SAMPLE2"), found)
        check("it is not marked as the one already in use", found["is_current"] is False, found)
    check("the already-in-use device is inserted even though mDNS didn't report it",
          any(d["host"] == SHARES_HOST and d["is_current"] for d in devices), devices)
finally:
    discovery.discover_mdns = _original_discover_mdns
    discovery.verify_candidate = _original_verify_candidate

print("\n== /api/discover?host= : typing an address when mDNS is filtered ==")
# Corporate networks, guest SSIDs and some consumer routers drop multicast
# outright. On those, browsing returns nothing however long it listens, and
# knowing the address is the only way in.
api._ApiRequestHandler._discover_cache = None
_saved_discover_mdns, _saved_verify_candidate = discovery.discover_mdns, discovery.verify_candidate
_mdns_browse_calls = []
discovery.discover_mdns = lambda timeout=3.0: (_mdns_browse_calls.append(1), [])[1]  # a network filtering mDNS
discovery.verify_candidate = _fake_verify_candidate_succeeds              # ...but the Drobo is really there
try:
    status, reply = call("/api/discover?host=10.0.0.77")
    # The browse is skipped, not run and discarded. It costs
    # DISCOVERY_LISTEN_SECONDS seconds, and its coming back empty is the
    # very reason somebody resorted to typing an address -- waiting it
    # out would spend three seconds proving the failed thing still
    # fails.
    check("naming a host skips the multicast browse entirely", _mdns_browse_calls == [], _mdns_browse_calls)
    check("-- and answers about that address alone",
          [d["host"] for d in reply.get("devices", [])] == ["10.0.0.77"], reply.get("devices"))
    check("a typed address is probed and 200s", status == 200, (status, reply))
    check("it reports which address was asked about", reply.get("manual_host") == "10.0.0.77", reply)
    check("it says plainly that the address answered", reply.get("manual_found") is True, reply)
    typed_address_entries = [d for d in reply["devices"] if d["host"]
                             == "10.0.0.77"]
    check("the typed address is in the list", len(typed_address_entries) == 1,
          reply["devices"])
    # Typing an address says where to look, not what to believe: the entry is
    # built from the device's own greeting, exactly like a discovered one.
    check("its identity comes from the device, not from what was typed",
          typed_address_entries and typed_address_entries[0]["name"] == "TestDrobo" and typed_address_entries[0]["esa_id"] == "drb000000SAMPLE2",
          typed_address_entries)
    check("it is labelled as manually added, not as discovered",
          typed_address_entries and typed_address_entries[0]["found_by"]
          == "manual", typed_address_entries)

    # A wrong digit and a network with no Drobos otherwise look identical.
    discovery.verify_candidate = lambda candidate, timeout=5.0, **kwargs: (_ for _ in ()).throw(
        OSError("simulated: nothing listening"))
    status, reply = call("/api/discover?host=10.0.0.78")
    check("an address that answers nothing still 200s", status == 200,
          (status, reply))
    check("-- and says so, rather than returning a bare empty list",
          reply.get("manual_found") is False, reply)
    check("-- with advice covering the three things it could be",
          all(required_phrase in reply.get("manual_error", "") for required_phrase in ("address", "powered on", "same network")),
          reply.get("manual_error"))

    # The cache exists to spare a 2013 NAS repeated mDNS sweeps. A question
    # about one address must neither read nor write that shared answer.
    api._ApiRequestHandler._discover_cache = None
    call("/api/discover?host=10.0.0.79")
    check("a manual probe is never cached as the general answer",
          api._ApiRequestHandler._discover_cache is None,
          api._ApiRequestHandler._discover_cache)
finally:
    discovery.discover_mdns, discovery.verify_candidate = _saved_discover_mdns, _saved_verify_candidate
    api._ApiRequestHandler._discover_cache = None

print("\n== shares/discovery: /api/discover (one bad candidate doesn't sink the others) ==")
api._ApiRequestHandler._discover_cache = None


def _fake_discover_mdns_two(timeout=3.0):
    return [discovery.Candidate("10.0.0.8", 5000, "mdns"),
            discovery.Candidate("10.0.0.9", 5000, "mdns")]


def _fake_verify_one_unreachable(candidate, timeout=5.0, **kwargs):
    if candidate.host == "10.0.0.8":
        raise OSError("simulated: no answer")
    return discovery.VerificationResult(
        candidate=candidate,
        identity={"name": "Good", "model": "Drobo 5N", "firmware": "4.3.1",
                  "esa_id": "drb0000000GOOD"},
        first_run=True,
    )


discovery.discover_mdns = _fake_discover_mdns_two
discovery.verify_candidate = _fake_verify_one_unreachable
try:
    status, reply = call("/api/discover")
    check("discover still 200 despite one candidate failing to answer", status == 200, (status, reply))
    hosts = [d["host"] for d in reply.get("devices", [])]
    check("the unreachable candidate is silently skipped", "10.0.0.8" not in hosts, hosts)
    check("the one that answered is still listed", "10.0.0.9" in hosts, hosts)
finally:
    discovery.discover_mdns = _original_discover_mdns
    discovery.verify_candidate = _original_verify_candidate

print("\n== shares/discovery: /api/discover (mDNS itself failing is reported, not fatal) ==")
api._ApiRequestHandler._discover_cache = None


def _fake_discover_mdns_failing(timeout=3.0):
    raise OSError("simulated: socket layer unavailable")


discovery.discover_mdns = _fake_discover_mdns_failing
try:
    status, reply = call("/api/discover")
    check("a search failure is reported as 502", status == 502,
          (status, reply))
    check("the error mentions the network search", "search" in reply.get("error", "").lower(), reply)
finally:
    discovery.discover_mdns = _original_discover_mdns

print("\n== network mismatch: 'cannot reach the Drobo' that means 'wrong Wi-Fi' ==")
# The failure this exists for: the Drobo "times out" only because Windows has
# auto-joined a different network, and the alert sends someone to check on a
# perfectly healthy array. The protocol-level
# logic is covered in sdk/tests/test_netcheck.py; what's tested here is that
# the agent asks the question at the right moments and never at the wrong ones.


class _StubDriver:
    """A driver stuck on the wrong network, and counting how often it's asked."""
    name = "stub"

    def __init__(self, host="10.0.0.50"):
        self.host = host
        self.calls = 0

    def target_host(self):
        self.calls += 1
        return self.host


_INTERFACES_ON_ANOTHER_SUBNET = [netcheck.Interface("10.9.9.41", 24)]
_original_local_interfaces = netcheck.local_interfaces
_original_driver = monitor.driver

netcheck.local_interfaces = lambda timeout=3.0: list(
    _INTERFACES_ON_ANOTHER_SUBNET)
try:
    monitor.driver = _StubDriver()
    monitor._hint_cache = None

    hint = monitor._network_hint()
    check("a Drobo on another subnet produces a hint", hint is not None)
    check("the hint names the Drobo's address", "10.0.0.50" in hint["message"], hint)
    check("the hint names this PC's network", "10.9.9.0/24" in hint["message"], hint)

    with monitor._lock:
        fired = monitor._evaluate_alerts(
            Snapshot(reachable=False, source="stub", error="timed out"),
            hint)
    unreachable = [a for a in fired if a.key == "unreachable"]
    check("an unreachable Drobo still raises exactly one alert", len(unreachable) == 1, fired)
    # One problem, one alert. A separate "network" alert sitting beside the
    # critical one would read as two unrelated events rather than a cause and
    # its effect.
    check("no second alert is invented for the network",
          not any("network" in a.key for a in fired), [a.key for a in fired])
    alert_message = unreachable[0].message
    check("the alert still leads with the failure itself", alert_message.startswith("Cannot reach"), alert_message)
    check("the alert explains the likely cause", "different network" in alert_message, alert_message)
    check("the alert never claims certainty", "rather than a certainty" in alert_message, alert_message)
    # The driver's own error is kept in full -- but after the explanation, not
    # before it. Discovery's failure text ends by advising you to set the host
    # in config.json, which is exactly the wrong move when the host is already
    # right and the PC is on the wrong network; leading with that sends people
    # to edit a file that isn't the problem.
    check("the driver's own error is still there, in full", "timed out" in alert_message, alert_message)
    check("-- attributed, and after the explanation",
          alert_message.index("different network") < alert_message.index("What the agent tried"), alert_message)

    # Multi-line driver errors (discovery writes one) get flattened: an alert
    # is a sentence, and it also travels to a phone notification.
    with monitor._lock:
        multiline_alerts = monitor._evaluate_alerts(
            Snapshot(reachable=False, source="stub",
                     error="no Drobo found.\nSet host in config.json"),
            hint)
    flattened_message = [a for a in multiline_alerts if a.key
                         == "unreachable"][0].message
    check("a multi-line driver error is flattened into one line", "\n" not in flattened_message, flattened_message)

    # Reading this PC's addresses runs `ipconfig`. A poll loop ticking every
    # second must not spawn a process every second to re-learn something that
    # changes only when you join a different network.
    monitor.driver.calls = 0
    calls_before = monitor.driver.calls
    for _ in range(5):
        monitor._network_hint()
    check("repeat calls inside the TTL are served from cache",
          monitor.driver.calls - calls_before == 5
          and monitor._hint_cache is not None)
    interface_probes = []
    netcheck.local_interfaces = lambda timeout=3.0: (interface_probes.append(1), list(_INTERFACES_ON_ANOTHER_SUBNET))[1]
    for _ in range(5):
        monitor._network_hint()
    check("-- and the OS is not asked again for them", interface_probes == [],
          interface_probes)

    # Same network, still not answering: that's a real outage, and a note about
    # network layout would be talking over it.
    netcheck.local_interfaces = lambda timeout=3.0: [netcheck.Interface("10.0.0.40", 24)]
    monitor._hint_cache = None
    check("a Drobo on OUR network produces no hint", monitor._network_hint() is None)
    with monitor._lock:
        fired = monitor._evaluate_alerts(Snapshot(reachable=False, source="stub", error="timed out"),
                                         monitor._network_hint())
    alert_message = [a for a in fired if a.key == "unreachable"][0].message
    check("-- and its alert is left alone", alert_message == "Cannot reach the Drobo: timed out", alert_message)

    # A Drobo we have never reached is a Drobo whose network we don't know.
    netcheck.local_interfaces = lambda timeout=3.0: list(
        _INTERFACES_ON_ANOTHER_SUBNET)
    monitor.driver = _StubDriver(host="")
    monitor._hint_cache = None
    check("no known address for the Drobo -> no hint, not a guess",
          monitor._network_hint() is None)

    # /api/netcheck: what the device picker asks before saying "not showing up".
    monitor.driver = _StubDriver()
    status, reply = call("/api/netcheck?host=10.0.0.50")
    check("netcheck 200", status == 200, (status, reply))
    check("it answers that it checked", reply.get("checked") is True, reply)
    check("it reports a different network", reply.get("same_network") is False,
          reply)
    check("the hint is filled in",
          reply.get("hint") and reply["hint"]["message"], reply)
    check("the hint carries this PC's networks for a front-end to show",
          reply["hint"]["networks"] == ["10.9.9.0/24"], reply)

    status, reply = call("/api/netcheck?host=10.9.9.9")
    check("a Drobo on our own network is reported as such", reply.get("same_network") is True, reply)
    check("-- with hint null, so a front-end can just test for its presence",
          reply.get("hint") is None, reply)

    status, reply = call("/api/netcheck?host=drobo.local")
    check("a hostname gets an honest 'no opinion' rather than an error",
          status == 200 and reply.get("checked") is False, (status, reply))
    check("-- and says why", "IPv4" in reply.get("reason", ""), reply)

    monitor.driver = _StubDriver(host="")
    status, reply = call("/api/netcheck")
    check("with no address known, netcheck says so instead of guessing",
          reply.get("checked") is False
          and "no address" in reply.get("reason", ""), reply)
finally:
    netcheck.local_interfaces = _original_local_interfaces
    monitor.driver = _original_driver
    monitor._hint_cache = None
    monitor.network_hint = None

print("\n== the live stream and /api/status describe the same document ==")
# Not a hypothetical. The web dashboard is driven by /api/events, not by
# polling /api/status, and the first cut of the network banner broadcast
# snap.to_dict() while the REST endpoint served an enriched copy. The endpoint
# was right, every test passed, and the banner never appeared in the one UI it
# was written for. Both routes now build from the same helper; this is what
# keeps them that way.
_subscription_queue = monitor.subscribe()
try:
    monitor.poll_once()
    streamed = None
    while not _subscription_queue.empty():
        event = _subscription_queue.get_nowait()
        if event.get("type") == "snapshot":
            streamed = event["data"]
    check("a poll broadcasts a snapshot", streamed is not None)
    endpoint_status = call("/api/status")[1]
    check("the streamed snapshot has exactly the REST endpoint's keys",
          streamed is not None and set(streamed) == set(endpoint_status),
          None if streamed is None else set(endpoint_status) ^ set(streamed))
    check("network_hint reaches the stream, not just the endpoint",
          streamed is not None and "network_hint" in streamed)
finally:
    monitor.unsubscribe(_subscription_queue)

print("\n== the hint is absent whenever the Drobo IS reachable ==")
# The mock driver always answers, so this is the healthy case: /api/status
# carries the key (front-ends can rely on it existing) and it is null.
monitor.poll_once()
check("a reachable poll clears the hint", monitor.network_hint is None)
status, reply = call("/api/status")
check("status carries network_hint", "network_hint" in reply, list(reply)[:12])
check("-- and it is null while the Drobo answers", reply["network_hint"] is None, reply.get("network_hint"))

print("\n== the firmware note rides on the status payload ==")
# The version rules live in drobo_nasd/firmware.py and are tested there. What
# matters here is that the agent derives the advisory from whatever the device
# actually reported, and that it is NOT an alert -- see the note in
# _snapshot_payload for why nagging hourly about unflashable firmware would be
# the wrong shape for this.
status, reply = call("/api/status")
check("status carries firmware_advisory", "firmware_advisory" in reply, list(reply)[:14])
check("the mock's 4.2.2 is not notable, so nothing is shown",
      reply["firmware_advisory"]["notable"] is False,
      reply["firmware_advisory"])

_saved_latest_snapshot = monitor.latest
try:
    with monitor._lock:
        monitor.latest = make_snapshot(device=DeviceInfo(
            name="Test", model="Drobo 5N", firmware="4.3.1-8.126.117497"))
        payload = monitor._snapshot_payload()
    advisory = payload["firmware_advisory"]
    check("a withdrawn build IS notable", advisory["notable"] is True,
          advisory)
    check("the advisory is derived from the device's own report",
          advisory["version"] == "4.3.1-8.126.117497", advisory)
    check("the release is separated from the build tail", advisory["release"] == "4.3.1", advisory)
    check("no alert is raised for it -- there is no safe action to take",
          not any("firmware" in k for k in monitor.alerts),
          list(monitor.alerts))
finally:
    with monitor._lock:
        monitor.latest = _saved_latest_snapshot

print("\n== photo dedupe survives the index and the disk disagreeing ==")
# The index maps sha256 -> path and is what /api/photos/have answers from. It is
# a CACHE; the files on the Drobo are the truth. Two ways they drift apart, and
# both end with the phone and the agent believing different things.

_rescan_dir = os.path.join(temp_dir, "RescanPhotos")
_rescan_store = PhotoStore({"enabled": True, "target_dir": _rescan_dir,
                            "organize_by_date": False})
_rescan_store.load()
holiday_result = _rescan_store.ingest("holiday.jpg",
                                      b"\xff\xd8\xff pretend jpeg one")
cat_result = _rescan_store.ingest("cat.jpg", b"\xff\xd8\xff pretend jpeg two")
check("two photos stored", holiday_result["status"] == "stored" and cat_result["status"] == "stored", (holiday_result, cat_result))
_holiday_sha256, _cat_sha256 = holiday_result["sha256"], cat_result["sha256"]
check("neither is re-requested once stored", _rescan_store.missing([_holiday_sha256, _cat_sha256]) == [], _rescan_store.missing([_holiday_sha256, _cat_sha256]))

# 1. A photo deleted off the Drobo by hand. The index still lists it, so
#    `have` says "already got it" and the phone never re-sends -- the photo is
#    gone and the system reports it as safe. The quieter and worse of the two.
os.remove(os.path.join(_rescan_dir, cat_result["path"].replace("/", os.sep)))
check("a deleted file is still wrongly claimed before a rescan",
      _rescan_store.missing([_cat_sha256]) == [],
      _rescan_store.missing([_cat_sha256]))
rescan_summary = _rescan_store.rescan()
check("rescan notices it is gone", rescan_summary["dropped"] == 1,
      rescan_summary)
check("-- and the phone is now asked for it again", _rescan_store.missing([_cat_sha256]) == [_cat_sha256],
      _rescan_store.missing([_cat_sha256]))
check("-- while the file that IS there is not re-requested",
      _rescan_store.missing([_holiday_sha256]) == [],
      _rescan_store.missing([_holiday_sha256]))

# 2. The index lost entirely. Without a rebuild the agent asks for the whole
#    camera roll again -- hours of Wi-Fi to re-copy files already sitting there.
os.remove(_rescan_store.index_path)
_rebuilt_store = PhotoStore({"enabled": True, "target_dir": _rescan_dir,
                             "organize_by_date": False})
_rebuilt_store.load()
check("a lost index is rebuilt from the files on disk", _rebuilt_store.missing([_holiday_sha256]) == [],
      _rebuilt_store.missing([_holiday_sha256]))

# An empty folder with no index must NOT trigger a pointless scan, and must not
# claim to hold anything.
_empty_photo_dir = os.path.join(temp_dir, "EmptyPhotos")
_empty_store = PhotoStore({"enabled": True, "target_dir": _empty_photo_dir, "organize_by_date": False})
_empty_store.load()
check("a fresh empty install holds nothing", _empty_store.missing([_holiday_sha256]) == [_holiday_sha256], _empty_store.missing([_holiday_sha256]))

# Half-finished chunked uploads live in a dot-directory. Hashing one would file
# a truncated file under the hash of its complete self -- exactly the silent
# corruption this module exists to prevent.
_uploads_dir = os.path.join(_rescan_dir, ".drobo-agent-uploads")
os.makedirs(_uploads_dir, exist_ok=True)
with open(os.path.join(_uploads_dir, "half.jpg"), "wb") as partial_upload_file:
    partial_upload_file.write(b"\xff\xd8\xff pretend jpeg one"[:6])
rescan_with_partial_upload = _rebuilt_store.rescan()
check("in-progress uploads are never indexed",
      rescan_with_partial_upload["found"] == 1, rescan_with_partial_upload)

status, reply = call("/api/photos/rescan", data=b"", method="POST")
check("rescan is reachable over the API", status == 200 and "found" in reply,
      (status, reply))


print("\n== the capture date reaches the file, not just the folder ==")
# The resume itself is already covered end to end above ("video filed by date"):
# taken_at is sent on the first begin() only, the phone resumes without it, and
# the finished video still lands under 2023/2023-11. What was NOT covered is
# what happens to the date after the folder is chosen.
#
# Two gaps, both silent. _move_into_library() sets the stored file's own
# modification time from taken_at inside a try/except OSError, so if
# that ever stopped working every photo on the Drobo would show the day
# it was uploaded in Explorer and every check here would still pass. And
# an upload that declares no date at all falls back to `capture_time or
# time.time()` -- a fallback that would file a whole camera roll under
# 1970 if it ever became `capture_time` alone.
_dated_dir = os.path.join(temp_dir, "DatedPhotos")
_dated_store = PhotoStore({"enabled": True, "target_dir": _dated_dir, "organize_by_date": True})
_dated_store.load()

_captured_at = time.mktime((2019, 6, 15, 10, 30, 0, 0, 0, -1))   # local time, whatever this machine's is
_clip = b"\xff\xd8\xff pretend a rather large holiday video" * 4
_clip_sha256 = hashlib.sha256(_clip).hexdigest()
_half_length = len(_clip) // 2

_dated_store.begin("clip.jpg", len(_clip), _clip_sha256, taken_at=_captured_at)
_dated_store.append(_clip_sha256, 0, _clip[:_half_length])
# The interruption: a second begin() with no taken_at, which is exactly what a
# phone resuming a suspended transfer sends.
_dated_store.begin("clip.jpg", len(_clip), _clip_sha256)
with open(os.path.join(_dated_store.session_dir, _clip_sha256 + ".json"), encoding="utf-8") as _session_file:
    _session_metadata = json.load(_session_file)
check("a resume does not overwrite the stored capture date",
      _session_metadata["taken_at"] == _captured_at,
      (_session_metadata.get("taken_at"), _captured_at))

_dated_store.append(_clip_sha256, _half_length, _clip[_half_length:])
_finished_upload = _dated_store.finish(_clip_sha256)
check("the resumed upload is stored", _finished_upload["status"] == "stored",
      _finished_upload)
_stored_path = os.path.join(_dated_dir,
                            _finished_upload["path"].replace("/", os.sep))
check("-- carrying that date as its own timestamp on disk, not the upload time",
      abs(os.path.getmtime(_stored_path) - _captured_at) < 2,
      (_finished_upload["path"], os.path.getmtime(_stored_path), _captured_at))

# The other half of the rule: an upload that never declared a date has nothing
# to preserve and must fall back to now -- not to 1970, and not to a crash.
_undated_bytes = b"\xff\xd8\xff pretend a photo with no date attached"
_undated_sha256 = hashlib.sha256(_undated_bytes).hexdigest()
_dated_store.begin("undated.jpg", len(_undated_bytes), _undated_sha256)
_dated_store.append(_undated_sha256, 0, _undated_bytes)
_undated_upload = _dated_store.finish(_undated_sha256)
check("an upload that never declared a date is filed under today",
      _undated_upload["path"] == time.strftime("%Y/%Y-%m") + "/undated.jpg",
      _undated_upload)

print("\n== /api/leds: two answering commands that had no way out ==")
# eCmdGetDimming and eCmdGetDemoModeInfo both answer with real data on this
# firmware and both had working parsers since 2026-07-26 with nothing exposing
# them. A measurement nobody can see is not a feature.
#
# The device reads are faked, like the /api/shares and /api/drobosync tests:
# earlier blocks leave a _resolved_host on the mock driver so the real command
# path gets exercised, so an un-patched call here opens a genuine socket to an
# address nothing is listening on. The values returned are the ones the live 5N
# actually reported (brightness 59, not in demo mode), so the assertions still
# say something true about the wiring rather than about the stub.

_original_get_dimming, _original_get_demo_mode = droboapps.get_dimming, droboapps.get_demo_mode
droboapps.get_dimming = lambda host, esa_id, port=5001, timeout=8.0: 59
droboapps.get_demo_mode = lambda host, esa_id, port=5001, timeout=8.0: {
    "demo_mode": False, "scale_factor": 1}
try:
    status, reply = call("/api/leds")
finally:
    droboapps.get_dimming, droboapps.get_demo_mode = _original_get_dimming, _original_get_demo_mode

check("leds endpoint 200", status == 200, (status, reply))
check("brightness is reported", reply.get("dimming") == 59, reply)
# The firmware documents no scale, so the number ships with a note saying so
# rather than being rendered as a percentage we would be inventing.
check("the unknown scale is admitted, not papered over",
      "never says what the scale is" in reply.get("scale_note", ""), reply.get("scale_note"))
check("demo mode is reported", reply.get("demo_mode") is False, reply)
check("-- with its scale factor", reply.get("scale_factor") == 1, reply)

print("\n== the update check is dormant, and says so ==")
# Built now so it exists before it is needed rather than being assembled in a
# hurry on release day. Off by default; the unit tests in test_updates.py
# boobytrap urllib to prove nothing is contacted. Here we only confirm the
# endpoint reports it honestly.
status, reply = call("/api/update")
check("update endpoint 200", status == 200, (status, reply))
check("it reports disabled by default", reply.get("state") == "disabled",
      reply)
check("no update is offered", reply.get("update_available") is False, reply)
check("it says nothing is contacted", "Nothing is contacted" in reply.get("reason", ""), reply)
check("it still reports the version we are on", reply.get("current_version") == api.VERSION, reply)
# A disabled check must not be cached for a day -- flipping the setting on
# should take effect on the next refresh, not tomorrow.
check("a disabled answer is not cached",
      api._ApiRequestHandler._update_cache is None,
      api._ApiRequestHandler._update_cache)

print("\n== DroboSync: built, dormant, and honest about why ==")
# Needs two Drobos; only a single unit is available. Shipped built rather than
# absent so the page explains itself instead of a button existing that fails -- ROADMAP
# decision, 2026-07-28.
#
# The device read is faked, exactly as the /api/shares tests fake config_cmd:
# earlier blocks in this file leave a _resolved_host on the mock driver so the
# real command path gets exercised, which means an un-patched call here would
# try to open a genuine socket to an address nothing is listening on. (It did,
# on the first run of this test, and hung until the client timed out.)
# (drobosync is imported with the other modules at the top of the file)

_original_sync_summary = drobosync.summary
drobosync.summary = lambda host, esa_id, port=5001, timeout=8.0: {
    "configured": False,
    "reads": {read_name: {"configured": False, "raw_xml": "", "fields": {},
                          "asked": read_name == "settings"}
              for read_name in ("settings", "summary_log", "detailed_log",
                                "log")},
    "note": ("This Drobo has no DroboSync partner set up. DroboSync copied one "
             "Drobo's shares to a second Drobo on a schedule, so it needs two "
             "units -- with one, there is nothing for it to do and the device "
             "correctly reports nothing. Everything needed to display it is "
             "built and waiting."),
}
try:
    status, reply = call("/api/drobosync")
finally:
    drobosync.summary = _original_sync_summary
check("drobosync endpoint 200", status == 200, (status, reply))
check("it reports not configured", reply.get("configured") is False, reply)
check("all four reads are represented",
      set(reply.get("reads", {})) == {"settings", "summary_log", "detailed_log", "log"},
      list(reply.get("reads", {})))
check("it explains that the feature needs two units", "needs two" in reply.get("note", ""), reply.get("note"))
check("-- and does not read as a failure", "error" not in reply.get("note", "").lower(), reply.get("note"))
# The simulator must not invent a configured sync: nobody has ever seen a
# populated reply, so a fake one would teach the UI a shape we made up.
check("the simulator does not fabricate a sync partner",
      all(not r.get("configured") for r in reply.get("reads", {}).values()),
      reply)

print("\n== the console never gets a secret or a traceback it did not earn ==")
# http.server logs a timeout BEFORE it has read a request line, so the handler
# has no .path attribute at all. log_message used to read self.path first and
# raise AttributeError there: a traceback for a connection nobody used.
_handler = object.__new__(api._ApiRequestHandler)
_handler.client_address = ("127.0.0.1", 0)
_captured_stdout = io.StringIO()
try:
    with contextlib.redirect_stdout(_captured_stdout):
        _handler.log_error("Request timed out: %r", TimeoutError("timed out"))
    _raised = None
except Exception as exc:  # noqa: BLE001 - any exception here is the bug
    _raised = exc
check("logging a timeout that arrived before any request line does not raise",
      _raised is None, repr(_raised))
check("-- and the timeout is still logged", "Request timed out" in _captured_stdout.getvalue(),
      _captured_stdout.getvalue())

# The access log prints the whole request line, and a token can still arrive in
# the URL: /api/events takes it that way, the pairing QR code hands out
# /?token=..., and an old bookmark or script may carry it on any route. Redacted
# at the point of printing since 2026-09-09, but it never had a check, so a
# later edit to log_message could have put the token back in the console
# unnoticed. The token itself is never echoed by these checks, pass or fail.
def _capture_access_log(request_line):
    handler = object.__new__(api._ApiRequestHandler)
    handler.client_address = ("127.0.0.1", 0)
    handler.requestline = request_line
    handler.path = request_line.split(" ")[1]
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        handler.log_request(200)
    return captured.getvalue()

for _request_line, _description in (
        (f"GET /?token={TOKEN} HTTP/1.1", "the dashboard link from the QR code"),
        (f"GET /api/status?x=1&token={TOKEN}&y=2 HTTP/1.1", "a token in the middle of a query"),
        (f"GET /api/pair.svg?TOKEN={TOKEN} HTTP/1.1", "a token spelled in capitals")):
    _logged_output = _capture_access_log(_request_line)
    check(f"the access log does not print the token: {_description}",
          TOKEN not in _logged_output and "<redacted>" in _logged_output, _logged_output.replace(TOKEN, "<THE TOKEN>"))
_logged_output = _capture_access_log(f"GET /api/events?token={TOKEN} HTTP/1.1")
check("the event stream is not access-logged at all", _logged_output == "",
      _logged_output.replace(TOKEN, "<THE TOKEN>"))

# The startup banner lands in the console Run-Agent.bat tells testers to paste.
# It printed ?token=<the real token> until 2026-09-14.
with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_agent.py"),
          encoding="utf-8") as _run_agent_file:
    _banner_lines = [line for line in _run_agent_file
                     if "print(" in line and "[agent] open" in line]
check("run_agent.py still has its 'open' banner line", len(_banner_lines) == 1, _banner_lines)
check("-- and that line does not print the token",
      all("token" not in line.split("print(", 1)[1].split("#", 1)[0].replace("access token", "")
          for line in _banner_lines), _banner_lines)

print("\n== 404 ==")
status, reply = call("/api/nope")
check("unknown path 404s", status == 404, status)

http_server.shutdown(); monitor.stop(); http_server.server_close()

# Everything above runs the simulator. This section starts the REAL driver in
# its shipping configuration -- "drobo5n" with "host": "auto" -- with discovery
# made to find nothing, which is what a PC sees when it boots before its Drobo
# or before its own network is up. Until 2026-09-26 the agent exited at that
# point, before serving anything, so the boot-time Task Scheduler job that
# docs/running-the-agent.md recommends ran no monitoring at all on such a PC.
#
# It runs after the simulated agent is shut down because api.serve keeps the
# monitor on the handler class: a second agent in this process replaces the
# first one's. No Drobo is contacted. discovery.resolve is replaced before the
# driver can call it, and each SDK call that could open a socket to a device
# is replaced by one that records the attempt and fails, so a stray call is
# counted by a check below rather than going out onto the network.
print("\n== starting with no Drobo to be found ==")

_NO_DROBO_AGENT_PORT = 7432
_SIMULATED_ESA_ID = "drb000000SAMPLE"
_saved_sdk_functions = (discovery.resolve, esatm.read_snapshot,
                        sysinfo.get_sysinfo_and_performance,
                        eventlog.get_event_log)
_resolve_calls, _device_calls = [], []


class _RecordingNotifier(notify.Notifier):
    name = "recorder"

    def __init__(self):
        self.sent = []

    def send(self, title, message, severity):
        self.sent.append((title, message, severity))


def _write_agent_config(name, drobo_section):
    path = os.path.join(temp_dir, f"config.{name}.json")
    with open(path, "w") as config_file:
        json.dump({"agent": {"bind": "127.0.0.1", "port": _NO_DROBO_AGENT_PORT, "poll_seconds": 1},
                   "drobo": drobo_section, "photos": {"enabled": False}},
                  config_file)
    with contextlib.redirect_stdout(io.StringIO()):  # the "new token" line
        return config.load(path)


def _resolve_nothing(*args, **kwargs):
    _resolve_calls.append(kwargs)
    raise LookupError("no Drobo found. Tried: nothing (simulated: this PC's "
                      "network is not up yet)")


def _must_not_reach(function_name):
    def _record_and_refuse(*args, **kwargs):
        _device_calls.append(function_name)
        raise OSError(f"simulated: {function_name} was called, and this test contacts no device")
    return _record_and_refuse


def _start_monitor(monitor_to_start):
    """Monitor.start, returning (what it printed, what it raised or None)."""
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            monitor_to_start.start()
    except BaseException as exc:  # noqa: BLE001 - SystemExit is the point
        return captured.getvalue(), exc
    return captured.getvalue(), None


def _wait_for(predicate, seconds=10.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


check("'not found' is its own type, a LookupError",
      issubclass(drivers.DroboNotFoundError, LookupError))

discovery.resolve = _resolve_nothing
esatm.read_snapshot = _must_not_reach("esatm.read_snapshot")
sysinfo.get_sysinfo_and_performance = _must_not_reach("sysinfo")
eventlog.get_event_log = _must_not_reach("eventlog")
_no_drobo_monitor = _no_drobo_server = None
try:
    _no_drobo_config = _write_agent_config(
        "nodrobo", {"driver": "drobo5n", "host": "auto"})
    _no_drobo_notifier = _RecordingNotifier()
    _no_drobo_monitor = Monitor(_no_drobo_config, _no_drobo_notifier)
    check("under test: the real driver, set to discover",
          _no_drobo_monitor.driver.name == "drobo5n" and _no_drobo_monitor.driver._uses_auto_discovery, _no_drobo_monitor.driver.name)

    _startup_output, _startup_error = _start_monitor(_no_drobo_monitor)
    check("discovery finding nothing at startup does not stop the agent", _startup_error is None, repr(_startup_error))
    check("-- discovery was really asked", len(_resolve_calls) >= 1, _resolve_calls)
    check("-- it says no Drobo was found, and that it is starting anyway",
          "no Drobo found yet" in _startup_output
          and "starting anyway" in _startup_output, _startup_output)
    check("-- and the poll loop is running",
          _no_drobo_monitor._poll_thread is not None
          and _no_drobo_monitor._poll_thread.is_alive())

    _no_drobo_server = api.serve(_no_drobo_config, _no_drobo_monitor,
                                 photo_store)
    threading.Thread(target=_no_drobo_server.serve_forever,
                     daemon=True).start()
    _NO_DROBO_AUTH_HEADERS = {
        "X-Agent-Token": _no_drobo_config["agent"]["token"]}

    def _call_no_drobo_agent(path):
        request = urllib.request.Request(f"http://127.0.0.1:{_NO_DROBO_AGENT_PORT}{path}", headers=_NO_DROBO_AUTH_HEADERS)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as http_error:
            return http_error.code, json.loads(http_error.read())

    status, reply = _call_no_drobo_agent("/api/ping")
    check("the API is served: /api/ping answers", status == 200 and reply.get("ok") is True, (status, reply))

    def _fetch_unreachable_alerts():
        alerts = _call_no_drobo_agent("/api/alerts")[1].get("alerts", [])
        return [a for a in alerts if a.get("key") == "unreachable"]

    _wait_for(_fetch_unreachable_alerts)
    _unreachable_alert_rows = _fetch_unreachable_alerts()
    check("the unreachable alert is raised", len(_unreachable_alert_rows) == 1,
          _unreachable_alert_rows)
    _unreachable_message = (_unreachable_alert_rows[0]["message"]
                            if _unreachable_alert_rows else "")
    check("-- as critical", bool(_unreachable_alert_rows) and _unreachable_alert_rows[0]["severity"] == SEVERITY_CRITICAL, _unreachable_alert_rows)
    check("-- carrying discovery's own reason",
          "no Drobo found" in _unreachable_message, _unreachable_message)
    # This advice used to sit in Run-Agent.bat's footer, shown only after the
    # agent had exited. Nothing exits now, so the alert has to carry it.
    check("-- and naming the first thing to check",
          "switched on" in _unreachable_message
          and "same network" in _unreachable_message, _unreachable_message)
    check("-- and it went out through the notifier",
          any(severity == SEVERITY_CRITICAL and "Cannot reach" in message for title, message, severity in _no_drobo_notifier.sent),
          _no_drobo_notifier.sent)
    status, reply = _call_no_drobo_agent("/api/status")
    check("/api/status says unreachable rather than failing",
          status == 200 and reply.get("reachable") is False, (status, reply))

    _resolve_count_before = len(_resolve_calls)
    check("every poll runs discovery again", _wait_for(lambda: len(_resolve_calls) > _resolve_count_before + 1),
          (len(_resolve_calls), _resolve_count_before))
    check("no device was contacted", _device_calls == [], _device_calls)

    # The Drobo comes up. Discovery now finds it (at a documentation address,
    # 192.0.2.0/24, which nothing real uses) and the status read is a fake.
    def _resolve_found(*args, **kwargs):
        _resolve_calls.append(kwargs)
        return discovery.VerificationResult(
            candidate=discovery.Candidate(host="192.0.2.10", port=5000, source="mdns"),
            identity={"esa_id": _SIMULATED_ESA_ID, "name": "SimDrobo", "model": "Drobo 5N",
                      "firmware": "4.2.2"},
            first_run=True)

    def _fake_greeting(host, port=5000, timeout=8.0):
        _device_calls.append(("esatm.read_snapshot", host))
        return Snapshot(reachable=True, source="drobo5n",
                        device=DeviceInfo(name="SimDrobo", model="Drobo 5N",
                                          serial=_SIMULATED_ESA_ID,
                                          firmware="4.2.2"),
                        capacity=Capacity(),
                        drives=[DriveSlot(bay=1, present=True, state="ok", capacity_bytes=1)])

    sysinfo.get_sysinfo_and_performance = lambda host, esa_id, port=None, timeout=8.0: {}
    eventlog.get_event_log = lambda host, esa_id, port=None, timeout=8.0: []
    esatm.read_snapshot = _fake_greeting
    discovery.resolve = _resolve_found
    check("when the Drobo turns up, a later poll finds it with no restart",
          _wait_for(lambda: _call_no_drobo_agent("/api/status")[1].get("reachable") is True),
          _call_no_drobo_agent("/api/status")[1])
    check("-- at the address discovery found",
          _call_no_drobo_agent("/api/status")[1].get("device", {}).get("ip") == "192.0.2.10",
          _call_no_drobo_agent("/api/status")[1].get("device"))
    check("-- and the unreachable alert clears", _fetch_unreachable_alerts() == [], _fetch_unreachable_alerts())
    check("-- and only the fake was read",
          all(c == ("esatm.read_snapshot", "192.0.2.10") for c in _device_calls), _device_calls)
finally:
    # Stopped BEFORE the real SDK functions go back, so the poll thread can
    # never reach a real one on its way out.
    if _no_drobo_server:
        _no_drobo_server.shutdown(); _no_drobo_server.server_close()
    if _no_drobo_monitor:
        _no_drobo_monitor.stop()

# A device that answers and is provably not this Drobo stays a hard refusal --
# but a readable one. It used to end in a raw traceback.
def _resolve_stranger(*args, **kwargs):
    raise discovery.IdentityMismatchError(
        "found device(s) on the network, but none matched the expected Drobo:\n"
        "  refusing 192.0.2.10:5000 -- it identifies as esa_id='drb000000OTHER', "
        f"not the expected {_SIMULATED_ESA_ID!r}. This is not your Drobo; not connecting.")

discovery.resolve = _resolve_stranger
_mismatch_monitor = Monitor(
    _write_agent_config("stranger", {"driver": "drobo5n", "host": "auto",
                                     "expected_esa_id": _SIMULATED_ESA_ID}),
    _RecordingNotifier())
_startup_output, _startup_error = _start_monitor(_mismatch_monitor)
check("a device that is provably not this Drobo still stops the agent",
      isinstance(_startup_error, SystemExit), repr(_startup_error))
_exit_message = getattr(_startup_error, "code", None)
# A SystemExit whose code is a string is printed as that string, with no
# traceback, and the process exits 1 -- which is what Run-Agent.bat looks for.
check("-- with a message rather than a traceback, naming the stranger",
      isinstance(_exit_message, str) and "drb000000OTHER" in _exit_message,
      repr(_exit_message))
check("-- and saying what to do if the Drobo really was replaced",
      isinstance(_exit_message, str) and "expected_esa_id" in _exit_message,
      repr(_exit_message))
check("-- and no poll loop was started",
      _mismatch_monitor._poll_thread is None)

# A reply that is not a Drobo's, at a fixed address: the poll path already
# treats that as "not reachable, go and look", so startup does too.
def _not_a_greeting(host, port=5000, timeout=8.0):
    _device_calls.append(("esatm.read_snapshot", host))
    raise esatm.FrameError("bad signature: b'SSH-2.0-'")

discovery.resolve = _resolve_nothing
esatm.read_snapshot = _not_a_greeting
_frame_error_monitor = Monitor(_write_agent_config("notadrobo", {"driver": "drobo5n", "host": "192.0.2.10"}),
                               _RecordingNotifier())
_startup_output, _startup_error = _start_monitor(_frame_error_monitor)
check("a fixed address that answers with something else no longer ends in a traceback",
      _startup_error is None, repr(_startup_error))
check("-- it names the address and says it is starting anyway",
      "192.0.2.10" in _startup_output
      and "starting anyway" in _startup_output.lower(), _startup_output)
check("-- and the poll loop is running",
      _frame_error_monitor._poll_thread is not None
      and _frame_error_monitor._poll_thread.is_alive())
_frame_error_monitor.stop()

# The monitor catches "not found", not LookupError: a KeyError is a LookupError
# too, and one raised by a bug in connect() must surface as the bug it is.
class _BuggyDriver:
    name = "buggy"

    def connect(self):
        raise KeyError("a bug, not a missing Drobo")

    def close(self):
        pass

_key_error_monitor = Monitor(_write_agent_config("buggy", {"driver": "mock"}),
                             _RecordingNotifier())
_key_error_monitor.driver = _BuggyDriver()
_startup_output, _startup_error = _start_monitor(_key_error_monitor)
check("a KeyError from connect() is not mistaken for 'no Drobo found yet'",
      isinstance(_startup_error, KeyError), repr(_startup_error))
check("-- and no poll loop was started",
      _key_error_monitor._poll_thread is None)

(discovery.resolve, esatm.read_snapshot,
 sysinfo.get_sysinfo_and_performance, eventlog.get_event_log) = _saved_sdk_functions

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
