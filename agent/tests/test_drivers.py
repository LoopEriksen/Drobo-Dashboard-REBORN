"""
Tests for Drobo5NDriver -- _read_sysinfo(), the one part of the real
driver that has actually run against a live 5N (see drivers.py's
docstring on it, 2026-07-26), and _read_event_log(), which follows its
pattern.

Runs entirely offline: nasd.sysinfo.get_sysinfo_and_performance and
nasd.eventlog.get_event_log are monkeypatched to fakes that never open a
socket. Covers the things that make these methods worth pinning down:

  * the rate limit (sysinfo_seconds / eventlog_seconds) -- a second call
    inside the window must not reach the device at all
  * a device failure leaves the last-known-good reading (and its
    original timestamp) intact rather than blanking it
  * the derived uptime in read_snapshot() keeps counting between reads
    without asking the device again
  * uptime and temperature each age from their own reading, so a reply
    carrying only one of them leaves the other's time alone
  * performance figures carry the time they were measured, and a failed
    fetch keeps both the figures and that original time
  * an event-log fetch failure degrades the snapshot to the cached
    (possibly empty) log without ever breaking the poll

    py test_drivers.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drobo_agent import drivers  # noqa: E402
from drobo_agent.models import DeviceInfo, Snapshot  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import esatm as nasd_esatm  # noqa: E402
from drobo_nasd import eventlog as nasd_eventlog  # noqa: E402
from drobo_nasd import sysinfo as nasd_sysinfo  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


HOST = "10.0.0.5"
ESA_ID = "drb000000SAMPLE"

_original_get_sysinfo_and_performance = (
    nasd_sysinfo.get_sysinfo_and_performance)
_original_read_snapshot = nasd_esatm.read_snapshot
_original_get_event_log = nasd_eventlog.get_event_log


def _eventlog_quiet(host, esa_id, port=None, timeout=8.0):
    """File-wide default: read_snapshot() now also fetches the device's
    event log, and every section except the dedicated one at the bottom
    is about OTHER reads -- without this stub each fresh driver's first
    snapshot would open a REAL socket to HOST and stall the suite for a
    full timeout."""
    return []


nasd_eventlog.get_event_log = _eventlog_quiet


def make_answering_sysinfo(calls, temperature_c, uptime_seconds):
    def _fake(host, esa_id, port=None, timeout=8.0):
        calls.append((host, esa_id))
        return {
            "temperature_c": temperature_c,
            "uptime_seconds": uptime_seconds,
        }
    return _fake


print("== default rate limit is two minutes, as documented ==")
default_driver = drivers.Drobo5NDriver({"host": HOST})
check("sysinfo_seconds defaults to 120.0",
      default_driver.sysinfo_seconds == 120.0,
      default_driver.sysinfo_seconds)
check(
    "no reading yet means temperature_c is None, not 0",
    default_driver._temperature_c is None,
)
check("no reading yet means uptime is None, not 0",
      default_driver._uptime_seconds is None)

print(
    "\n== rate limit: a second call inside the window never reaches the "
    "device =="
)
calls = []
nasd_sysinfo.get_sysinfo_and_performance = make_answering_sysinfo(
    calls, temperature_c=36, uptime_seconds=1000
)
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 100000})
    driver._known_esa_id = ESA_ID
    driver._read_sysinfo(HOST)
    check("first call reaches the device", len(calls) == 1, calls)
    check("temperature cached from that call", driver._temperature_c == 36,
          driver._temperature_c)
    check("uptime cached from that call", driver._uptime_seconds == 1000,
          driver._uptime_seconds)
    first_temperature_read_at = driver._temperature_read_at
    check("a timestamp was stamped", first_temperature_read_at is not None)

    driver._read_sysinfo(HOST)
    driver._read_sysinfo(HOST)
    check("further calls inside sysinfo_seconds do not reach the device",
          len(calls) == 1, calls)
    check("cached temperature is unchanged", driver._temperature_c == 36,
          driver._temperature_c)
    check("cached timestamp is unchanged (no fresh read happened)",
          driver._temperature_read_at == first_temperature_read_at,
          (driver._temperature_read_at, first_temperature_read_at))
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )

print("\n== rate limit: once the window elapses, a fresh read is allowed ==")
calls = []
nasd_sysinfo.get_sysinfo_and_performance = make_answering_sysinfo(
    calls, temperature_c=40, uptime_seconds=2000
)
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 0.05})
    driver._known_esa_id = ESA_ID
    driver._read_sysinfo(HOST)
    check("first call reaches the device", len(calls) == 1, calls)
    time.sleep(0.08)
    driver._read_sysinfo(HOST)
    check("a call after the window elapsed reaches the device again",
          len(calls) == 2, calls)
    check("temperature refreshed to the new value",
          driver._temperature_c == 40,
          driver._temperature_c)
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )

print("\n== no esa_id yet: the command port needs a serial we don't have ==")
calls = []
nasd_sysinfo.get_sysinfo_and_performance = make_answering_sysinfo(
    calls, temperature_c=99, uptime_seconds=1
)
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 0.01})
    # _known_esa_id left blank -- exactly the state before a status read
    # ever succeeds.
    check("starts blank", driver._known_esa_id == "", driver._known_esa_id)
    driver._read_sysinfo(HOST)
    check("no esa_id means no attempt at all", calls == [], calls)
    check("temperature stays None", driver._temperature_c is None,
          driver._temperature_c)
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )

print(
    "\n== last-known-good survives a device failure, with its original "
    "timestamp =="
)
calls = []
nasd_sysinfo.get_sysinfo_and_performance = make_answering_sysinfo(
    calls, temperature_c=42, uptime_seconds=5000
)
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 0.05})
    driver._known_esa_id = ESA_ID
    driver._read_sysinfo(HOST)
    check("first good read cached", driver._temperature_c == 42,
          driver._temperature_c)
    good_temperature_read_at = driver._temperature_read_at
    time.sleep(0.08)

    def _device_unreachable(host, esa_id, port=None, timeout=8.0):
        calls.append((host, esa_id))
        raise OSError("device unreachable (simulated)")

    nasd_sysinfo.get_sysinfo_and_performance = _device_unreachable
    driver._read_sysinfo(HOST)
    check(
        "a failed read still counts as an attempt (rate limit still applies)",
        len(calls) == 2,
        calls,
    )
    check(
        "last known good temperature is kept, not blanked",
        driver._temperature_c == 42,
        driver._temperature_c,
    )
    check(
        "its original timestamp is kept too -- this reading really is that "
        "old",
        driver._temperature_read_at == good_temperature_read_at,
        (driver._temperature_read_at, good_temperature_read_at),
    )
    check("uptime is kept too", driver._uptime_seconds == 5000,
          driver._uptime_seconds)

    # ConfigError is the other declared failure mode
    # (get_sysinfo_and_performance raises SysInfoError, a ConfigError
    # subclass, e.g. on a bad login/reply) and must behave identically
    # -- neither wipes the cache.
    def _command_port_refused(host, esa_id, port=None, timeout=8.0):
        calls.append((host, esa_id))
        raise config_cmd.ConfigError("command port refused (simulated)")

    nasd_sysinfo.get_sysinfo_and_performance = _command_port_refused
    time.sleep(0.08)
    driver._read_sysinfo(HOST)
    check(
        "ConfigError also leaves the cached temperature intact",
        driver._temperature_c == 42,
        driver._temperature_c,
    )
    check("ConfigError also leaves the cached timestamp intact",
          driver._temperature_read_at == good_temperature_read_at,
          (driver._temperature_read_at, good_temperature_read_at))
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )

print("\n== _read_sysinfo never raises on the two declared failure modes ==")
for declared_error, error_name in (
    (OSError("boom"), "OSError"),
    (config_cmd.ConfigError("boom"), "ConfigError"),
):
    def _raise_declared_error(host, esa_id, port=None, timeout=8.0,
                              _error=declared_error):
        raise _error
    nasd_sysinfo.get_sysinfo_and_performance = _raise_declared_error
    try:
        driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 0.0})
        driver._known_esa_id = ESA_ID
        try:
            driver._read_sysinfo(HOST)
            check(f"{error_name} is swallowed, not propagated", True)
        except Exception as caught:  # noqa: BLE001 -- this IS the check
            check(f"{error_name} is swallowed, not propagated", False, caught)
    finally:
        nasd_sysinfo.get_sysinfo_and_performance = (
            _original_get_sysinfo_and_performance
        )


print("\n== an undeclared exception is NOT silently swallowed ==")


# The docstring promises "never raises" only in the sense of "a device
# failure is not an error" -- the except clause is scoped to
# (OSError, config_cmd.ConfigError). Something else going wrong (a real
# bug) must still surface, not vanish into "no fresh reading".
def _raise_undeclared_bug(host, esa_id, port=None, timeout=8.0):
    raise ValueError("this is a bug, not a device failure")


nasd_sysinfo.get_sysinfo_and_performance = _raise_undeclared_bug
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 0.0})
    driver._known_esa_id = ESA_ID
    try:
        driver._read_sysinfo(HOST)
        check(
            "an unexpected exception type propagates rather than being hidden",
            False,
        )
    except ValueError:
        check(
            "an unexpected exception type propagates rather than being hidden",
            True,
        )
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )

print(
    "\n== derived uptime keeps counting between reads, without re-asking the "
    "device =="
)
sysinfo_calls = []
esatm_calls = []
nasd_sysinfo.get_sysinfo_and_performance = make_answering_sysinfo(
    sysinfo_calls, temperature_c=33, uptime_seconds=1000
)


def fake_read_snapshot(host, port=None, timeout=8.0):
    esatm_calls.append((host, port))
    return Snapshot(
        reachable=True,
        source="drobo5n",
        device=DeviceInfo(name="TestDrobo", model="Drobo 5N", serial=ESA_ID),
    )


nasd_esatm.read_snapshot = fake_read_snapshot
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 100000})
    driver._resolved_host = HOST
    driver._resolved_port = 5000
    driver._known_esa_id = ESA_ID

    first_snapshot = driver.read_snapshot()
    check(
        "temperature carried onto the snapshot",
        first_snapshot.device.temperature_c == 33,
        first_snapshot.device.temperature_c,
    )
    check(
        "temperature_at carried onto the snapshot",
        first_snapshot.device.temperature_at is not None,
    )
    check(
        "uptime derived onto the snapshot",
        first_snapshot.device.uptime_seconds == 1000
        or first_snapshot.device.uptime_seconds
        == 1001,  # a whole second may have ticked
        first_snapshot.device.uptime_seconds,
    )
    check(
        "only one sysinfo call happened so far",
        len(sysinfo_calls) == 1,
        sysinfo_calls,
    )

    time.sleep(1.1)
    second_snapshot = driver.read_snapshot()
    check("a second status read happened", len(esatm_calls) == 2, esatm_calls)
    check("sysinfo was NOT asked again -- still inside the rate-limit window",
          len(sysinfo_calls) == 1, sysinfo_calls)
    check("but the derived uptime kept counting up between reads",
          second_snapshot.device.uptime_seconds
          > first_snapshot.device.uptime_seconds,
          (first_snapshot.device.uptime_seconds,
           second_snapshot.device.uptime_seconds))
    check(
        "the underlying temperature reading is untouched (still the same "
        "cached value)",
        second_snapshot.device.temperature_c == 33,
        second_snapshot.device.temperature_c,
    )
    check(
        "temperature_at is unchanged too -- it really is the same, aging, "
        "reading",
        second_snapshot.device.temperature_at
        == first_snapshot.device.temperature_at,
        (
            first_snapshot.device.temperature_at,
            second_snapshot.device.temperature_at,
        ),
    )
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )
    nasd_esatm.read_snapshot = _original_read_snapshot

print(
    "\n== before any successful sysinfo read, the greeting's own uptime is "
    "left alone =="
)
esatm_calls = []


def fake_read_snapshot_with_uptime(host, port=None, timeout=8.0):
    esatm_calls.append((host, port))
    return Snapshot(
        reachable=True,
        source="drobo5n",
        device=DeviceInfo(
            name="TestDrobo",
            model="Drobo 5N",
            serial=ESA_ID,
            uptime_seconds=None,
        ),
    )


def _never_answers(host, esa_id, port=None, timeout=8.0):
    raise OSError("simulated: command port never answers")


nasd_sysinfo.get_sysinfo_and_performance = _never_answers
nasd_esatm.read_snapshot = fake_read_snapshot_with_uptime
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 0.0})
    driver._resolved_host = HOST
    driver._resolved_port = 5000
    driver._known_esa_id = ESA_ID
    snapshot = driver.read_snapshot()
    check("no temperature ever obtained -> temperature_c stays None",
          snapshot.device.temperature_c is None, snapshot.device.temperature_c)
    check("no successful sysinfo read -> derived-uptime branch never fires, "
          "greeting's own (None) uptime is left alone",
          snapshot.device.uptime_seconds is None,
          snapshot.device.uptime_seconds)
finally:
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )
    nasd_esatm.read_snapshot = _original_read_snapshot

print("\n== uptime ages from its own reading, not the temperature's ==")
# A reply may carry one reading without the other: parse_sysinfo drops
# an implausible temperature and keeps the uptime. Until this was fixed,
# both readings shared one timestamp, so an uptime-only reply was
# counted on from the last TEMPERATURE reading -- adding that gap a
# second time -- and a temperature-only reply restarted an old uptime
# from now.


class _FakeClock:
    """Stands in for drivers.time, so no window needs a real sleep."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


FAKE_START = 1_000_000.0
fake_clock = _FakeClock(FAKE_START)
sysinfo_replies = [
    {"temperature_c": 36, "uptime_seconds": 5000},
    {"temperature_c": None, "uptime_seconds": 5120},
    {"temperature_c": 37, "uptime_seconds": None},
]


def _next_scripted_reply(host, esa_id, port=None, timeout=8.0):
    return sysinfo_replies.pop(0)


_real_time_module = drivers.time
drivers.time = fake_clock
nasd_sysinfo.get_sysinfo_and_performance = _next_scripted_reply
nasd_esatm.read_snapshot = fake_read_snapshot
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 100})
    driver._resolved_host = HOST
    driver._resolved_port = 5000
    driver._known_esa_id = ESA_ID

    snapshot = driver.read_snapshot()
    check("both readings together: uptime is the device's own figure",
          snapshot.device.uptime_seconds == 5000,
          snapshot.device.uptime_seconds)

    fake_clock.now = FAKE_START + 120
    snapshot = driver.read_snapshot()
    check("an uptime-only reply is not counted on from the old temperature",
          snapshot.device.uptime_seconds == 5120,
          snapshot.device.uptime_seconds)
    check("-- and the temperature keeps its own, older timestamp",
          snapshot.device.temperature_c == 36
          and snapshot.device.temperature_at == FAKE_START,
          (snapshot.device.temperature_c, snapshot.device.temperature_at))

    fake_clock.now = FAKE_START + 240
    snapshot = driver.read_snapshot()
    check("a temperature-only reply does not restart the old uptime",
          snapshot.device.uptime_seconds == 5240,
          snapshot.device.uptime_seconds)
    check("-- while the temperature and its timestamp move on",
          snapshot.device.temperature_c == 37
          and snapshot.device.temperature_at == FAKE_START + 240,
          (snapshot.device.temperature_c, snapshot.device.temperature_at))

    sysinfo_replies[:] = [{"temperature_c": None, "uptime_seconds": 700}]
    fresh = drivers.Drobo5NDriver({"host": HOST})
    fresh._resolved_host = HOST
    fresh._resolved_port = 5000
    fresh._known_esa_id = ESA_ID
    snapshot = fresh.read_snapshot()
    check("an uptime-only first reply stamps no temperature time",
          snapshot.device.temperature_c is None
          and snapshot.device.temperature_at is None,
          (snapshot.device.temperature_c, snapshot.device.temperature_at))
    check("-- but its uptime is still shown",
          snapshot.device.uptime_seconds == 700,
          snapshot.device.uptime_seconds)
finally:
    drivers.time = _real_time_module
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance)
    nasd_esatm.read_snapshot = _original_read_snapshot

print("\n== performance figures carry the time they were measured ==")
# A failed fetch keeps the last figures, as it keeps the temperature,
# so their measured_at must stay at the time they arrived: the UIs show
# that age, and kept figures stamped with the poll time would look
# fresh.
PERF_FIGURES = {"read_mb_per_s": 12, "write_mb_per_s": 3, "iops": 40,
                "tier_iops": 0}
PERF_UNANSWERED = {"read_mb_per_s": None, "write_mb_per_s": None,
                   "iops": None, "tier_iops": None}
perf_replies = [
    {"temperature_c": 36, "uptime_seconds": 5000,
     "performance": PERF_FIGURES},
    OSError("simulated: command port stops answering"),
    # sysinfo answered but eCmdGetPerformance did not (sysinfo.py then
    # returns None for it), or answered with no usable figures.
    {"temperature_c": 37, "uptime_seconds": 5240, "performance": None},
    {"temperature_c": 37, "uptime_seconds": 5360,
     "performance": PERF_UNANSWERED},
    {"temperature_c": 38, "uptime_seconds": 5480,
     "performance": {**PERF_FIGURES, "read_mb_per_s": 20}},
]


def _next_perf_reply(host, esa_id, port=None, timeout=8.0):
    reply = perf_replies.pop(0)
    if isinstance(reply, Exception):
        raise reply
    return reply


fake_clock = _FakeClock(FAKE_START)
drivers.time = fake_clock
nasd_sysinfo.get_sysinfo_and_performance = _next_perf_reply
nasd_esatm.read_snapshot = fake_read_snapshot
try:
    driver = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 100})
    driver._resolved_host = HOST
    driver._resolved_port = 5000
    driver._known_esa_id = ESA_ID

    perf = driver.read_snapshot().performance
    check("a fresh reading is stamped with the time it arrived",
          perf.measured and perf.read_mb_per_s == 12
          and perf.measured_at == FAKE_START,
          perf)
    check("-- and the stamp is served in the snapshot's JSON",
          driver.read_snapshot().to_dict()["performance"]["measured_at"]
          == FAKE_START)

    fake_clock.now = FAKE_START + 120
    perf = driver.read_snapshot().performance
    check("a failed fetch keeps the figures with their OLD time",
          perf.measured and perf.read_mb_per_s == 12
          and perf.measured_at == FAKE_START,
          perf)

    fake_clock.now = FAKE_START + 240
    snapshot = driver.read_snapshot()
    check("a reply without performance keeps the old time too",
          snapshot.performance.read_mb_per_s == 12
          and snapshot.performance.measured_at == FAKE_START,
          snapshot.performance)
    check("-- while the temperature in that reply is stamped afresh",
          snapshot.device.temperature_at == FAKE_START + 240,
          snapshot.device.temperature_at)

    fake_clock.now = FAKE_START + 360
    perf = driver.read_snapshot().performance
    check("figures that are all unanswered keep the old time as well",
          perf.read_mb_per_s == 12 and perf.measured_at == FAKE_START,
          perf)

    fake_clock.now = FAKE_START + 480
    perf = driver.read_snapshot().performance
    check("the next good reading moves figures and time on together",
          perf.read_mb_per_s == 20
          and perf.measured_at == FAKE_START + 480,
          perf)

    never = drivers.Drobo5NDriver({"host": HOST, "sysinfo_seconds": 100})
    never._resolved_host = HOST
    never._resolved_port = 5000
    never._known_esa_id = ESA_ID
    perf_replies[:] = [OSError("simulated: never answers")]
    perf = never.read_snapshot().performance
    check("no reading ever: not measured, and no time either",
          not perf.measured and perf.measured_at is None, perf)

    mock_perf = drivers.MockDriver({}).read_snapshot().performance
    check("the mock driver stamps its simulated figures with now",
          mock_perf.measured
          and mock_perf.measured_at == fake_clock.now,
          mock_perf)
finally:
    drivers.time = _real_time_module
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance)
    nasd_esatm.read_snapshot = _original_read_snapshot

print(
    "\n== the device's own event log rides the snapshot; a failure never "
    "breaks the poll =="
)
eventlog_calls = []
esatm_calls = []
nasd_sysinfo.get_sysinfo_and_performance = make_answering_sysinfo(
    [], temperature_c=30, uptime_seconds=10
)
nasd_esatm.read_snapshot = fake_read_snapshot


def fake_event_log(host, esa_id, port=None, timeout=8.0):
    eventlog_calls.append((host, esa_id))
    return nasd_eventlog.entries_from_lines(["line one", "line two"])


nasd_eventlog.get_event_log = fake_event_log
try:
    driver = drivers.Drobo5NDriver({"host": HOST})
    driver._resolved_host = HOST
    driver._resolved_port = 5000
    driver._known_esa_id = ESA_ID
    check("eventlog_seconds defaults to 600.0",
          driver.eventlog_seconds == 600.0,
          driver.eventlog_seconds)

    first_snapshot = driver.read_snapshot()
    check(
        "first snapshot fetched the log",
        len(eventlog_calls) == 1,
        eventlog_calls,
    )
    check("entries carried onto the snapshot verbatim",
          [entry.message for entry in first_snapshot.event_log]
          == ["line one", "line two"],
          first_snapshot.event_log)
    check("the SDK's NOT_DECODED markers pass through untouched",
          all(entry.ts == nasd_eventlog.TIMESTAMP_NOT_DECODED
              and entry.severity == nasd_eventlog.SEVERITY_NOT_DECODED
              for entry in first_snapshot.event_log))

    second_snapshot = driver.read_snapshot()
    check("a second snapshot inside eventlog_seconds does not re-fetch",
          len(eventlog_calls) == 1, eventlog_calls)
    check("the cached log still rides the snapshot",
          [entry.message for entry in second_snapshot.event_log]
          == ["line one", "line two"])

    def _eventlog_down(host, esa_id, port=None, timeout=8.0):
        eventlog_calls.append((host, esa_id))
        raise nasd_eventlog.EventLogError("connection torn down (simulated)")

    nasd_eventlog.get_event_log = _eventlog_down
    # step past the rate limit without a 600s sleep
    driver._eventlog_tried_at = 0.0
    after_failure_snapshot = driver.read_snapshot()
    check(
        "the failed fetch was attempted",
        len(eventlog_calls) == 2,
        eventlog_calls,
    )
    check("a failed fetch keeps the last good log, and the poll survives",
          [entry.message for entry in after_failure_snapshot.event_log]
          == ["line one", "line two"],
          after_failure_snapshot.event_log)

    # A driver whose fetch has NEVER succeeded degrades to an empty list
    # -- for both declared failure modes, EventLogError and OSError.
    def _eventlog_unreachable(host, esa_id, port=None, timeout=8.0):
        raise OSError("device unreachable (simulated)")

    for failing_fetch, error_name in ((_eventlog_down, "EventLogError"),
                                      (_eventlog_unreachable, "OSError")):
        nasd_eventlog.get_event_log = failing_fetch
        fresh_driver = drivers.Drobo5NDriver({"host": HOST})
        fresh_driver._resolved_host = HOST
        fresh_driver._resolved_port = 5000
        fresh_driver._known_esa_id = ESA_ID
        never_fetched_snapshot = fresh_driver.read_snapshot()
        check(
            f"{error_name} with nothing cached degrades to an empty list, "
            "not a raise",
            never_fetched_snapshot.event_log == [],
            never_fetched_snapshot.event_log,
        )
finally:
    nasd_eventlog.get_event_log = _original_get_event_log
    nasd_sysinfo.get_sysinfo_and_performance = (
        _original_get_sysinfo_and_performance
    )
    nasd_esatm.read_snapshot = _original_read_snapshot

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
