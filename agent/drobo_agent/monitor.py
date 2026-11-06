"""
The watcher.

Polls the Drobo on a timer, keeps a rolling history, works out which
alerts are active, and pushes notifications when something changes. This
is the piece that makes the agent worth running at all -- it's awake
when you aren't.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque

from drobo_nasd import discovery, esatm, firmware, netcheck

from . import drivers
from .models import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATE_FAILED,
    STATE_WARNING,
    Alert,
    Snapshot,
)


# Health-telemetry baseline: observed on ONE healthy Drobo 5N, firmware
# 4.3.1, 2026-07-24. NOT a specification -- we don't know what these
# codes mean, only what a working unit reports. A deviation is flagged
# as "changed, worth a look", never as an assertion about what the new
# value means. Keyed by the PackHealth attribute name so
# _evaluate_alerts can walk it generically.
_PACK_STATUS_BASELINE = {
    "disk_pack_status": 0,
    "dnas_status": 6,
    "device_status": 98304,
    "status_ex": 0,
}


def _format_bytes(size_bytes: float) -> str:
    """Render a byte count the way the capacity alert text shows it.

    Decimal units (1 KB = 1000 bytes), because that is how drive and
    Drobo capacities are sold and labelled, so the free space an alert
    quotes matches the numbers on the box.
    """
    scaled_size = size_bytes
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(scaled_size) < 1000 or unit == "PB":
            if unit == "B":
                return f"{int(scaled_size)} B"
            return f"{scaled_size:.1f} {unit}"
        scaled_size /= 1000.0
    return f"{scaled_size:.1f} PB"


# -- alert rules -------------------------------------------------------
#
# One function per rule, each called by Monitor._evaluate_alerts. Every
# rule takes the same `seen` dict (alert key -> (severity, message)) and
# adds an entry for each condition that holds; none of them raises or
# sends anything itself. They stay in this file and keep the literal
# seen[...] subscripts, because test_notification_simulation reads
# this source to find every alert key the agent can raise.


def _unreachable_rule(
    seen: dict[str, tuple[str, str]], snapshot: Snapshot,
    network_hint: dict | None,
) -> None:
    """
    The Drobo did not answer. Raised alone: with no reading, no other
    rule has anything true to say.
    """
    error_text = " ".join((snapshot.error or "no response").split())
    if network_hint:
        # Kept as ONE alert rather than raised as a second: there is a
        # single problem here, and a critical "cannot reach" sitting
        # next to a separate note explaining it would read as two
        # unrelated events instead of a cause and its effect.
        #
        # The explanation goes BEFORE the driver's own error, and that
        # ordering is the point. Left the other way round, the useful
        # sentence lands after a wall of diagnostics -- and in this
        # exact case the diagnostics actively mislead: discovery's
        # failure message ends with "set host in config.json", which is
        # the wrong advice when the host is already correct and the PC
        # is simply on another network. The raw error is still here in
        # full, attributed and last, where a technical reader can find
        # it and nobody else has to wade through it.
        message = (
            f"Cannot reach the Drobo. {network_hint['message']} "
            f"(What the agent tried: {error_text})")
    else:
        message = f"Cannot reach the Drobo: {error_text}"
    seen["unreachable"] = (SEVERITY_CRITICAL, message)


def _drive_rules(
    seen: dict[str, tuple[str, str]], drives: list, alert_config: dict,
) -> None:
    """Every rule about one drive, for each bay that holds one."""
    for drive in drives:
        if not drive.present:
            continue
        _drive_state_rules(seen, drive)
        _drive_temperature_rule(seen, drive, alert_config)


def _drive_state_rules(seen: dict[str, tuple[str, str]], drive) -> None:
    """A failed or troubled drive, and any drive reporting errors."""
    where = f"bay {drive.bay}"
    if drive.state == STATE_FAILED:
        seen[f"drive-failed-{drive.bay}"] = (
            SEVERITY_CRITICAL,
            f"Drive in {where} has failed "
            f"({drive.model or 'unknown model'}). "
            "Replace it as soon as possible.",
        )
    elif drive.state == STATE_WARNING:
        seen[f"drive-warning-{drive.bay}"] = (
            SEVERITY_WARNING,
            f"Drive in {where} reports a problem. "
            f"{drive.note}".strip(),
        )
    # mErrorCount is unambiguous -- above zero is a real problem,
    # regardless of the drive's overall state.
    if drive.error_count > 0:
        seen[f"drive-errors-{drive.bay}"] = (
            SEVERITY_WARNING,
            f"Drive in {where} has reported {drive.error_count} "
            f"error(s) ({drive.model or 'unknown model'}).",
        )


def _drive_temperature_rule(
    seen: dict[str, tuple[str, str]], drive, alert_config: dict,
) -> None:
    """
    A drive running hot. Silent when the drive reports no temperature,
    which on a Drobo 5N is every drive (see _chassis_temperature_rule).
    """
    drive_temperature_c = drive.temperature_c
    if drive_temperature_c is None:
        return
    where = f"bay {drive.bay}"
    if drive_temperature_c >= alert_config["temperature_critical_c"]:
        seen[f"temp-critical-{drive.bay}"] = (
            SEVERITY_CRITICAL,
            f"Drive in {where} is {drive_temperature_c:.1f} C "
            f"-- above the "
            f"{alert_config['temperature_critical_c']:.0f} C "
            f"critical threshold.",
        )
    elif drive_temperature_c >= alert_config["temperature_warning_c"]:
        seen[f"temp-warning-{drive.bay}"] = (
            SEVERITY_WARNING,
            f"Drive in {where} is running warm at "
            f"{drive_temperature_c:.1f} C.",
        )


def _chassis_temperature_rule(
    seen: dict[str, tuple[str, str]], chassis_temperature_c: float | None,
    alert_config: dict,
) -> None:
    """
    Chassis temperature, from eCmdGetSysInfo on the command port.

    The per-drive rule never fires on a Drobo 5N: the status greeting
    reports mTemperature as 0 for every slot, so drive.temperature_c is
    always None. Until this rule existed, the dashboard displayed a real
    chassis temperature that absolutely nothing was watching -- a Drobo
    could cook itself and no alert would ever be raised.

    Same thresholds as the per-drive rule. They are a whole-box reading
    rather than a per-disk one, so the wording says so; a 5N in a
    cupboard on a warm day is the case this is for.
    """
    if chassis_temperature_c is None:
        return
    if chassis_temperature_c >= alert_config["temperature_critical_c"]:
        seen["chassis-temp-critical"] = (
            SEVERITY_CRITICAL,
            f"The Drobo is {chassis_temperature_c} C inside -- "
            f"above the "
            f"{alert_config['temperature_critical_c']:.0f} C "
            f"critical threshold. "
            "Check it has room to breathe and "
            "that the fan is running.",
        )
    elif chassis_temperature_c >= alert_config["temperature_warning_c"]:
        seen["chassis-temp-warning"] = (
            SEVERITY_WARNING,
            f"The Drobo is running warm at "
            f"{chassis_temperature_c} C inside. "
            "Worth checking it has space around it "
            "and clear vents.",
        )


def _battery_rule(seen: dict[str, tuple[str, str]], battery) -> None:
    """A failed or weak cache battery."""
    if battery.state == STATE_FAILED:
        battery_advice = (battery.note
                          or "Replace it as soon as possible.")
        seen["battery-failed"] = (
            SEVERITY_CRITICAL,
            f"Cache battery has failed. {battery_advice}".strip(),
        )
    elif battery.state == STATE_WARNING:
        seen["battery-warning"] = (
            SEVERITY_WARNING,
            f"Cache battery is weak. {battery.note}".strip(),
        )


def _power_rule(seen: dict[str, tuple[str, str]], power) -> None:
    """
    A failed or troubled power supply, the same shape as the battery
    rule.

    Found missing by the 2026-08-12 notification simulation: the mock
    could inject a PSU fault and /api/status showed it, but nothing
    watched it, so no alert -- and no desktop notification -- could
    ever fire for a failing power supply. On a real 5N these sensors do
    not report (the commands time out), so STATE_UNKNOWN is the everyday
    value and must stay silent; only a positive failed/warning state
    alerts. The fan rule follows the same reasoning.
    """
    if power.state == STATE_FAILED:
        power_advice = (power.note
                        or "Check the PSU and its connection.")
        seen["power-failed"] = (
            SEVERITY_CRITICAL,
            f"Power supply has failed. {power_advice}".strip(),
        )
    elif power.state == STATE_WARNING:
        seen["power-warning"] = (
            SEVERITY_WARNING,
            f"Power supply reports a problem. {power.note}".strip(),
        )


def _fan_rule(seen: dict[str, tuple[str, str]], fan) -> None:
    """A failed or troubled fan; silent on STATE_UNKNOWN, as for PSU."""
    if fan.state == STATE_FAILED:
        seen["fan-failed"] = (
            SEVERITY_CRITICAL,
            "A cooling fan has failed. The Drobo will overheat "
            "without it -- "
            "check airflow and the fan.",
        )
    elif fan.state == STATE_WARNING:
        seen["fan-warning"] = (
            SEVERITY_WARNING,
            "A cooling fan reports a problem.",
        )


def _capacity_rule(
    seen: dict[str, tuple[str, str]], capacity, pack_health,
    alert_config: dict,
) -> None:
    """
    The array filling up.

    Prefers the device's OWN capacity alert levels (mRedThreshold /
    mYellowThreshold) when the greeting reports them -- we'd then be
    alerting on exactly the same levels the original Dashboard does.
    Falls back to our config defaults when absent (e.g. the mock
    driver, which doesn't set pack_health thresholds).
    """
    used_fraction = capacity.used_fraction
    free_space_text = _format_bytes(capacity.free_bytes)
    critical_fraction = pack_health.red_threshold_fraction
    if critical_fraction is None:
        critical_fraction = alert_config["capacity_critical_fraction"]
    warning_fraction = pack_health.yellow_threshold_fraction
    if warning_fraction is None:
        warning_fraction = alert_config["capacity_warning_fraction"]
    if used_fraction >= critical_fraction:
        seen["capacity-critical"] = (
            SEVERITY_CRITICAL,
            f"Only {free_space_text} free ({used_fraction:.0%} "
            f"used). The array is nearly full.",
        )
    elif used_fraction >= warning_fraction:
        seen["capacity-warning"] = (
            SEVERITY_WARNING,
            f"{used_fraction:.0%} used, {free_space_text} free.",
        )


def _pack_health_rules(
    seen: dict[str, tuple[str, str]], pack_health,
) -> None:
    """
    Pack-level health the status greeting already sends: degraded and
    relayout counts, and any status code that has moved off the
    healthy baseline (see _PACK_STATUS_BASELINE for why a change is
    only ever "worth a look").
    """
    if pack_health.double_degraded_count > 0:
        seen["double-degraded"] = (
            SEVERITY_CRITICAL,
            f"Double-degraded count is "
            f"{pack_health.double_degraded_count} -- two "
            "drives were degraded at once; the array's protection may "
            "have been exhausted.",
        )
    if pack_health.relayout_count > 0:
        seen["relayout"] = (
            SEVERITY_INFO,
            f"Relayout count is {pack_health.relayout_count} -- "
            "rebuilding, this is normal after a drive change.",
        )
    for pack_status_field, baseline in _PACK_STATUS_BASELINE.items():
        current_value = getattr(pack_health, pack_status_field)
        if current_value is None or current_value == baseline:
            continue
        seen[f"pack-status-{pack_status_field}"] = (
            SEVERITY_WARNING,
            f"{pack_status_field} changed from the healthy "
            f"baseline ({baseline}) "
            f"to {current_value}. We don't know what this code means -- "
            "just that it's different from before.",
        )


def _warmest_temperature_c(snapshot: Snapshot) -> float | None:
    """
    The warmest temperature one reading holds, drives and chassis
    together, or None when it holds none.

    The chassis counts because a Drobo 5N reports no drive
    temperatures at all (every bay reads 0, which the driver turns into
    "no reading"). Until 2026-11-02 the history graph used the drives
    alone, so on real hardware it stayed empty while the chassis
    reading, the one real temperature, went unplotted.
    """
    temperatures = [drive.temperature_c for drive in snapshot.drives
                    if drive.temperature_c is not None]
    if snapshot.device.temperature_c is not None:
        temperatures.append(snapshot.device.temperature_c)
    return max(temperatures) if temperatures else None


# -- why no Drobo could be read ----------------------------------------
#
# Two places make the first connection and have to explain a failure to
# a person: Monitor.start, and run_agent.py --once. Both use these, so
# the reasons and the advice cannot drift apart between them.

#: What a driver's connect() raises when it read no Drobo: something
#: answered that is provably not this Drobo, discovery found nothing,
#: something answered that is not a Drobo, or nothing answered at a
#: fixed address. Monitor.start refuses to start on the first and
#: starts anyway on the rest; --once reports each one and exits 1.
#: Anything else connect() raises is a bug, left to surface as one.
CANNOT_READ_DROBO_ERRORS = (discovery.IdentityMismatchError,
                            drivers.DroboNotFoundError, esatm.FrameError,
                            OSError)


def why_drobo_cannot_be_read(driver: drivers.DroboDriver,
                             exc: Exception) -> str:
    """
    Why `driver` read no Drobo, for any of CANNOT_READ_DROBO_ERRORS:
    what happened, and what to do where there is something to do.

    Monitor.start adds what the agent does next; run_agent.py --once
    adds nothing, because it does not stay up. Until 2026-11-06 the
    words lived only in Monitor.start, and --once, which never calls
    it, ended each of these in a Python traceback instead.
    """
    if isinstance(exc, discovery.IdentityMismatchError):
        # discovery's message already names both serials. Whether the
        # Drobo was replaced or something else is answering as it is
        # the user's call, so the advice says how to accept a
        # replacement rather than guessing which it is.
        return (f"{exc}\n"
                "If your Drobo really was replaced, change expected_esa_id "
                'in the "drobo" block of config.json to the serial it '
                "reports (or remove it to pair with the next Drobo found), "
                "then start the agent again.")
    if isinstance(exc, drivers.DroboNotFoundError):
        # The driver's message says what was tried and what to check.
        return f"no Drobo found yet. {exc}"
    if isinstance(exc, esatm.FrameError):
        where = driver.target_host() or "the configured address"
        return (f"the reply from {where} was not a Drobo status message "
                f"({exc}).")
    return f"cannot reach the Drobo yet ({exc})."


class Monitor:
    """
    The agent's one watcher of the Drobo, shared by every route.

    A background thread polls the driver on a timer; each reading is
    kept as `latest`, appended to a bounded `history`, run through the
    alert rules, and pushed to every live-event subscriber. The API
    reads all of it through the methods at the bottom, under the same
    lock the poll thread writes under, so a reader never sees half of
    one poll and half of the next.
    """

    #: How long the poll thread may take to finish its current reading
    #: when the agent stops. A poll against an unreachable Drobo can
    #: sit in a network timeout; shutdown should not wait for all of it.
    POLL_THREAD_JOIN_TIMEOUT_SECONDS = 5

    #: Live events queued per subscriber before new ones are dropped.
    #: A slow or stalled browser tab must not make the poll thread wait
    #: or grow memory without bound; it can re-read /api/status.
    SUBSCRIBER_QUEUE_MAX_EVENTS = 64

    #: How long a network diagnosis stays good for. Reading this
    #: machine's own addresses means running `ipconfig`, and the poll
    #: loop can tick every couple of seconds -- spawning a process that
    #: often to re-learn something that changes when you join a
    #: different Wi-Fi network would be silly. Short enough that
    #: reconnecting clears the warning within half a minute.
    NETWORK_HINT_TTL_SECONDS = 30.0

    def __init__(self, config: dict, notifier):
        self.config = config
        self.notifier = notifier
        self.driver = drivers.build(config["drobo"])

        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._poll_thread: threading.Thread | None = None
        # Who closes the driver at shutdown: stop(), or the poll thread
        # on its way out when a read outlives stop()'s bounded wait.
        # Both flags are read and written under _close_handoff_lock so
        # the driver is closed exactly once. See stop().
        self._close_handoff_lock = threading.Lock()
        self._poll_loop_exited = False
        self._close_when_poll_loop_exits = False

        self.latest: Snapshot | None = None
        self.history: deque[Snapshot] = deque(
            maxlen=int(config["agent"]["history_length"]))
        self.alerts: dict[str, Alert] = {}
        # alert key -> when it was last sent (epoch seconds), for the
        # resend interval
        self._last_sent_at_by_key: dict[str, float] = {}
        self._subscribers: list[queue.Queue] = []
        self.started_at = time.time()

        # Why the Drobo can't be reached, when the answer is "this PC
        # isn't on its network". None whenever the Drobo is reachable,
        # or whenever we can't say. See _network_hint.
        self.network_hint: dict | None = None
        # (target host, checked at in epoch seconds, hint) -- see
        # _network_hint
        self._network_hint_cache: tuple[str, float, dict | None] | None = None

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """
        Connect to the Drobo and start the poll thread.

        Only a Drobo that is provably the wrong one stops the agent
        from starting. Every other connection failure is reported and
        then left to the poll loop, because "I can't reach your Drobo"
        is something the agent must stay up to say.
        """
        poll_seconds = self.config["agent"]["poll_seconds"]
        self._connect_or_explain(poll_seconds)
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="drobo-monitor", daemon=True)
        self._poll_thread.start()
        print(f"[monitor] polling every {poll_seconds}s via the "
              f"{self.driver.name!r} driver",
              flush=True)

    def _connect_or_explain(self, poll_seconds) -> None:
        """
        The first connection attempt. Raises SystemExit only for an
        identity mismatch; any other failure prints why the agent is
        starting anyway, and returns. Each reason is
        why_drobo_cannot_be_read's, which run_agent.py --once prints
        too.
        """
        try:
            self.driver.connect()
        except discovery.IdentityMismatchError as exc:
            # Something answered and it is provably not this Drobo: a
            # different serial at the configured address, or discovery
            # finding only devices that are not the one config.json
            # names. The one startup failure that stays fatal. Either
            # the Drobo was replaced or something else is answering as
            # it, and which of those it is is the user's call, not
            # something a retry loop should paper over. It used to end
            # in a raw traceback; discovery's message already names both
            # serials, so exit with that instead.
            raise SystemExit(
                f"\n[monitor] not starting: "
                f"{why_drobo_cannot_be_read(self.driver, exc)}\n") from exc
        except drivers.DroboNotFoundError as exc:
            # Discovery ran and nothing answered. Until 2026-09-26 this
            # exited the agent, under a comment calling it "a
            # programming/config error, not a network condition". That
            # was true when it was written (2026-07-27): the default
            # driver was the simulator, and the only way here was to
            # have asked for the real driver by hand. On 2026-08-10 the
            # real driver with automatic discovery became the default,
            # and from then on this line mostly meant a network
            # condition -- the Drobo off or still booting, or this PC's
            # own network not up yet, which is the normal state of a PC
            # starting the agent at boot. The Task Scheduler job in
            # docs/running-the-agent.md, with no window and no restart,
            # then ran no monitoring at all on a PC that booted before
            # its Drobo.
            #
            # It is the OSError case below in different clothes and gets
            # the same answer: start, serve, and let the poll loop take
            # over. Drobo5NDriver.read_snapshot runs discovery again on
            # every poll until something answers, and poll_once turns
            # each miss into the critical "unreachable" alert.
            print(f"[monitor] {why_drobo_cannot_be_read(self.driver, exc)}",
                  flush=True)
            print(f"[monitor] starting anyway: the dashboard stays up, the "
                  f"'cannot reach' alert is raised, and discovery runs again "
                  f"every {poll_seconds}s.", flush=True)
        except esatm.FrameError as exc:
            # Only reachable with a fixed address -- discovery absorbs
            # this per candidate. Something at that address replied with
            # bytes that are not a Drobo status message, or its reply
            # broke off or ran out of time part-way. The poll path
            # already treats this as "not reachable, go and look"
            # (read_snapshot re-runs discovery on it), and some of it is
            # transient: a Drobo still booting can accept a connection
            # and drop it. So it gets the OSError answer below, with its
            # own wording, instead of the traceback it used to end in.
            print(f"[monitor] {why_drobo_cannot_be_read(self.driver, exc)} "
                  f"Starting anyway and will keep trying every "
                  f"{poll_seconds}s.", flush=True)
        except OSError as exc:
            # The Drobo is not answering right now. That is NOT a reason
            # to refuse to start.
            #
            # This used to crash the agent with a raw traceback, which
            # meant the dashboard would not open at all whenever the
            # Drobo was asleep, rebooting, or the PC had joined a
            # different Wi-Fi network. A monitoring tool that only runs
            # while the thing it monitors is healthy has it exactly
            # backwards: "I can't reach your Drobo" is the single most
            # important thing it might have to tell you, and it cannot
            # say it from a traceback.
            #
            # So: start anyway. The poll loop retries on its own
            # schedule, the snapshot reports unreachable, and the
            # unreachable alert fires through the normal path. If the
            # Drobo comes back, the next poll picks it up with no
            # restart.
            print(f"[monitor] {why_drobo_cannot_be_read(self.driver, exc)} "
                  f"Starting anyway and will keep trying every "
                  f"{poll_seconds}s.", flush=True)

    def stop(self) -> None:
        """
        Ask the poll thread to finish, give it a bounded time to do so,
        then close the driver's connection to the Drobo.

        When a read outlives that wait, the driver is closed by the
        poll thread itself once the read is over, not here. Until
        2026-11-02 stop() closed it regardless, under a read still in
        flight; every driver's close() does nothing today, so that was
        latent, but a driver holding a connection would have had it
        shut mid-read.

        The hand-off needs the process to outlive the read: the poll
        thread is a daemon, so if the agent exits first the driver is
        not closed at all. That is harmless while close() does nothing,
        and a driver that one day holds a connection open has that case
        to handle.
        """
        self._stop_requested.set()
        if self._poll_thread:
            self._poll_thread.join(
                timeout=self.POLL_THREAD_JOIN_TIMEOUT_SECONDS)
        with self._close_handoff_lock:
            if self._poll_thread and not self._poll_loop_exited:
                self._close_when_poll_loop_exits = True
                return
        self.driver.close()

    def _poll_loop(self) -> None:
        """
        Poll, then wait. The wait is on the stop event rather than a
        sleep, so stop() ends the loop at once instead of after up to
        a whole poll interval. On its way out it closes the driver if
        stop() has handed that over (see stop()).
        """
        poll_seconds = float(self.config["agent"]["poll_seconds"])
        try:
            while not self._stop_requested.is_set():
                self.poll_once()
                self._stop_requested.wait(poll_seconds)
        finally:
            with self._close_handoff_lock:
                self._poll_loop_exited = True
                close_driver = self._close_when_poll_loop_exits
            if close_driver:
                self.driver.close()

    # -- polling -------------------------------------------------------

    def poll_once(self) -> Snapshot:
        """
        Take one reading, record it, and send what it changed.

        Never raises for a driver failure: a reading that could not be
        taken becomes an unreachable snapshot, which the alert rules
        turn into the "cannot reach" alert. The notifications and live
        events go out after the lock is released, so a slow notifier
        never holds up an API reader.
        """
        try:
            snapshot = self.driver.read_snapshot()
        # Any driver failure means "unreachable".
        except Exception as exc:  # noqa: BLE001
            snapshot = Snapshot(
                reachable=False, source=self.driver.name, error=str(exc))

        # Worked out BEFORE the lock is taken: this can shell out to
        # `ipconfig`, and no /api/status reader should ever be made to
        # wait behind a subprocess. Only asked for when the Drobo didn't
        # answer -- when it did, the network layout is not the story and
        # nothing needs explaining.
        network_hint = (self._network_hint()
                        if not snapshot.reachable else None)

        with self._lock:
            self.latest = snapshot
            self.network_hint = network_hint
            self.history.append(snapshot)
            fired_alerts = self._evaluate_alerts(snapshot, network_hint)
            # Built here, from the same helper /api/status uses, rather
            # than from snapshot.to_dict() directly. The web dashboard
            # is driven by this stream and not by polling /api/status,
            # so a field added to one and not the other reaches half the
            # front-ends and no test -- which is exactly how the network
            # banner came to be missing from the very UI it was written
            # for while the REST endpoint served it correctly.
            payload = self._snapshot_payload()

        self._broadcast({"type": "snapshot", "data": payload})
        for alert in fired_alerts:
            self._dispatch(alert)
        return snapshot

    # -- why we can't reach it -----------------------------------------

    def _network_hint(self) -> dict | None:
        """
        The most common reason a Drobo "disappears", checked before we
        let the dashboard imply the hardware is at fault.

        A common cause of "Cannot reach the Drobo: timed out" is nothing
        more than Windows having auto-joined a different Wi-Fi network
        -- a phone hotspot, a guest SSID -- while the array is healthy
        throughout. That message sends someone to check on their NAS
        when the fix is on the machine reading this sentence, so when
        it's true the message should say so.

        Returns None whenever we can't say honestly: no known address
        for the Drobo, no readable addresses for this PC, or a Drobo
        that IS on our network and simply isn't answering (a real
        outage, which this must not talk over). See drobo_nasd.netcheck
        for what the check can and cannot prove -- it is an explanation
        for a failure that already happened, never a prediction, and
        never a reason not to keep trying.

        Cached per target for NETWORK_HINT_TTL_SECONDS so a fast poll
        loop doesn't run `ipconfig` every tick.
        """
        target = self.driver.target_host()
        if not target:
            return None

        now = time.time()
        if self._network_hint_cache:
            cached_target, checked_at, cached_hint = self._network_hint_cache
            if (cached_target == target
                    and now - checked_at < self.NETWORK_HINT_TTL_SECONDS):
                return cached_hint

        try:
            diagnosis = netcheck.diagnose(target)
        except Exception:  # noqa: BLE001 - a diagnosis must never break a poll
            # Nothing in netcheck is supposed to raise, but this runs on
            # every failed poll on somebody's storage monitor. A bug in
            # the explanation must not cost them the alert it was
            # explaining.
            diagnosis = None

        hint = (diagnosis.to_dict()
                if diagnosis and not diagnosis.same_network else None)
        self._network_hint_cache = (target, now, hint)
        return hint

    # -- alert rules ---------------------------------------------------

    def _evaluate_alerts(
        self, snapshot: Snapshot, network_hint: dict | None = None
    ) -> list[Alert]:
        """Work out which conditions hold right now.
        Returns newly-fired alerts.

        Every rule writes into `seen` (alert key -> (severity, message))
        rather than raising alerts itself. The keys are stable ids such
        as "drive-failed-3", so the same problem seen on the next poll
        updates the alert it already raised instead of raising another,
        and a key that stops appearing means the problem has cleared.
        That is what makes alerts sticky: the apps show a list of
        current problems, not a stream of one-off messages. The rules
        run in a fixed order, and that order is the order notifications
        go out in.
        """
        alert_config = self.config["alerts"]
        seen: dict[str, tuple[str, str]] = {}  # key -> (severity, message)

        if not snapshot.reachable:
            _unreachable_rule(seen, snapshot, network_hint)
            return self._reconcile_alerts(
                seen, alert_config, device_was_read=False)

        _drive_rules(seen, snapshot.drives, alert_config)
        _chassis_temperature_rule(
            seen, snapshot.device.temperature_c, alert_config)
        _battery_rule(seen, snapshot.battery)
        _power_rule(seen, snapshot.power)
        _fan_rule(seen, snapshot.fan)
        _capacity_rule(
            seen, snapshot.capacity, snapshot.pack_health, alert_config)
        _pack_health_rules(seen, snapshot.pack_health)
        return self._reconcile_alerts(seen, alert_config)

    def _reconcile_alerts(
        self, seen: dict[str, tuple[str, str]], alert_config: dict,
        device_was_read: bool = True,
    ) -> list[Alert]:
        """
        Fold this poll's findings into the standing alerts. Returns
        what should be sent now: alerts that are new, long-running ones
        whose resend interval has passed, and a "-cleared" alert for
        each one that has gone away, so a user who was told about a
        problem is also told when it ends.

        device_was_read is False for a poll that could not reach the
        Drobo. Such a poll learns nothing about the drives, capacity,
        temperature or pack, so their alerts are left exactly as they
        stand. Until 2026-11-02 they were cleared like any other alert
        missing from `seen`: every network blink sent each one as
        "Resolved" and the next good poll sent it again as new.
        """
        now = time.time()
        fired_alerts: list[Alert] = []

        for key, (severity, message) in seen.items():
            existing = self.alerts.get(key)
            if not (existing and existing.active):
                alert = Alert(key=key, severity=severity, message=message)
                self.alerts[key] = alert
                fired_alerts.append(alert)
                continue
            existing.last_seen = now
            existing.message = message
            # Re-send long-running problems occasionally so they
            # aren't forgotten.
            if (now - self._last_sent_at_by_key.get(key, 0)
                    >= alert_config["resend_seconds"]):
                fired_alerts.append(existing)

        if device_was_read:
            fired_alerts.extend(self._clear_alerts_not_seen(seen, now))
        return fired_alerts

    def _clear_alerts_not_seen(
        self, seen: dict[str, tuple[str, str]], now: float
    ) -> list[Alert]:
        """
        Mark every active alert missing from `seen` as cleared, and
        return a "-cleared" alert for each, to be sent.
        """
        cleared_alerts: list[Alert] = []
        for key, alert in self.alerts.items():
            if not alert.active or key in seen:
                continue
            alert.cleared_at = now
            self._last_sent_at_by_key.pop(key, None)
            cleared_alerts.append(Alert(
                key=key + "-cleared",
                severity=SEVERITY_INFO,
                message=f"Resolved: {alert.message}",
            ))
        return cleared_alerts

    def _dispatch(self, alert: Alert) -> None:
        """
        Send one alert to the notifier and to every live-event
        subscriber, and note when it went, so the resend interval
        counts from here.
        """
        self._last_sent_at_by_key[alert.key] = time.time()
        title = {
            SEVERITY_CRITICAL: "Drobo: action needed",
            SEVERITY_WARNING: "Drobo: warning",
        }.get(alert.severity, "Drobo")
        self.notifier.send(title, alert.message, alert.severity)
        self._broadcast({"type": "alert", "data": alert.to_dict()})

    # -- live event stream (used by /api/events) -----------------------

    def subscribe(self) -> queue.Queue:
        """
        A queue that receives every snapshot and alert event from now
        on. Bounded, so a client that stops reading only misses events
        (see _broadcast); the caller must unsubscribe() when done.
        """
        subscriber_queue: queue.Queue = queue.Queue(
            maxsize=self.SUBSCRIBER_QUEUE_MAX_EVENTS)
        with self._lock:
            self._subscribers.append(subscriber_queue)
        return subscriber_queue

    def unsubscribe(self, subscriber_queue: queue.Queue) -> None:
        """Stop sending events to a queue; an unknown one is ignored."""
        with self._lock:
            if subscriber_queue in self._subscribers:
                self._subscribers.remove(subscriber_queue)

    def _broadcast(self, payload: dict) -> None:
        """
        Offer one event to every subscriber without waiting on any of
        them. The list is copied under the lock and the puts happen
        outside it, so a subscriber joining or leaving mid-send is
        safe and the poll thread never blocks on a client.
        """
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber_queue in subscribers:
            try:
                subscriber_queue.put_nowait(payload)
            except queue.Full:
                # a slow client just misses updates;
                # it can re-read /api/status
                pass

    # -- read helpers used by the API ----------------------------------

    def _snapshot_payload(self) -> dict:
        """
        The snapshot as every front-end receives it. Caller must hold
        the lock.

        The ONE place this shape is defined. Both routes out of the
        agent go through here -- /api/status below, and the SSE stream
        in poll_once -- because they are the same document to a
        front-end, and a field present on one but not the other is a bug
        that only shows up in whichever UI happens to use the other
        channel.
        """
        if self.latest is None:
            # Carries network_hint too, even though it is always None
            # here. A front-end that checks for the key rather than its
            # value -- a reasonable thing to do -- must not see a
            # differently-shaped payload just because it asked during
            # the first few seconds of the agent's life.
            return {"reachable": False, "error": "no reading taken yet",
                    "network_hint": None, "firmware_advisory": None}
        # network_hint rides along on the snapshot rather than living
        # inside it: a Snapshot describes a Drobo, and where this PC
        # happens to be plugged in is not a fact about the Drobo.
        # Non-null only when the device is unreachable AND we can
        # actually explain why -- so a front-end can treat "it's there"
        # as reason enough to render the banner. to_dict() builds a
        # fresh dict each call, so adding a key here can't leak into the
        # Snapshot.
        payload = self.latest.to_dict()
        payload["network_hint"] = self.network_hint
        # What the running firmware version means. Cheap (a regex over a
        # string we already hold) and derived rather than stored, so it
        # can never go stale against the device's own report.
        #
        # Deliberately NOT an alert. Alerts in this agent are sticky and
        # re-send on a timer, which is right for a failing drive and
        # wrong for this: the only "fix" is flashing firmware, which
        # this software refuses to do and actively advises against, so
        # an alert would nag hourly about something with no safe action
        # behind it. It is a standing note about the device, so it rides
        # on the device's status and the UIs render it once, calmly.
        payload["firmware_advisory"] = firmware.assess(
            self.latest.device.firmware).to_dict()
        return payload

    def snapshot_dict(self) -> dict:
        """The latest snapshot as /api/status serves it."""
        with self._lock:
            return self._snapshot_payload()

    def active_alerts(self) -> list[dict]:
        """The problems that hold right now, as JSON-ready dicts."""
        with self._lock:
            return [alert.to_dict() for alert in self.alerts.values()
                    if alert.active]

    def all_alerts(self) -> list[dict]:
        """
        Every alert since the agent started, cleared ones included, so
        an app can show what happened while nobody was looking.
        """
        with self._lock:
            return [alert.to_dict() for alert in self.alerts.values()]

    def history_points(self, max_points: int = 720) -> list[dict]:
        """Trimmed history for graphing --
        just the numbers, not whole snapshots.
        """
        with self._lock:
            recent_snapshots = list(self.history)[-max_points:]
        points = []
        for snapshot in recent_snapshots:
            points.append(
                {
                    "t": round(snapshot.taken_at, 1),
                    "reachable": snapshot.reachable,
                    "state": snapshot.overall_state,
                    "used_bytes": snapshot.capacity.used_bytes,
                    "free_bytes": snapshot.capacity.free_bytes,
                    "max_temp_c": _warmest_temperature_c(snapshot),
                    "read_mb_per_s": snapshot.performance.read_mb_per_s,
                    "write_mb_per_s": snapshot.performance.write_mb_per_s,
                }
            )
        return points
