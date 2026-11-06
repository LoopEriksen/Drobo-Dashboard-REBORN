"""
Drobo drivers.

This is the ONLY part of the whole project that knows anything about how
a Drobo talks. Everything above it -- the monitor, the API, the Windows
app, the iOS app -- deals in the plain shapes from models.py.

Two drivers exist today:

  MockDriver     A simulated Drobo 5N. Lets us build and test the
                 entire stack before the protocol is understood, and
                 lets us trigger fake drive failures on demand to
                 prove the alert path works.

  Drobo5NDriver  The real one, and it works against real hardware,
                 not a stub: reads the live status greeting
                 (esatm.py), discovers the device by mDNS when its
                 address changes (discovery.py), and tops up chassis
                 temperature/uptime from the command channel
                 (sysinfo.py), all confirmed against a live Drobo 5N.
                 See docs/protocol-map.md for what's been measured.
"""

from __future__ import annotations

import copy
import math
import random
import threading
import time
from collections import deque

from .models import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATE_EMPTY,
    STATE_FAILED,
    STATE_OK,
    STATE_WARNING,
    BatteryInfo,
    Capacity,
    DeviceInfo,
    DriveSlot,
    EventLogEntry,
    FanInfo,
    PerformanceInfo,
    PowerInfo,
    Snapshot,
    Volume,
    gigabytes_to_bytes,
)
from drobo_nasd import (
    config_cmd, discovery, esatm, eventlog, sharewrite, sysinfo)


class DroboNotFoundError(LookupError):
    """
    Automatic discovery ran and nothing answered as a Drobo: not a typed
    address, not the last-known one, not mDNS, and not the subnet sweep
    if it is switched on.

    A network condition, not a configuration error. The Drobo may be
    off, asleep or still booting, or this PC's own network may not be up
    yet -- which is the ordinary state of a PC that starts the agent at
    boot. The monitor starts anyway on it and every poll runs discovery
    again (see Monitor.start). Its own type rather than a bare
    LookupError so that catching it can never also swallow a KeyError or
    IndexError, which are LookupErrors too and would be bugs.

    Until 2026-09-26 this was reported as ProtocolNotMappedError, a
    leftover from before the protocol was mapped, and the monitor exited
    on it as a configuration error. Once the real driver with discovery
    became the default (2026-08-10), that meant an agent started before
    its Drobo never monitored anything at all.
    """


class SimulatedWriteUnconfirmedError(Exception):
    """
    The simulated device took a configuration write and then made
    settling its outcome impossible -- the injected mirror of
    config_cmd's ConfigWriteOutcomeUnknownError and sharewrite's
    ShareWriteOutcomeUnknownError.

    Deliberately the simulator's OWN type rather than one of those: the
    mock stands in for a device, not for a particular SDK release, and
    it should not start reporting write endings differently because the
    SDK reorganised its exceptions. api._UNCONFIRMED_WRITE_ERRORS maps
    all three to one answer.
    """


# -- how a simulated configuration write ENDS --------------------------
# Fault injection for the write path, named after what the device DOES
# rather than after the HTTP code it produces. WRITE_FAULT_HANGS_UP is
# not invented: on 2026-08-25 a real 5N was sent a share create, closed
# the socket without ever answering, and had created the share anyway --
# so every layer above reported a completed write as a failure. These
# switches keep that case, and its unsettleable cousin, testable forever
# without hardware.

#: The device answers and the change is there. The default.
WRITE_FAULT_NONE = "none"

#: The device APPLIES the change and then hangs up without answering.
#: The read-back still finds the change, which is the only reason
#: anything knows it worked.
WRITE_FAULT_HANGS_UP = "hangs_up"

#: The device refuses the write. Nothing is applied -- the only fault
#: here that leaves the simulated device untouched.
WRITE_FAULT_REFUSED = "refused"

#: The device applies the change, hangs up, AND the read-back that would
#: settle it cannot be completed either. The worst case: the change is
#: really there and nothing above can prove it.
WRITE_FAULT_UNCONFIRMABLE = "unconfirmable"

WRITE_FAULTS = (WRITE_FAULT_NONE, WRITE_FAULT_HANGS_UP, WRITE_FAULT_REFUSED,
                WRITE_FAULT_UNCONFIRMABLE)


#: The longest socket timeout the real driver gives its command-port
#: top-ups (sysinfo, event log); a shorter configured timeout is used as
#: it is. The SDK applies it to each connect and each read, so it bounds
#: every wait on a slow command port rather than the whole request;
#: those requests only top up a poll that has already read the status
#: greeting.
_COMMAND_TIMEOUT_CAP_SECONDS = 6.0


class DroboDriver:
    """Base class.
    A driver's whole job is to produce a Snapshot on demand.

    It is the seam that lets the simulator stand in for a real Drobo:
    the monitor only ever calls these methods and only ever sees the
    plain shapes from models.py, so swapping MockDriver for
    Drobo5NDriver changes where its numbers come from and nothing else.
    The API reaches further: with simulate_config it calls the
    simulator's own command-port stand-ins, and it reads host,
    _resolved_host, _resolved_port and _known_esa_id by getattr, so
    those attribute names are load-bearing.
    """

    name = "base"

    def __init__(self, drobo_config: dict):
        """Take the drobo section of the agent's config.

        The base class keeps nothing from it; a driver that needs a
        setting reads it in its own __init__.
        """

    def connect(self) -> None:
        """
        Called once at startup. Raise if the device can't be reached.

        Monitor.start sorts what is raised. OSError, esatm.FrameError
        and DroboNotFoundError mean "not reachable yet": the agent
        starts anyway and the poll loop keeps trying.
        discovery.IdentityMismatchError means something answered and it
        is provably not this Drobo: the agent refuses to start. Anything
        else is a bug and is left to surface as one. run_agent.py --once
        sorts the same four (monitor.CANNOT_READ_DROBO_ERRORS) and says
        why it took no reading.
        """

    def read_snapshot(self) -> Snapshot:
        """
        One complete reading of the device, called on every poll.

        Every driver answers with a whole Snapshot rather than pieces,
        so the monitor can compare one reading with the last and raise
        alerts without knowing which driver produced either.
        """
        raise NotImplementedError

    def target_host(self) -> str:
        """
        The address this driver is trying to reach, or "" if it hasn't
        got one.

        Exists so the monitor can explain a failure without reaching
        into a driver's private attributes. "" is a real answer meaning
        "no opinion": a simulated Drobo has no address, and a driver set
        to auto-discovery that has never once succeeded genuinely does
        not know where its device lives -- so nothing downstream may
        claim the PC is on the wrong network for it. See
        monitor._network_hint.
        """
        return ""

    def close(self) -> None:
        """Called at shutdown."""


# ---------------------------------------------------------------------------
# Mock driver
# ---------------------------------------------------------------------------


class MockDriver(DroboDriver):
    """
    A believable fake Drobo 5N: five bays, four populated, capacity
    that creeps up over time, and temperatures that drift the way real
    drives do.

    Fault injection lets us test alerts -- and awkward write endings --
    without touching real hardware:
        driver.fail_bay(3)      # simulate a drive failure
        driver.pull_bay(3)      # simulate someone yanking a drive
        driver.fail_write(WRITE_FAULT_HANGS_UP)
                                # applied, never acknowledged
        driver.reset()          # back to healthy
    """

    name = "mock"

    _DRIVE_MODELS = ["WDC WD40EFRX-68N32N0", "ST4000VN008-2DR166",
                     "TOSHIBA HDWQ140"]

    #: How many simulated events are kept; the oldest drop off first,
    #: so a long-running demo cannot grow the log without bound.
    _EVENT_LOG_MAX_ENTRIES = 200

    #: How much the simulated pack fills up on every poll, so the
    #: capacity graph has something to draw.
    _USAGE_GROWTH_GB_PER_POLL = 0.002

    #: Each simulated volume's maximum size: 64 TiB, what the real 5N
    #: reports for a volume.
    _VOLUME_MAX_SIZE_BYTES = 70368744177664

    #: At or below this speed the simulated fan reads as a warning.
    _FAN_SLOW_RPM = 800

    # How fast the simulated readings drift: each one follows
    # sin(elapsed_seconds / X) for its X below, so one full cycle takes
    # 2 * pi * X seconds. Different for each reading so they do not all
    # rise and fall together, which no real device does.
    _DRIVE_TEMPERATURE_SECONDS_PER_RADIAN = 240.0
    _WEAK_BATTERY_SECONDS_PER_RADIAN = 300.0
    _BATTERY_CHARGE_SECONDS_PER_RADIAN = 600.0
    _FAN_SPEED_SECONDS_PER_RADIAN = 180.0
    _READ_THROUGHPUT_SECONDS_PER_RADIAN = 90.0
    _WRITE_THROUGHPUT_SECONDS_PER_RADIAN = 130.0

    def __init__(self, drobo_config: dict):
        """
        Build a healthy simulated 5N.

        The drive layout comes from a fixed seed, so the same bays,
        models and serials appear on every restart and a demo or a test
        can point at "bay 3" and mean the same drive each time.
        Everything a write can change starts as a per-instance copy of
        the class-level defaults (see _init_simulated_command_port).
        """
        super().__init__(drobo_config)
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._bay_state_overrides: dict[int, str] = {}
        self._bay_specs: list[dict] = self._build_bay_specs()
        self._used_bytes = gigabytes_to_bytes(6800)

        # -- Phase 1 scope additions (ROADMAP.md) -- believable
        # baselines that drift a little, same idea as the drive
        # temperatures above.
        self._battery_override: str | None = None
        self._battery_charge_baseline_pct = 97.0
        self._fan_rpm_baseline = 2350
        self._power_override: str | None = None
        self._event_log: deque[EventLogEntry] = deque(
            maxlen=self._EVENT_LOG_MAX_ENTRIES)
        self._record_event(SEVERITY_INFO, "Drobo started up (mock driver)")

        # Kept only so a config carrying manual_discovery_ips loads
        # cleanly against either driver. The simulator has no network to
        # search, so there is nothing here for an address to point AT --
        # the setting is read and acted on by Drobo5NDriver, which is
        # where discovery lives.
        self.manual_discovery_ips: list[str] = list(
            drobo_config.get("manual_discovery_ips", []))

        self._init_simulated_command_port(drobo_config)

    @classmethod
    def _build_bay_specs(cls) -> list[dict]:
        """
        The five simulated bays: four 4 TB drives and one empty bay.

        The seed is fixed so restarts look consistent, and the draws
        happen in one order -- model, serial, then baseline temperature,
        bay by bay -- so the same seed always gives the same drives.
        """
        seeded_random = random.Random(5150)
        bay_specs: list[dict] = []
        for bay in range(1, 6):
            populated = bay <= 4
            bay_specs.append(
                {
                    "bay": bay,
                    "present": populated,
                    "model": (seeded_random.choice(cls._DRIVE_MODELS)
                              if populated else ""),
                    "serial": (
                        f"MOCK{seeded_random.randrange(10**7, 10**8)}"
                        if populated else ""),
                    "capacity_bytes": (gigabytes_to_bytes(4000)
                                       if populated else 0),
                    "baseline_temperature_c": (
                        34.0 + seeded_random.uniform(-2.0, 4.0)),
                }
            )
        return bay_specs

    def _init_simulated_command_port(self, drobo_config: dict) -> None:
        """
        The state behind what a real 5N serves on its command port:
        configuration, Identify, and how a configuration write ends.

        Kept apart from the status-greeting state above because it is
        a separate thing to switch on (simulate_config) and the part
        that simulated writes change.
        """
        # Whether this mock also stands in for the COMMAND port (shares,
        # network and admin config), not just the status greeting.
        #
        # Off by default, and that default matters: test_agent.py drives
        # a MockDriver as a stand-in for real hardware while patching
        # config_cmd.get_config to fake device reads. If the mock
        # answered config queries by itself, those tests would silently
        # stop exercising the path they exist to cover. So it is opt-in,
        # set in config.mock.json, and only the demo simulator turns it
        # on.
        self.simulate_config = bool(drobo_config.get("simulate_config", False))

        # Per-instance, writable copies of the configuration this
        # simulator serves. The class-level _MOCK_* tables are the
        # pristine defaults; the simulated write methods below mutate
        # ONLY these copies, so a write in one test or one agent run
        # never leaks into another.
        self._shares = copy.deepcopy(self._MOCK_SHARES)
        self._droboapps = copy.deepcopy(self._MOCK_DROBOAPPS)
        self._dimming: int = self._MOCK_DIMMING
        self._spindown_delay: int = 14  # the real unit's default, enabled
        self._feature_states: int = 7   # bits 0,2 set + spin-down bit 1 set

        # How many times Identify has been asked for. Exists so a test
        # can prove the command actually reached the driver rather than
        # being swallowed somewhere in the API layer.
        self._identify_count = 0
        self._identify_last_interval: int | None = None

        # How the next simulated configuration write ends -- see the
        # WRITE_FAULT_* constants and fail_write() below. Healthy by
        # default; reset() puts it back.
        self._write_fault = WRITE_FAULT_NONE

    # -- simulated configuration ---------------------------------------

    #: A believable share layout, in the EXACT shape
    #: config_cmd.get_config returns for a real device. Deliberately so:
    #: /api/shares then runs the real shares.summarize() and
    #: link_health() over it, which means demo mode exercises the
    #: production parsing path rather than a parallel pretend one. If
    #: that parser breaks, the simulator notices.
    #:
    #: Chosen to be worth testing against rather than tidy:
    #:   - four shares, not one, so the list has to lay out properly
    #:   - a mix of access codes, including one nobody can reach
    #:   - a Time Machine target, which renders differently
    #:   - a share whose name has a space in it, because UNC paths and
    #:     `net use` both care and it is the classic thing to get wrong
    _MOCK_SHARES = {
        "DRINASConfig": {
            "DRIShareConfig": {
                "ConfigVersionBasis": "14",
                # A real 5N always sends this element, empty when the
                # device has no named accounts -- which is the case on
                # the only unit this project can read. It was missing
                # here once, so the simulator produced "accounts
                # unknown" where hardware produces "definitely none". A
                # simulator that omits a field the device always sends
                # hides exactly the behaviour that field controls.
                #
                # Since 2026-09-14 the simulated Drobo HAS one account,
                # admin, at the maintainer's request: with none,
                # "Connect as..." was greyed out and the whole sign-in
                # flow could not be tried without hardware. That is a
                # deliberate divergence from the maintainer's unit, and
                # it moves the "no accounts" branch of access_advice out
                # of demo mode. It stays covered where it belongs, in
                # sdk/tests/test_shares.py. The shape is the one the SDK
                # parses (User/UserName) and the one that test's
                # fixtures use; a populated UserList has still never
                # been seen on hardware.
                "UserList": {"User": {"UserName": "admin"}},
                "Shares": {
                    "Share": [
                        # admin has read/write on every share. On
                        # Private that is the ONLY way in, since
                        # Everyone has none -- which is what makes it
                        # worth signing in to.
                        {"ShareName": "Documents", "ShareState": "0",
                         "TimeMachineEnabled": "0",
                         "ShareUsers": {"ShareUser": [
                             {"ShareUsername": "Everyone",
                              "ShareUserAccess": "1"},
                             {"ShareUsername": "admin",
                              "ShareUserAccess": "1"}]}},
                        {"ShareName": "Photo Library", "ShareState": "0",
                         "TimeMachineEnabled": "0",
                         "ShareUsers": {"ShareUser": [
                             {"ShareUsername": "Everyone",
                              "ShareUserAccess": "1"},
                             {"ShareUsername": "guest",
                              "ShareUserAccess": "0"},
                             {"ShareUsername": "admin",
                              "ShareUserAccess": "1"}]}},
                        {"ShareName": "TimeMachine", "ShareState": "0",
                         "TimeMachineEnabled": "1",
                         "ShareMaxTMSizeGB": "1000.000000",
                         "ShareUsers": {"ShareUser": [
                             {"ShareUsername": "Everyone",
                              "ShareUserAccess": "1"},
                             {"ShareUsername": "admin",
                              "ShareUserAccess": "1"}]}},
                        {"ShareName": "Private", "ShareState": "0",
                         "TimeMachineEnabled": "0",
                         "ShareUsers": {"ShareUser": [
                             {"ShareUsername": "Everyone",
                              "ShareUserAccess": "0"},
                             {"ShareUsername": "admin",
                              "ShareUserAccess": "1"}]}},
                    ]
                },
            }
        }
    }

    #: Network config, again in the real shape. PortSpeed is 100 on
    #: purpose -- a gigabit-capable box negotiating 100 Mbit is a real
    #: fault this project detects and warns about, and demo mode should
    #: show that warning rather than a perfect machine that exercises
    #: none of the interesting code.
    _MOCK_NETWORK = {
        "DRINASConfig": {
            "DRINasNetworkConfig": {
                "Version": "11",
                "NasName": "MockDrobo",
                "NasWorkgroup": "WORKGROUP",
                "IPConfig": {"IPConfigType": "0", "IP": "10.0.0.5",
                             "Subnet": "255.255.255.0", "Gateway": "10.0.0.1",
                             "DNS1": "10.0.0.1", "DNS2": "9.9.9.9"},
                "JumboFramesConfig": {"Enabled": "0", "MTUSize": "1500"},
                "MACAddress": "02:00:00:00:00:01",
                "PortSpeed": "100",
                "PortDuplex": "full",
            }
        }
    }

    #: Admin config. The password fields are present because a real
    #: device sends them -- they exist here so the redaction path is
    #: exercised in demo mode too, and so a leak would show up while
    #: simulating rather than only against real hardware.
    _MOCK_ADMIN = {
        "DRINASConfig": {
            "DRINasAdminConfig": {
                "UserName": "admin",
                "Password": "****************",
                "ValidPassword": "1",
                "EncryptedPassword": "*",
            }
        }
    }

    #: What the simulator claims is installed. Shaped like the real
    #: reply, but the apps are invented -- names, versions, descriptions
    #: and states are examples, not any real unit's list. One app is
    #: stopped on purpose so the UI has both states to render, and
    #: the web-interface flag is set on some and not others for the
    #: same reason.
    _MOCK_DROBOAPPS = {
        "sdk_version": "2.1",
        "apps": [
            {"name": "AlphaApp", "version": "1.0.0.1",
             "description": "Example web app.",
             "running": False, "status": "", "has_web_ui": True},
            {"name": "betaapp", "version": "2.0.1",
             "description": "Example service.",
             "running": True, "status": "Example service is running.",
             "has_web_ui": False},
            {"name": "GammaApp", "version": "3.2.0.7",
             "description": "Example app with its own web page.",
             "running": True, "status": "", "has_web_ui": True},
            {"name": "deltaapp", "version": "0.9.4",
             "description": "Example background task.",
             "running": True, "status": "Waiting for work.",
             "has_web_ui": False},
        ],
    }

    #: LED brightness, as the device reports it. The real 5N
    #: returned 59.
    _MOCK_DIMMING = 59

    #: What DroboSync looks like on a device with no partner -- which is
    #: what the real 5N reports, and what almost everyone running this
    #: will see. The simulator does NOT invent a configured sync: nobody
    #: on this project has ever observed a populated reply, so a fake
    #: one would be teaching the UI a shape we made up. Showing the
    #: honest empty case is the useful thing.
    _MOCK_DROBOSYNC = {
        "configured": False,
        "reads": {
            "settings": {"configured": False, "raw_xml": "", "fields": {}},
            "summary_log": {"configured": False, "raw_xml": "", "fields": {}},
            "detailed_log": {"configured": False, "raw_xml": "", "fields": {}},
            "log": {"configured": False, "raw_xml": "", "fields": {}},
        },
        "note": (
            "This Drobo has no DroboSync partner set up. DroboSync copied one "
            "Drobo's shares to a second Drobo on a schedule, so it needs two "
            "units -- with one, there is nothing for it to do and the device "
            "correctly reports nothing. Everything needed to display it is "
            "built and waiting."
        ),
    }

    def get_droboapps(self) -> dict:
        """The stand-in for droboapps.get_droboapps: the simulated
        installed-apps list, in the shape the SDK returns, so the API
        serves it through the same code it uses for a real device.

        A copy, like every read here, as a real device's reply is a
        fresh parse each time: until 2026-11-02 the live table was
        handed out, and changed under its holder at the next action."""
        with self._lock:
            return copy.deepcopy(self._droboapps)

    def get_drobosync(self) -> dict:
        """The stand-in for the DroboSync reads: the honest
        no-partner reply described at _MOCK_DROBOSYNC. A copy, because
        the table is shared by every MockDriver: until 2026-11-02 a
        caller's change to it reached every later instance."""
        return copy.deepcopy(self._MOCK_DROBOSYNC)

    def get_dimming(self) -> int:
        """The stand-in for droboapps.get_dimming: the simulated LED
        brightness, which set_dimming changes."""
        return self._dimming

    # -- simulated configuration WRITES --------------------------------
    # These mutate PER-INSTANCE copies, never the class-level _MOCK_*
    # tables. A class-level mutation would leak one test's write into
    # every other test and across agent restarts -- a simulator that
    # remembers the wrong thing is worse than no simulator. Each applies
    # the SAME validation the real SDK write applies, so a value the
    # device would refuse is refused here too, and the UI path that
    # drives these cannot be certified by a simulator that is more
    # permissive than the hardware.
    #
    # Each returns what an SDK write returns -- a small result dict the
    # API layer classifies with the same code it uses on the hardware
    # answer -- and each honours any injected write fault, by calling
    # _refuse_if_injected() BEFORE it touches anything and returning
    # _write_ending() after.

    def _refuse_if_injected(self) -> None:
        """
        The one write ending that must happen BEFORE the change: a
        refusal leaves the simulated device untouched, which is the
        whole difference between it and the two unacknowledged endings.
        """
        if self._write_fault == WRITE_FAULT_REFUSED:
            raise config_cmd.ConfigWriteError(
                "the device did not accept the write. Nothing is confirmed "
                "changed. (injected mock fault)")

    def _write_ending(self) -> dict:
        """
        How a write that has ALREADY been applied gets reported.

        Called after the change on purpose: the point of both
        unacknowledged endings is that the change is genuinely on the
        (simulated) device while the answer coming back says nothing of
        the sort -- which is exactly what a real 5N did on 2026-08-25.
        `acknowledged` is the field the API layer reads to tell a plain
        success from that one.
        """
        if self._write_fault == WRITE_FAULT_UNCONFIRMABLE:
            raise SimulatedWriteUnconfirmedError(
                "the device took the write and then closed the connection, "
                "and the read-back that would confirm it could not be "
                "completed either. (injected mock fault)")
        return {"verified": True,
                "acknowledged": self._write_fault != WRITE_FAULT_HANGS_UP}

    def set_dimming(self, brightness: int) -> dict:
        """The stand-in for droboapps.set_dimming, refusing the same
        values: the SDK's own band is borrowed rather than restated, so
        the simulator can never accept a brightness the real write
        would refuse."""
        from drobo_nasd.droboapps import _DIMMING_MIN, _DIMMING_MAX
        if (isinstance(brightness, bool) or not isinstance(brightness, int)
                or not (_DIMMING_MIN <= brightness <= _DIMMING_MAX)):
            raise ValueError(
                f"brightness must be a whole number "
                f"{_DIMMING_MIN}-{_DIMMING_MAX}")
        with self._lock:
            self._refuse_if_injected()
            self._dimming = brightness
            self._record_event(
                SEVERITY_INFO,
                f"LED brightness set to {brightness} (mock driver)")
            return self._write_ending()

    def set_spindown(self, delay) -> dict:
        """None disables (bit cleared + delay 0), a positive int sets
        the delay and enables -- mirroring droboapps.set_spindown /
        disable_spindown, including the refusal of a bare 0 that the
        real device coerces.

        The delay carries no unit in its name because the device's
        unit is unconfirmed (see droboapps._SPINDOWN_MAX)."""
        from drobo_nasd.droboapps import _SPINDOWN_MAX, _SPINDOWN_ENABLED_BIT
        with self._lock:
            if delay is None:
                self._refuse_if_injected()
                self._feature_states &= ~_SPINDOWN_ENABLED_BIT
                self._spindown_delay = 0
                self._record_event(SEVERITY_INFO,
                                   "Drive spin-down disabled (mock driver)")
                return self._write_ending()
            if (isinstance(delay, bool) or not isinstance(delay, int)
                    or delay <= 0 or delay > _SPINDOWN_MAX):
                raise ValueError(
                    f"spin-down delay must be 1-{_SPINDOWN_MAX}; "
                    f"a bare 0 does not disable (the device coerces it) "
                    f"-- disable explicitly")
            self._refuse_if_injected()
            self._feature_states |= _SPINDOWN_ENABLED_BIT
            self._spindown_delay = delay
            self._record_event(
                SEVERITY_INFO,
                f"Drive spin-down delay set to {delay} (mock driver)")
            return self._write_ending()

    def get_spindown(self) -> dict:
        """Both halves of the spin-down state, because the delay alone
        cannot say whether the feature is on: the enable bit lives in
        FeatureStates beside it (measured 2026-08-12), and a delay with
        the bit clear is a value nobody can observe."""
        from drobo_nasd.droboapps import _SPINDOWN_ENABLED_BIT
        with self._lock:
            return {"enabled": bool(
                        self._feature_states & _SPINDOWN_ENABLED_BIT),
                    "delay": self._spindown_delay}

    def droboapps_action(self, name: str, action: str) -> None:
        """Start or stop a simulated app -- the stand-in for
        droboapps.droboapps_action, applying the same refusals: only the
        two case-sensitive verbs the capture confirmed, and only a name
        the (simulated) device reports as installed. The state flips
        immediately; a real app takes a moment, which is why the API
        layer reports an unflipped read-back as still settling rather
        than as a failure."""
        from drobo_nasd.droboapps import _APP_ACTIONS
        if action not in _APP_ACTIONS:
            raise ValueError(
                f"action must be one of {_APP_ACTIONS!r}, not {action!r} -- "
                f"the device's verbs are case-sensitive "
                f"and only these two are confirmed")
        app_name = (name or "").strip()
        with self._lock:
            apps = self._droboapps["apps"]
            matching_apps = [app for app in apps if app["name"] == app_name]
            if not matching_apps:
                installed_names = sorted(app["name"] for app in apps)
                raise ValueError(
                    f"{app_name!r} is not an installed DroboApp on this "
                    f"device (installed: {installed_names}); "
                    f"refusing to act on a name the device did not report")
            matching_apps[0]["running"] = action == "Start"
            self._record_event(
                SEVERITY_INFO,
                f"DroboApp {app_name!r}: {action} (mock driver)")

    # -- the simulated share config's shape ---------------------------
    # The share table is held in the dict rendering of the device's XML,
    # where an element that occurs once is a dict and one that repeats
    # is a list. These helpers do that normalisation in one place, so
    # every simulated share write reads and stores the table the same
    # way the real config_cmd.get_config reply would have it.

    @staticmethod
    def _as_list(element) -> list:
        """A repeated XML element as a list, whether it occurred once
        (a dict) or several times (already a list)."""
        return element if isinstance(element, list) else [element]

    @classmethod
    def _share_user_nodes(cls, share_node: dict) -> list:
        """A share's ShareUser rows, tolerating a missing or empty
        <ShareUsers/>, which a freshly created share holds."""
        return cls._as_list(
            (share_node.get("ShareUsers") or {}).get("ShareUser") or [])

    def _share_config_node(self) -> dict:
        """The DRIShareConfig block every simulated share write
        edits."""
        return self._shares["DRINASConfig"]["DRIShareConfig"]

    def _share_nodes(self, share_config_node: dict) -> list:
        """The share rows as a list. When the table already holds a
        list, this IS that list, so appending to it edits the table."""
        return self._as_list(share_config_node["Shares"]["Share"])

    @staticmethod
    def _store_share_nodes(share_config_node: dict, shares: list) -> None:
        """Put the share rows back the way the device renders them: a
        single share as a dict, several as a list."""
        share_config_node["Shares"]["Share"] = (
            shares[0] if len(shares) == 1 else shares)

    @staticmethod
    def _bump_config_version(share_config_node: dict) -> None:
        """Count one more change, as the device's ConfigVersionBasis
        does, so a simulated write is visible in the version too."""
        share_config_node["ConfigVersionBasis"] = str(
            int(share_config_node.get("ConfigVersionBasis", "0") or 0)
            + 1)

    @classmethod
    def _allowed_access_codes(cls, shares: list) -> set:
        """
        The access codes a simulated write may set: no access, the
        confirmed codes, and any code already stored on the device.

        The same rule as sharewrite: a code the device itself already
        uses is known to mean something to it; any other code would be
        an invention.
        """
        from drobo_nasd.shares import CONFIRMED_ACCESS_CODES
        from drobo_nasd.shareedit import NO_ACCESS_CODE
        allowed_codes = {NO_ACCESS_CODE} | set(CONFIRMED_ACCESS_CODES)
        stored_codes = (
            str(user_node.get("ShareUserAccess", "")).strip()
            for share_node in shares
            for user_node in cls._share_user_nodes(share_node))
        allowed_codes.update(code for code in stored_codes if code)
        return allowed_codes

    @staticmethod
    def _resolve_share(shares: list, share_name: str) -> dict | None:
        """
        The one share `share_name` means: an exact-case match wins,
        otherwise the name must case-fold to exactly one share.

        Returns None when nothing matches, so each caller can say so
        in its own words; raises when the name is ambiguous, because
        acting on a guess could change the wrong share.
        """
        exact_matches = [
            share_node for share_node in shares
            if (share_node.get("ShareName") or "").strip() == share_name]
        case_folded_matches = [
            share_node for share_node in shares
            if (share_node.get("ShareName") or "").strip().lower()
            == share_name.lower()]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(case_folded_matches) == 1:
            return case_folded_matches[0]
        if not case_folded_matches:
            return None
        raise ValueError(
            f"{len(case_folded_matches)} shares fold to "
            f"{share_name!r}; ambiguous -- refusing")

    @classmethod
    def _editable_share_users(cls, target_share: dict) -> tuple[dict, list]:
        """
        A share's <ShareUsers> node and its rows, ready to be edited.

        A share created by create_share holds ShareUsers None -- the
        dict rendering of the empty <ShareUsers/> element a real device
        stores for a share nobody can reach yet. `or {}` covers that as
        well as a missing key; setdefault would hand back the None and
        crash on the very flow the create endpoint tells people to
        follow. The node returned is new in that case, and the caller
        stores it back on the share.
        """
        share_users_node = target_share.get("ShareUsers") or {}
        users = cls._as_list(share_users_node.get("ShareUser") or [])
        return share_users_node, users

    @staticmethod
    def _user_row_for(users: list, username: str,
                      share_name: str) -> dict | None:
        """
        The row for `username` (ignoring case), or None when the user
        has no row on this share yet.

        Raises when several rows fold to the name, because changing one
        of them would be a guess.
        """
        matching_users = [
            user_node for user_node in users
            if (user_node.get("ShareUsername") or "").strip().lower()
            == username.lower()]
        if len(matching_users) > 1:
            raise ValueError(
                f"{len(matching_users)} user rows fold to {username!r} "
                f"on {share_name!r}; ambiguous -- refusing")
        return matching_users[0] if matching_users else None

    def set_share_access(self, share_name: str, username: str,
                         access_code: str) -> dict:
        """One user's access on one share, on the per-instance share
        config. Same rules as sharewrite: code must be 0, a CONFIRMED
        code, or one already on the device; the share must resolve
        unambiguously."""
        with self._lock:
            share_config_node = self._share_config_node()
            shares = self._share_nodes(share_config_node)
            allowed_codes = self._allowed_access_codes(shares)
            if access_code not in allowed_codes:
                raise ValueError(
                    f"access code {access_code!r} is neither confirmed nor "
                    f"on this device (allowed: {sorted(allowed_codes)}); "
                    f"refusing to invent a code")
            target_share = self._resolve_share(shares, share_name)
            if target_share is None:
                raise ValueError(
                    f"no share named {share_name!r} on this device")
            share_users_node, users = self._editable_share_users(target_share)
            user_row = self._user_row_for(users, username, share_name)
            # Last point at which nothing has changed yet -- where an
            # injected refusal has to happen for it to leave the device
            # untouched.
            self._refuse_if_injected()
            if user_row is not None:
                user_row["ShareUserAccess"] = access_code
            else:
                users.append(
                    {"ShareUsername": username,
                     "ShareUserAccess": access_code})
            share_users_node["ShareUser"] = (users[0] if len(users) == 1
                                             else users)
            target_share["ShareUsers"] = share_users_node
            self._bump_config_version(share_config_node)
            self._record_event(
                SEVERITY_INFO,
                f"{username!r} on {share_name!r} set to access "
                f"{access_code} (mock driver)")
            return self._write_ending()

    @staticmethod
    def _validate_new_share_name(name) -> str:
        """
        The name exactly as given, or ValueError if the project's name
        rule refuses it.

        Surrounding whitespace is refused, never stripped -- a stripped
        name could not be verified against a read-back. The SDK's own
        pattern is borrowed rather than restated: two copies of one rule
        is how the simulated and hardware paths drift apart.
        """
        share_name = name if isinstance(name, str) else ""
        if share_name != share_name.strip():
            raise ValueError(
                f"invalid share name {share_name!r}: surrounding whitespace "
                f"is refused rather than silently stripped -- a stripped name "
                f"could never be verified against the device's read-back")
        if not sharewrite._SHARE_NAME_PATTERN.match(share_name):
            raise ValueError(
                f"invalid share name {share_name!r}: "
                f"must be {sharewrite._SHARE_NAME_RULE}")
        return share_name

    def create_share(self, name: str) -> dict:
        """One new share on the per-instance share config -- the
        stand-in for sharewrite.create_share, applying the same
        refusals: the project's name rule on the name exactly as given
        (surrounding whitespace is refused, never stripped -- a stripped
        name could not be verified against a read-back), and no name
        that case-folds onto an existing share, because such a pair
        could never be addressed apart again.

        The stored row mirrors what the device holds for a share nobody
        has granted access to yet: ShareUsers empty (None is the dict
        rendering of an empty element, same as UserList above) and no
        VolumeName -- the 2026-08-25 capture shows the create NODE
        naming a volume while the stored share nodes never carry one."""
        share_name = self._validate_new_share_name(name)
        with self._lock:
            share_config_node = self._share_config_node()
            shares = self._share_nodes(share_config_node)
            clashing_names = [
                (share_node.get("ShareName") or "").strip()
                for share_node in shares
                if (share_node.get("ShareName") or "").strip().lower()
                == share_name.lower()]
            if clashing_names:
                raise ValueError(
                    f"a share named {clashing_names[0]!r} already exists on "
                    f"this device, and share names differing only by case "
                    f"could never be addressed apart afterwards -- "
                    f"refusing the duplicate")
            self._refuse_if_injected()
            shares.append({"ShareName": share_name, "ShareState": "0",
                           "TimeMachineEnabled": "0", "ShareUsers": None})
            self._store_share_nodes(share_config_node, shares)
            self._bump_config_version(share_config_node)
            self._record_event(SEVERITY_INFO,
                               f"share {share_name!r} created (mock driver)")
            return self._write_ending()

    def delete_share(self, name: str) -> dict:
        """Remove one share from the per-instance share config -- the
        stand-in for sharewrite.delete_share, resolving the target the
        same way: an exact-case match wins, otherwise the name must
        case-fold to exactly one share. On real hardware this destroys
        everything the share holds; the typed-confirm gate lives with
        the caller in both cases."""
        share_name = (name or "").strip()
        if not share_name:
            raise ValueError("no share name given")
        with self._lock:
            share_config_node = self._share_config_node()
            shares = self._share_nodes(share_config_node)
            target_share = self._resolve_share(shares, share_name)
            if target_share is None:
                existing_names = sorted(
                    (share_node.get("ShareName") or "").strip()
                    for share_node in shares)
                raise ValueError(
                    f"no share named {share_name!r} on this device "
                    f"(have: {existing_names})")
            self._refuse_if_injected()
            shares.remove(target_share)
            self._store_share_nodes(share_config_node, shares)
            self._bump_config_version(share_config_node)
            self._record_event(SEVERITY_INFO,
                               f"share {share_name!r} deleted (mock driver)")
            return self._write_ending()

    def get_demo_mode(self) -> dict:
        """Not in demo mode -- so the simulator's capacity numbers are
        "real" in the sense that matters: they are what this fake device
        genuinely holds, not shop-display fiction on top of a fake
        device."""
        return {"demo_mode": False, "scale_factor": 1}

    def identify(self, interval: int | None = None) -> None:
        """
        Pretend to blink the lights, and record it in the event log.

        The simulator cannot flash anything, but it can prove the whole
        path works -- button, endpoint, guard, result handling --
        without touching real hardware. That is how the first write in
        this project's history was actually built and tested.

        It records the INTERVAL as well as the count, because since
        2026-08-06 there is a value to carry and "did the interval
        survive the trip from the query string to the driver" is a
        question the simulator can answer and hardware cannot answer
        cheaply. `interval` defaults to None so older callers that pass
        nothing still work.
        """
        with self._lock:
            self._identify_count += 1
            self._identify_last_interval = interval
            self._record_event(SEVERITY_INFO,
                               f"Identify: front lights flashing, "
                               f"interval={interval} (mock driver)")

    #: The simulated Drobo's one account: user admin, password admin,
    #: set at the maintainer's request on 2026-09-14. Plain text on
    #: purpose and only here: it is a fixture for a pretend device,
    #: documented in the README as the demo login, and no real device or
    #: real login ever passes through it.
    _MOCK_ACCOUNTS = {"admin": "admin"}

    # Win32 codes the Windows app already explains in plain English, so
    # a simulated login fails with exactly the wording a real one would.
    _ERROR_ACCESS_DENIED = 5
    _ERROR_BAD_NET_NAME = 67
    _ERROR_LOGON_FAILURE = 1326

    def check_sign_in(self, share: str, username: str, password: str) -> dict:
        """
        Would this login open this share on the simulated Drobo?

        A real share login is decided by the Drobo's SMB server, which
        the simulator does not have -- its shares point at an address
        with no file server behind it, so Windows could never connect to
        one whatever the password. This answers the same question from
        the simulated config, so the Connect dialog can be exercised end
        to end without hardware.

        Follows what SMB does rather than inventing rules: share names
        and user names are case-insensitive, passwords are not; a user's
        own entry on a share decides, and without one the Everyone entry
        does.

        Returns {"ok", "win32_error", "error"}.
        Never returns the password.
        """
        with self._lock:
            target_share = self._find_share_case_insensitive(share)
            if target_share is None:
                return {"ok": False, "win32_error": self._ERROR_BAD_NET_NAME,
                        "error": (f"The simulated Drobo has "
                                  f"no share called {share!r}.")}

            account = next((name for name in self._MOCK_ACCOUNTS
                            if name.lower() == (username or "").lower()), None)
            if account is None or self._MOCK_ACCOUNTS[account] != password:
                return {"ok": False, "win32_error": self._ERROR_LOGON_FAILURE,
                        "error": "The username or password is wrong."}

            access_code = self._access_for_account(target_share, account)
            if access_code == "0":
                return {"ok": False, "win32_error": self._ERROR_ACCESS_DENIED,
                        "error": (f"{account} has no access to "
                                  f"{target_share.get('ShareName')}.")}
            return {"ok": True, "win32_error": 0, "error": ""}

    def _find_share_case_insensitive(self, share: str) -> dict | None:
        """
        The first share whose name matches `share` ignoring case, or
        None.

        Case-insensitive because SMB share names are. Reads the table
        tolerantly -- a missing level reads as no shares -- so a check
        against a config without shares answers "no such share".
        """
        shares = (self._shares.get("DRINASConfig", {})
                  .get("DRIShareConfig", {})
                  .get("Shares") or {}).get("Share") or []
        return next(
            (share_node for share_node in self._as_list(shares)
             if (share_node.get("ShareName") or "").lower()
             == (share or "").lower()), None)

    @classmethod
    def _access_for_account(cls, target_share: dict, account: str) -> str:
        """
        The access code `account` has on `target_share`: its own row
        if it has one, otherwise the Everyone row, otherwise "0".

        That is the order SMB decides in: a user's own entry overrides
        the group one.
        """
        access_by_user = {
            (user_node.get("ShareUsername") or "").lower():
                str(user_node.get("ShareUserAccess", ""))
            for user_node in cls._share_user_nodes(target_share)}
        return access_by_user.get(account.lower(),
                                  access_by_user.get("everyone", "0"))

    def get_config(self, section: str) -> dict:
        """Stand in for config_cmd.get_config
        against a simulated device.

        Answers in the real reply's shape, so the API runs the same
        parsing over it as over hardware, and refuses an unknown
        section rather than inventing an empty one. The refusal is a
        ValueError, which /api/droboconfig answers with 400, where the
        SDK's ConfigError gets 502; and "general", which the SDK knows,
        is unknown here.

        A copy, as a real reply is a fresh parse each time: until
        2026-11-02 the live share table and the class-level network and
        admin tables were handed out, so a caller's change reached the
        simulator itself and every later MockDriver.
        """
        with self._lock:
            sections = {
                "shares": self._shares,
                "network": self._MOCK_NETWORK,
                "admin": self._MOCK_ADMIN,
            }
            if section not in sections:
                raise ValueError(f"unknown section {section!r}")
            return copy.deepcopy(sections[section])

    def _record_event(self, severity: str, message: str) -> None:
        """Add one entry to the simulated device's event log, so every
        simulated change shows up where a real device would report
        it."""
        self._event_log.append(EventLogEntry(
            ts=time.time(), severity=severity, message=message))

    # -- fault injection -----------------------------------------------

    def fail_bay(self, bay: int) -> None:
        """Simulate a failed drive in `bay`, so the drive-failure alert
        can be watched end to end. Sticky until reset()."""
        with self._lock:
            self._bay_state_overrides[bay] = STATE_FAILED

    def warn_bay(self, bay: int) -> None:
        """Simulate a SMART warning on the drive in `bay`. Sticky until
        reset()."""
        with self._lock:
            self._bay_state_overrides[bay] = STATE_WARNING

    def pull_bay(self, bay: int) -> None:
        """Simulate someone removing the drive from `bay`: the bay reads
        as empty until reset()."""
        with self._lock:
            self._bay_state_overrides[bay] = "pulled"

    def fail_battery(self, state: str = STATE_FAILED) -> None:
        """Simulate cache-battery trouble.
        state is STATE_WARNING or STATE_FAILED.
        """
        with self._lock:
            self._battery_override = state
            self._record_event(
                self._severity_for_injected_state(state),
                f"Cache battery reports {state} (mock driver)",
            )

    def fail_power(self, state: str = STATE_FAILED) -> None:
        """Simulate a power-supply problem in the given state, so the
        PSU alert can be exercised; a real 5N has never answered with
        one. Sticky until reset()."""
        with self._lock:
            self._power_override = state
            self._record_event(self._severity_for_injected_state(state),
                               f"PSU reports {state} (mock driver)")

    @staticmethod
    def _severity_for_injected_state(state: str) -> str:
        """
        How the simulated event log rates an injected battery or PSU
        state: a failure as critical, a warning as a warning.

        Until 2026-11-02 a failed battery was logged as info, milder
        than a warning one, and a failed PSU only as a warning, so the
        event-log viewers ranked a failure below the warning it follows.
        """
        if state == STATE_FAILED:
            return SEVERITY_CRITICAL
        return SEVERITY_WARNING

    def fail_write(self, write_fault: str = WRITE_FAULT_HANGS_UP) -> None:
        """
        Make every simulated configuration write from here on end
        the given way -- see the WRITE_FAULT_* constants for what each
        one does.

        Sticky like the bay and PSU faults, and cleared by reset() the
        same way, because the useful thing is to arm it once and then
        drive the whole UI through the ending it produces.
        WRITE_FAULT_HANGS_UP is the one measured on hardware; the others
        are its neighbours, kept here so all four outcomes the API
        reports can be exercised.
        """
        if write_fault not in WRITE_FAULTS:
            raise ValueError(
                f"unknown write fault {write_fault!r}; "
                f"choose from {list(WRITE_FAULTS)}")
        with self._lock:
            self._write_fault = write_fault
            self._record_event(
                SEVERITY_INFO,
                f"config writes will now end as {write_fault!r} (mock driver)")

    def reset(self) -> None:
        """Clear every injected fault -- bays, battery, PSU and write
        ending -- and go back to a healthy device. Only the faults:
        the event log, the simulated configuration and the Identify
        counters are left as they are."""
        with self._lock:
            self._bay_state_overrides.clear()
            self._battery_override = None
            self._power_override = None
            self._write_fault = WRITE_FAULT_NONE

    # -- driver interface ----------------------------------------------

    def read_snapshot(self) -> Snapshot:
        """
        One simulated poll: every reading drifts a little from the last
        and every injected fault shows, so the monitor and the UIs have
        something changing to react to without hardware.

        The random draws happen in one fixed order -- drive
        temperatures bay by bay, then battery, fan and performance --
        under the lock, so a poll is one consistent reading.
        """
        with self._lock:
            elapsed_seconds = time.time() - self._started_at
            # Creep usage upward slowly so the capacity graph has
            # something to draw, and wobble temperatures on a slow sine
            # plus noise.
            self._used_bytes += int(
                gigabytes_to_bytes(self._USAGE_GROWTH_GB_PER_POLL))
            drives = [self._simulate_drive_slot(bay_spec, elapsed_seconds)
                      for bay_spec in self._bay_specs]
            capacity = self._simulated_capacity(drives)

            battery = self._read_battery(elapsed_seconds)
            fan = self._read_fan(elapsed_seconds)
            power = self._read_power()
            performance = self._read_performance(elapsed_seconds)

            return Snapshot(
                reachable=True,
                source=self.name,
                device=DeviceInfo(
                    name="MockDrobo",
                    model="Drobo 5N",
                    serial="MOCK-5N-0001",
                    firmware="4.2.2",
                    ip="127.0.0.1",
                    uptime_seconds=int(elapsed_seconds),
                ),
                capacity=capacity,
                drives=drives,
                volumes=self._simulated_volumes(capacity.used_bytes),
                max_volumes=16,
                battery=battery,
                fan=fan,
                power=power,
                performance=performance,
                event_log=list(self._event_log),
            )

    def _simulate_drive_slot(self, bay_spec: dict,
                             elapsed_seconds: float) -> DriveSlot:
        """
        One bay as this poll sees it: empty if it has no drive or was
        pulled, otherwise its drive with a drifting temperature and any
        injected fault.

        Only a bay with a drive in it draws a random number. Keep it
        so: the draw order decides what a seeded run produces.
        """
        bay = bay_spec["bay"]
        bay_state_override = self._bay_state_overrides.get(bay)
        if bay_state_override == "pulled" or not bay_spec["present"]:
            return DriveSlot(bay=bay, present=False, state=STATE_EMPTY)
        drive_temperature_c = (
            bay_spec["baseline_temperature_c"]
            + 3.0 * math.sin(
                elapsed_seconds / self._DRIVE_TEMPERATURE_SECONDS_PER_RADIAN
                + bay)
            + random.uniform(-0.4, 0.4)
        )
        state = bay_state_override or STATE_OK
        note = ""
        if state == STATE_FAILED:
            note = "simulated failure (mock driver)"
            drive_temperature_c = None
        elif state == STATE_WARNING:
            note = "simulated SMART warning (mock driver)"
        return DriveSlot(
            bay=bay,
            present=True,
            state=state,
            model=bay_spec["model"],
            serial=bay_spec["serial"],
            capacity_bytes=bay_spec["capacity_bytes"],
            temperature_c=(
                round(drive_temperature_c, 1)
                if drive_temperature_c is not None else None),
            note=note,
        )

    def _simulated_capacity(self, drives: list[DriveSlot]) -> Capacity:
        """
        The pack's capacity from the drives present, with single-disk
        redundancy: usable is roughly raw minus the largest drive.

        A failed drive still counts towards raw capacity (it is in the
        chassis) but not towards usable, so a failure also shows as
        lost usable space.
        """
        raw_bytes = sum(drive.capacity_bytes for drive in drives
                        if drive.present)
        healthy_raw_bytes = sum(
            drive.capacity_bytes for drive in drives
            if drive.present and drive.state != STATE_FAILED
        )
        largest_drive_bytes = max(
            (drive.capacity_bytes for drive in drives if drive.present),
            default=0)
        usable_bytes = max(healthy_raw_bytes - largest_drive_bytes, 0)
        used_bytes = min(self._used_bytes, usable_bytes)
        return Capacity(
            raw_bytes=raw_bytes,
            usable_bytes=usable_bytes,
            used_bytes=used_bytes,
            free_bytes=max(usable_bytes - used_bytes, 0),
            protection_bytes=largest_drive_bytes,
        )

    def _simulated_volumes(self, used_bytes: int) -> list[Volume]:
        """
        Two volumes, where the real 5N reports one, sharing the used
        space evenly.

        Deliberate: a single-volume pack is the case that hides layout
        bugs, and the whole point of the simulator is to show the
        states real hardware won't. Field values mirror what the device
        actually sends, including the undocumented
        partition_type/format.
        """
        return [
            Volume(lun=0, unique_id="1000", name="",
                   max_size_bytes=self._VOLUME_MAX_SIZE_BYTES,
                   used_bytes=used_bytes // 2,
                   partition_count=2, partition_type=3,
                   partition_format=64, share_state=0),
            Volume(lun=1, unique_id="1001", name="Media",
                   max_size_bytes=self._VOLUME_MAX_SIZE_BYTES,
                   used_bytes=used_bytes // 2,
                   partition_count=1, partition_type=3,
                   partition_format=64, share_state=0),
        ]

    # -- Phase 1 scope additions ---------------------------------------

    def _read_battery(self, elapsed_seconds: float) -> BatteryInfo:
        """eCmdCacheBattery. Charge drifts near-full with a slow wobble;
        a failure override drains it and marks the state, same idea as a
        real battery that's stopped holding charge."""
        if self._battery_override == STATE_FAILED:
            return BatteryInfo(
                state=STATE_FAILED, charge_pct=0.0,
                note="cache battery failed (mock driver) -- "
                     "write cache is not safe across a power loss",
            )
        if self._battery_override == STATE_WARNING:
            charge_pct = max(0.0, 35.0 + 2.0 * math.sin(
                elapsed_seconds / self._WEAK_BATTERY_SECONDS_PER_RADIAN))
            return BatteryInfo(
                state=STATE_WARNING, charge_pct=round(charge_pct, 1),
                note="cache battery is weak (mock driver) -- "
                     "consider a replacement",
            )
        charge_pct = (self._battery_charge_baseline_pct
                      + 1.5 * math.sin(
                          elapsed_seconds
                          / self._BATTERY_CHARGE_SECONDS_PER_RADIAN)
                      + random.uniform(-0.3, 0.3))
        charge_pct = max(0.0, min(100.0, charge_pct))
        return BatteryInfo(
            state=STATE_OK, charge_pct=round(charge_pct, 1), note="")

    def _read_fan(self, elapsed_seconds: float) -> FanInfo:
        """eCmdGetFanInfo. RPM drifts a little; stalls if the fan bay
        was also given a state via the drive overrides (not modelled --
        fans just idle around their baseline for now)."""
        rpm = (self._fan_rpm_baseline
               + 60.0 * math.sin(
                   elapsed_seconds / self._FAN_SPEED_SECONDS_PER_RADIAN)
               + random.uniform(-15.0, 15.0))
        rpm = max(0, int(rpm))
        state = STATE_OK if rpm > self._FAN_SLOW_RPM else STATE_WARNING
        return FanInfo(rpm=rpm, state=state)

    def _read_power(self) -> PowerInfo:
        """eCmdGetPowerInfo. Healthy unless fail_power() injected a
        state: the real 5N never answered this command, so the
        simulator is the only place the PSU alert can be seen."""
        if self._power_override:
            note = "simulated PSU problem (mock driver)"
            return PowerInfo(state=self._power_override, note=note)
        return PowerInfo(state=STATE_OK, note="")

    def _read_performance(self, elapsed_seconds: float) -> PerformanceInfo:
        """
        eCmdGetPerformance. A gentle, bounded random walk so the graph
        has something to show without being pure noise.

        Scaled to MEGABYTES per second, matching what the real device
        reports (it sends single- and double-digit integers, not byte
        counts -- see sdk/drobo_nasd/sysinfo.parse_performance). This
        used to simulate bytes/second, which made the mock produce
        numbers eight orders of magnitude away from anything real
        hardware ever sends -- exactly the kind of fixture that lets a
        unit bug survive a green test suite.
        """
        baseline_read_mb_per_s = 45.0 + 20.0 * math.sin(
            elapsed_seconds / self._READ_THROUGHPUT_SECONDS_PER_RADIAN)
        baseline_write_mb_per_s = 18.0 + 8.0 * math.sin(
            elapsed_seconds / self._WRITE_THROUGHPUT_SECONDS_PER_RADIAN + 1.0)
        read_mb_per_s = max(
            0, int(baseline_read_mb_per_s + random.uniform(-3.0, 3.0)))
        write_mb_per_s = max(
            0, int(baseline_write_mb_per_s + random.uniform(-1.5, 1.5)))
        return PerformanceInfo(
            read_mb_per_s=read_mb_per_s,
            write_mb_per_s=write_mb_per_s,
            iops=max(0, int(read_mb_per_s * 4 + write_mb_per_s * 2
                            + random.uniform(-5, 5))),
            measured=True,
            measured_at=time.time(),  # simulated afresh on every poll
        )


# ---------------------------------------------------------------------------
# Real driver -- Drobo 5N
# ---------------------------------------------------------------------------


class Drobo5NDriver(DroboDriver):
    """
    The real thing --
    confirmed working against a live Drobo 5N on 2026-07-24.

    On connect, nasd (TCP 5000) pushes a complete <ESATMUpdate> status
    document with no login required, so a poll is just: reconnect, read
    the greeting, parse it. All the protocol detail lives in
    sdk/drobo_nasd/esatm.py.

    config, static IP (works exactly as before):
        "drobo": {"driver": "drobo5n", "host": "10.0.0.50",
                  "port": 5000}

    config, no static IP needed -- "host" absent or "auto" runs
    discovery (sdk/drobo_nasd/discovery.py: mDNS, then optional
    "hostname", then a last-known IP remembered in memory, then an
    opt-in subnet sweep):
        "drobo": {
            "driver": "drobo5n",
            "host": "auto",
            "hostname": "MyDrobo.local",
            "expected_esa_id": "drbXXXXXXXXXXXX",
            "discovery_sweep": false
        }

    (The values above are placeholders. Your own device's serial is
    printed in the agent log on first successful connect -- paste that
    in. Real serials are deliberately kept out of the source tree; see
    ROADMAP.md Phase 6.)

    expected_esa_id pins discovery to one physical device (mESAID is the
    permanent hardware serial, so it survives IP changes and even a
    repack). Leave it blank for first-run pairing: discovery still
    connects, but logs the discovered esa_id prominently so it can be
    pasted into config.json. Once set, a candidate that answers with a
    DIFFERENT esa_id is refused, never silently trusted -- see
    discovery.IdentityMismatchError.

    Read-only, but no longer silent: as of 2026-07-26 it also asks the
    device for its chassis temperature (eCmdGetSysInfo, a query) at most
    once every two minutes. See _read_sysinfo() for why it is that
    infrequent. Since 2026-08-21 it also fetches the device's own event
    log (eCmdGetUserEventLog, another query) on its own, slower cadence
    -- see _read_event_log().

    Not available from the greeting (all report 0 or aren't present):
    per-drive temperature, cache battery, fan, PSU, performance.
    Per-drive temperature appears not to exist on a 5N; the chassis
    reading above is the real one. Fan and PSU were probed directly on
    the command port and never answered (docs/command-surface.md). Those
    health-extra fields stay at their STATE_UNKNOWN defaults.
    """

    name = "drobo5n"

    def __init__(self, drobo_config: dict):
        """
        Read the drobo block of config.json. Nothing touches the
        network here: connect() or the first read_snapshot() does,
        so an agent can start before its Drobo is reachable.
        """
        super().__init__(drobo_config)
        self._read_connection_settings(drobo_config)
        self._init_command_port_caches(drobo_config)

    def _read_connection_settings(self, drobo_config: dict) -> None:
        """Where to find the device and how to know it is the right
        one: the address or discovery settings, and the identity
        state discovery keeps between polls."""
        self.host = (drobo_config.get("host") or "").strip()
        self.port = int(drobo_config.get("port", esatm.DEFAULT_PORT))
        self.timeout_seconds = float(drobo_config.get("timeout", 8.0))
        self.hostname = (drobo_config.get("hostname") or "").strip()
        self.expected_esa_id = (
            drobo_config.get("expected_esa_id") or "").strip()
        self.discovery_sweep_enabled = bool(
            drobo_config.get("discovery_sweep", False))

        # Addresses somebody typed because mDNS doesn't work on their
        # network. Tried before the multicast browse -- see
        # discovery._gather_candidates for why that order, and why it
        # changes nothing about identity verification. This used to
        # exist on the MOCK driver as a config-shaped placeholder that
        # nothing read; it is now the real thing, on the real driver,
        # where it can actually reach a device.
        self.manual_discovery_ips: list[str] = [
            str(ip).strip()
            for ip in (drobo_config.get("manual_discovery_ips") or [])
            if str(ip).strip()
        ]

        # In-memory only. _resolved_host/_port is whatever address
        # actually worked last, used both to report snapshot.device.ip
        # and as the "last-known-good IP" discovery tries first on a
        # re-resolve. If expected_esa_id was left blank, _known_esa_id
        # still gets filled in after the first successful contact, so a
        # later re-discovery (after a failure) keeps confirming identity
        # against the SAME device rather than being willing to accept
        # literally anything that answers.
        self._resolved_host: str = ""
        self._resolved_port: int = self.port
        self._known_esa_id: str = self.expected_esa_id
        # Set when a poll's greeting named another serial, cleared when
        # ours answers again. While it is set the agent's command routes
        # refuse to send (api._command_target), because the address they
        # would use belongs to a Drobo that is not ours.
        self.answering_as_another_drobo: bool = False

    def _init_command_port_caches(self, drobo_config: dict) -> None:
        """The readings that come from the command port rather than the
        status greeting, each cached with the time it was read, because
        they are fetched far less often than the device is polled."""
        # Temperature comes from a command, not the status greeting, so
        # it costs a second connection to the device. See
        # _read_sysinfo().
        self.sysinfo_seconds = float(
            drobo_config.get("sysinfo_seconds", 120.0))
        self._temperature_c: int | None = None
        self._temperature_read_at: float | None = None
        self._sysinfo_tried_at: float = 0.0
        # Uptime rides the same reply but keeps its own timestamp: a
        # reply can carry either reading without the other, and each
        # must age from the moment it was actually read.
        self._uptime_seconds: int | None = None
        self._uptime_read_at: float | None = None
        # Throughput/IOPS, fetched on the same connection as the
        # temperature. None until the device has answered once --
        # distinct from "all zeros", which is what a real but idle array
        # reports.
        self._performance: dict | None = None
        # When `_performance` arrived -- the twin of
        # `_temperature_read_at`. A failed fetch keeps the old figures,
        # so this, not the poll time, is their age.
        self._performance_read_at: float | None = None
        # The device's own event log (eCmdGetUserEventLog), cached like
        # the temperature but on a slower clock: it costs its own
        # command-port connection, and a log changes far less often than
        # a fan spins. See _read_event_log() for the failure handling.
        self.eventlog_seconds = float(
            drobo_config.get("eventlog_seconds", 600.0))
        self._event_log: list[EventLogEntry] = []
        self._eventlog_tried_at: float = 0.0
        self._eventlog_last_warning: str = ""

    @property
    def _uses_auto_discovery(self) -> bool:
        """True when config.json names no address (or says "auto"), so
        the device has to be found by discovery rather than dialled."""
        return not self.host or self.host.lower() == "auto"

    def target_host(self) -> str:
        """
        Whatever address we'd try next: the one that last worked, else
        the configured static IP.

        In auto mode with nothing resolved yet this is "" -- not a
        guess. A Drobo we have never reached is a Drobo whose network we
        do not know, and "your PC is on the wrong network for it" would
        be an invention.
        """
        if self._resolved_host:
            return self._resolved_host
        return "" if self._uses_auto_discovery else self.host

    def _remember_working_address(self, esa_id: str, host: str,
                                  port: int) -> None:
        """
        Record the address that just answered, and the serial if none
        is pinned yet.

        The serial is filled only when empty: the first device reached
        becomes the one later discoveries must match, which is what
        stops a re-discovery from accepting whatever answers next. This
        method does not compare serials itself; discovery, connect() and
        the poll do, and call it only for ours, so it also lifts the
        refusal a stranger's greeting set.
        """
        self._resolved_host = host
        self._resolved_port = port
        self.answering_as_another_drobo = False
        if not self._known_esa_id:
            self._known_esa_id = esa_id

    def _discover(self) -> discovery.VerificationResult:
        """
        Find the Drobo by identity and remember where it answered.

        A device that answers with the wrong serial is refused with
        discovery's own message; finding nothing becomes
        DroboNotFoundError, which the monitor treats as "not reachable
        yet" rather than as a configuration error.
        """
        last_known_host = self._resolved_host or (
            self.host if not self._uses_auto_discovery else "")
        try:
            verified = discovery.resolve(
                expected_esa_id=self._known_esa_id,
                hostname=self.hostname,
                last_known_ip=last_known_host,
                port=self.port,
                try_sweep=self.discovery_sweep_enabled,
                timeout=self.timeout_seconds,
                manual_ips=self.manual_discovery_ips,
            )
        except discovery.IdentityMismatchError:
            # A device answered, and it is provably not ours. Let this
            # through untouched: discovery.py already produced a precise
            # message naming both serials, and reframing it as "couldn't
            # find anything" would be actively misleading -- we DID find
            # something and refused it. Caught explicitly (rather than
            # relying on the exception hierarchy) so the safety property
            # is visible in the code.
            raise
        except LookupError as exc:
            raise DroboNotFoundError(
                self._not_found_message(last_known_host, exc)) from exc
        self._remember_working_address(
            verified.identity["esa_id"], verified.candidate.host,
            verified.candidate.port)
        if verified.first_run:
            self._print_first_run_pairing(verified)
        return verified

    def _not_found_message(self, last_known_host: str,
                           exc: LookupError) -> str:
        """
        What the "cannot reach" alert says when discovery found nothing:
        what was tried, discovery's own reason, and what to check.

        The advice leads with the two checks that settle most cases.
        This text used to be read mainly in the console the agent left
        behind when it exited, under a footer from Run-Agent.bat saying
        the same thing; the agent no longer exits here, so the text has
        to carry the first thing to check itself. The config.json route
        is for a network that blocks mDNS, which is the rarer case.
        """
        return (
            "Could not find the Drobo automatically (tried "
            + ", ".join(self._discovery_steps_tried(last_known_host))
            + f"). {exc}\n"
            "Check the Drobo is switched on and this PC is on the same "
            "network. If both are true and this network blocks mDNS, set "
            '"drobo": {"driver": "drobo5n", "host": "<drobo-ip>"} in '
            "config.json, or find the IP with:  "
            "py tools/drobo_probe.py mdns"
        )

    def _discovery_steps_tried(self, last_known_host: str) -> list[str]:
        """
        What discovery tried, in the order discovery tries it
        (discovery._gather_candidates): typed addresses, mDNS, hostname,
        last-known IP, subnet sweep. Until 2026-11-02 the message put
        mDNS ahead of the typed addresses.
        """
        steps = []
        if self.manual_discovery_ips:
            steps.append(f"{len(self.manual_discovery_ips)} "
                         "manually-added address(es)")
        steps.append("mDNS")
        if self.hostname:
            steps.append(f"hostname {self.hostname!r}")
        if last_known_host:
            steps.append(f"last-known IP {last_known_host!r}")
        if self.discovery_sweep_enabled:
            steps.append("subnet sweep")
        return steps

    @staticmethod
    def _print_first_run_pairing(
            verified: discovery.VerificationResult) -> None:
        """Print the serial a first-run discovery found, with the line
        to paste into config.json, because until expected_esa_id is set
        a stranger on this address could later be trusted."""
        print(
            "[drobo5n] first-run pairing: no expected_esa_id "
            "configured yet. This device identifies as "
            f"esa_id={verified.identity['esa_id']!r} "
            f"({verified.identity.get('name', '?')!r}, "
            f"{verified.identity.get('model', '?')!r}, "
            f"firmware {verified.identity.get('firmware', '?')!r}). "
            f'Add  "expected_esa_id": '
            f'"{verified.identity["esa_id"]}"  to the '
            '"drobo" block in config.json so a future stranger on this '
            "IP can never be silently trusted.",
            flush=True,
        )

    def connect(self) -> None:
        """
        Reach the device once at startup: by discovery in auto mode,
        otherwise at the configured address.

        A configured address is checked against expected_esa_id when
        one is set, so a static IP that now belongs to another device
        is refused at startup rather than trusted.
        """
        if self._uses_auto_discovery:
            verified = self._discover()
            print(
                f"[drobo5n] discovered "
                f"{verified.candidate.host}:{verified.candidate.port} "
                f"via {verified.candidate.source} -- "
                f"{verified.identity.get('name', '?')} "
                f"(esa_id={verified.identity['esa_id']})",
                flush=True,
            )
            return

        # Explicit IP: identical to the original behaviour by default.
        # If expected_esa_id IS set, also refuse a mismatch here -- an
        # admin who hardcoded an IP still deserves protection against
        # DHCP quietly handing that address to a different device later.
        snapshot = esatm.read_snapshot(self.host, self.port,
                                       self.timeout_seconds)
        if (self.expected_esa_id
                and snapshot.device.serial != self.expected_esa_id):
            raise discovery.IdentityMismatchError(
                f"refusing {self.host}:{self.port} -- it identifies as "
                f"esa_id={snapshot.device.serial!r}, not the expected "
                f"{self.expected_esa_id!r}. "
                f"This is not your Drobo; not connecting."
            )
        self._remember_working_address(snapshot.device.serial, self.host,
                                       self.port)
        print(f"[drobo5n] connected to {snapshot.device.name or self.host} "
              f"(firmware {snapshot.device.firmware})", flush=True)

    def read_snapshot(self) -> Snapshot:
        """
        One poll: read the status greeting, re-discovering once if the
        last address has stopped answering or now answers as another
        Drobo, then fill in the cached command-port readings.

        The command-port top-ups run on their own slower clocks and
        never raise, so a poll fails only when the greeting itself
        cannot be read, or comes only from a Drobo that is not ours.
        """
        host, port, snapshot = self._read_greeting_from_our_drobo()
        self._remember_working_address(snapshot.device.serial, host, port)
        snapshot.device.ip = host
        self._read_sysinfo(host)
        snapshot.device.temperature_c = self._temperature_c
        snapshot.device.temperature_at = self._temperature_read_at
        self._apply_cached_uptime(snapshot)
        self._apply_cached_performance(snapshot)
        self._read_event_log(host)
        snapshot.event_log = list(self._event_log)
        return snapshot

    def _read_greeting_from_our_drobo(self) -> tuple[str, int, Snapshot]:
        """
        The status greeting from the pinned Drobo, and where it
        answered.

        A greeting carrying another serial is treated like an address
        that stopped answering: discovery runs again, identity-checked,
        so the stranger is refused there. Until 2026-11-02 the serial
        was never compared, so an address DHCP had handed to another
        Drobo was trusted, and the command-port top-ups logged in to it
        with our serial. A mismatch also sets
        answering_as_another_drobo, so the API's command routes stop
        sending to that address too. Raises
        discovery.IdentityMismatchError, or DroboNotFoundError, when no
        greeting from ours can be had.
        """
        host, port = self._resolved_host, self._resolved_port
        if not host:
            # connect() was never called (or auto mode hasn't resolved
            # yet): discover now rather than failing with "no host
            # configured".
            verified = self._discover()
            host, port = verified.candidate.host, verified.candidate.port

        try:
            snapshot = esatm.read_snapshot(host, port, self.timeout_seconds)
        except (OSError, esatm.FrameError):
            # The address that used to work doesn't anymore -- almost
            # always a DHCP lease change. Re-run discovery instead of
            # dying, so a Drobo that moved to a new IP self-heals
            # without a restart, then retry once against whatever
            # discovery finds. If discovery also comes up empty, that
            # exception (already framed clearly) is what the caller sees
            # -- this never masks a real outage as success.
            return self._rediscover_and_read_greeting()
        if self._is_pinned_drobo(snapshot):
            return host, port, snapshot
        self.answering_as_another_drobo = True
        return self._rediscover_and_read_greeting()

    def _rediscover_and_read_greeting(self) -> tuple[str, int, Snapshot]:
        """
        Run discovery again and read the greeting where it found our
        Drobo, refusing it if that greeting names another serial (a
        device swapped in between discovery's read and this one).
        """
        verified = self._discover()
        host, port = verified.candidate.host, verified.candidate.port
        snapshot = esatm.read_snapshot(host, port, self.timeout_seconds)
        if not self._is_pinned_drobo(snapshot):
            self.answering_as_another_drobo = True
            raise discovery.IdentityMismatchError(
                f"refusing {host}:{port} -- it identifies as "
                f"esa_id={snapshot.device.serial!r}, not the expected "
                f"{self._known_esa_id!r}. "
                f"This is not your Drobo; not reading it.")
        return host, port, snapshot

    def _is_pinned_drobo(self, snapshot: Snapshot) -> bool:
        """True when no serial is pinned yet, or the greeting carries
        the pinned one."""
        return (not self._known_esa_id
                or snapshot.device.serial == self._known_esa_id)

    def _apply_cached_uptime(self, snapshot: Snapshot) -> None:
        """
        Put the cached uptime on `snapshot`, advanced by the time since
        it was read.

        Uptime arrives with the temperature, then keeps counting on its
        own -- no need to ask the device again just to watch a clock.
        """
        if self._uptime_seconds is None or self._uptime_read_at is None:
            return
        seconds_since_read = time.time() - self._uptime_read_at
        snapshot.device.uptime_seconds = int(
            self._uptime_seconds + seconds_since_read)

    def _apply_cached_performance(self, snapshot: Snapshot) -> None:
        """
        Put the cached throughput and IOPS on `snapshot`, once the
        device has answered at least once.

        measured=True even when every counter is zero: an idle array
        really does report zeros, and that is a reading, not a gap.
        measured_at is when they arrived, which after a failed fetch is
        earlier than this poll -- the UIs show that age.
        """
        if self._performance is None:
            return
        snapshot.performance = PerformanceInfo(
            read_mb_per_s=self._performance.get("read_mb_per_s") or 0,
            write_mb_per_s=self._performance.get("write_mb_per_s") or 0,
            iops=self._performance.get("iops") or 0,
            tier_iops=self._performance.get("tier_iops") or 0,
            measured=True,
            measured_at=self._performance_read_at,
        )

    # -- temperature ---------------------------------------------------

    def _read_sysinfo(self, host: str) -> None:
        """
        Top up the cached chassis temperature, at most once every
        `sysinfo_seconds`. Never raises.

        Two reasons this is deliberately infrequent rather
        than once per poll.

        The honest one: it opens a second TCP connection and sends a
        command, where the rest of this driver only listens. That is a
        bigger imposition on a 2013 embedded box than reading a
        greeting, and it earns its keep at two-minute resolution -- a
        NAS chassis does not change temperature meaningfully in ten
        seconds.

        The one learned the hard way: on 2026-07-26 this command
        answered 15 times out of 15, and then -- after roughly ninety
        command connections within about an hour, across various tools
        -- stopped answering entirely, while eCmdGetConfig on the same
        port kept working 3/3. The device had not crashed and the array
        stayed healthy, but something inside nasd stopped servicing this
        particular request. We do not know what, and we are not going to
        find out by doing it again to the single unit available. So: ask
        rarely, accept a stale answer, and never retry a failure in a
        tight loop.

        It cleared on its own: left alone, with no probing beyond this
        driver's ordinary `sysinfo_seconds`-spaced poll, the command
        started answering again after about 30 minutes, with no restart
        needed. So this is a temporary refusal under load, not damage --
        but the recovery window is long enough that hammering the port
        costs a real stretch of "no fresh reading", which is the other
        reason to keep this infrequent rather than something to retry.

        A failure is not an error anywhere. It means "no fresh reading",
        and the last good value stays put with its original timestamp so
        whatever displays it can say how old it is.
        """
        now = time.time()
        if now - self._sysinfo_tried_at < self.sysinfo_seconds:
            return
        self._sysinfo_tried_at = now
        if not self._known_esa_id:
            return  # the command port needs the serial; we don't have one yet
        try:
            # Both readings share this one connection. Given the
            # exhaustion described above, connection count is the thing
            # worth conserving -- a second command on a socket we
            # already hold costs almost nothing.
            sysinfo_reply = sysinfo.get_sysinfo_and_performance(
                host, self._known_esa_id,
                timeout=min(self.timeout_seconds,
                            _COMMAND_TIMEOUT_CAP_SECONDS))
        except (OSError, config_cmd.ConfigError, sysinfo.SysInfoError):
            return  # unavailable right now; keep whatever we already had
        self._store_sysinfo_reply(sysinfo_reply, now)

    def _store_sysinfo_reply(self, sysinfo_reply: dict, now: float) -> None:
        """
        Cache each reading the reply actually carries, stamped `now`.

        Each reading is kept or replaced on its own: a reply can carry
        the temperature without the uptime or the other way round, and
        a reading the reply lacks keeps its earlier value and age.
        """
        if sysinfo_reply.get("temperature_c") is not None:
            self._temperature_c = sysinfo_reply["temperature_c"]
            self._temperature_read_at = now
        if sysinfo_reply.get("uptime_seconds") is not None:
            self._uptime_seconds = sysinfo_reply["uptime_seconds"]
            self._uptime_read_at = now

        performance = sysinfo_reply.get("performance")
        if performance and any(reading is not None
                               for reading in performance.values()):
            self._performance = performance
            self._performance_read_at = now

    # -- event log -----------------------------------------------------

    def _read_event_log(self, host: str) -> None:
        """
        Top up the cached event log, at most once every
        `eventlog_seconds`. Never raises.

        Same discipline as _read_sysinfo, with the clock set even slower
        (ten minutes by default): the fetch opens its own command-port
        connection, and that port measurably stops answering under
        connection volume -- see _read_sysinfo's account of the
        2026-07-26 exhaustion. A log that is minutes stale costs
        nothing; a command port sulking for half an hour costs every
        fresh reading this driver makes.

        A failure never touches the poll: the snapshot degrades to
        whatever the cache holds -- the last good log, or [] if the
        device has never answered -- and the failure is printed once per
        distinct message rather than every attempt, because
        eventlog.get_event_log refuses BY DESIGN if the (never-captured)
        reply turns out to be an XML schema, and that refusal would
        otherwise repeat verbatim forever.

        Entries arrive with eventlog.TIMESTAMP_NOT_DECODED /
        SEVERITY_NOT_DECODED markers -- the reply's line format is
        uncaptured, so the SDK will not invent timestamps or severities.
        Passed through untouched; presenting an undecoded field is the
        UI's call, not this driver's.
        """
        now = time.time()
        if now - self._eventlog_tried_at < self.eventlog_seconds:
            return
        self._eventlog_tried_at = now
        if not self._known_esa_id:
            return  # the command port needs the serial; we don't have one yet
        try:
            entries = eventlog.get_event_log(
                host, self._known_esa_id,
                timeout=min(self.timeout_seconds,
                            _COMMAND_TIMEOUT_CAP_SECONDS))
        except (OSError, config_cmd.ConfigError,
                eventlog.EventLogError) as exc:
            error_text = str(exc)
            if error_text != self._eventlog_last_warning:
                self._eventlog_last_warning = error_text
                print(f"[drobo5n] event log unavailable: {error_text}",
                      flush=True)
            return  # keep whatever the cache already holds
        self._eventlog_last_warning = ""
        self._event_log = entries


DRIVERS: dict[str, type[DroboDriver]] = {
    "mock": MockDriver,
    "drobo5n": Drobo5NDriver,
}


def build(drobo_config: dict) -> DroboDriver:
    """
    The driver config.json's drobo block names, the simulator when it
    names none.

    The monitor builds its driver only through here, so an unknown name
    is refused with the list of real ones instead of failing later in
    some less obvious way.
    """
    driver_name = (drobo_config.get("driver") or "mock").lower()
    if driver_name not in DRIVERS:
        raise ValueError(
            f"unknown driver {driver_name!r}; choose from {sorted(DRIVERS)}")
    return DRIVERS[driver_name](drobo_config)
