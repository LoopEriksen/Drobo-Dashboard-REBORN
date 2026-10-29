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


class DroboDriver:
    """Base class.
    A driver's whole job is to produce a Snapshot on demand.
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
        else is a bug and is left to surface as one.
        """

    def read_snapshot(self) -> Snapshot:
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

    def __init__(self, drobo_config: dict):
        super().__init__(drobo_config)
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._bay_state_overrides: dict[int, str] = {}
        self._bay_specs: list[dict] = []
        # fixed seed so restarts look consistent
        seeded_random = random.Random(5150)
        for bay in range(1, 6):
            populated = bay <= 4
            self._bay_specs.append(
                {
                    "bay": bay,
                    "present": populated,
                    "model": (seeded_random.choice(self._DRIVE_MODELS)
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
        self._used_bytes = gigabytes_to_bytes(6800)

        # -- Phase 1 scope additions (ROADMAP.md) -- believable
        # baselines that drift a little, same idea as the drive
        # temperatures above.
        self._battery_override: str | None = None
        self._battery_charge_baseline_pct = 97.0
        self._fan_rpm_baseline = 2350
        self._power_override: str | None = None
        self._event_log: deque[EventLogEntry] = deque(maxlen=200)
        self._record_event(SEVERITY_INFO, "Drobo started up (mock driver)")

        # Kept only so a config carrying manual_discovery_ips loads
        # cleanly against either driver. The simulator has no network to
        # search, so there is nothing here for an address to point AT --
        # the setting is read and acted on by Drobo5NDriver, which is
        # where discovery lives.
        self.manual_discovery_ips: list[str] = list(
            drobo_config.get("manual_discovery_ips", []))

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
        return self._droboapps

    def get_drobosync(self) -> dict:
        return self._MOCK_DROBOSYNC

    def get_dimming(self) -> int:
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
        real device coerces."""
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
            matching_apps = [a for a in apps if a["name"] == app_name]
            if not matching_apps:
                raise ValueError(
                    f"{app_name!r} is not an installed DroboApp on this "
                    f"device (installed: {sorted(a['name'] for a in apps)}); "
                    f"refusing to act on a name the device did not report")
            matching_apps[0]["running"] = action == "Start"
            self._record_event(
                SEVERITY_INFO,
                f"DroboApp {app_name!r}: {action} (mock driver)")

    def set_share_access(self, share_name: str, username: str,
                         access_code: str) -> dict:
        """One user's access on one share, on the per-instance share
        config. Same rules as sharewrite: code must be 0, a CONFIRMED
        code, or one already on the device; the share must resolve
        unambiguously."""
        from drobo_nasd.shares import CONFIRMED_ACCESS_CODES
        from drobo_nasd.shareedit import NO_ACCESS_CODE
        with self._lock:
            share_config_node = self._shares["DRINASConfig"]["DRIShareConfig"]
            share_element = share_config_node["Shares"]["Share"]
            shares = (share_element if isinstance(share_element, list)
                      else [share_element])
            allowed_codes = {NO_ACCESS_CODE} | set(CONFIRMED_ACCESS_CODES)
            for share_node in shares:
                user_element = ((share_node.get("ShareUsers") or {})
                                .get("ShareUser") or [])
                for user_node in (user_element
                                  if isinstance(user_element, list)
                                  else [user_element]):
                    stored_code = str(
                        user_node.get("ShareUserAccess", "")).strip()
                    if stored_code:
                        allowed_codes.add(stored_code)
            if access_code not in allowed_codes:
                raise ValueError(
                    f"access code {access_code!r} is neither confirmed nor "
                    f"on this device (allowed: {sorted(allowed_codes)}); "
                    f"refusing to invent a code")
            exact_matches = [
                share_node for share_node in shares
                if (share_node.get("ShareName") or "").strip() == share_name]
            case_folded_matches = [
                share_node for share_node in shares
                if (share_node.get("ShareName") or "").strip().lower()
                == share_name.lower()]
            if len(exact_matches) == 1:
                target_share = exact_matches[0]
            elif len(case_folded_matches) == 1:
                target_share = case_folded_matches[0]
            elif not case_folded_matches:
                raise ValueError(
                    f"no share named {share_name!r} on this device")
            else:
                raise ValueError(
                    f"{len(case_folded_matches)} shares fold to "
                    f"{share_name!r}; ambiguous -- refusing")
            # A share created by create_share holds ShareUsers None --
            # the dict rendering of the empty <ShareUsers/> element a
            # real device stores for a share nobody can reach yet. `or
            # {}` covers that as well as a missing key; setdefault would
            # hand back the None and crash on the very flow the create
            # endpoint tells people to follow.
            share_users_node = target_share.get("ShareUsers") or {}
            user_element = share_users_node.get("ShareUser") or []
            users = (user_element if isinstance(user_element, list)
                     else [user_element])
            matching_users = [
                user_node for user_node in users
                if (user_node.get("ShareUsername") or "").strip().lower()
                == username.lower()]
            if len(matching_users) > 1:
                raise ValueError(
                    f"{len(matching_users)} user rows fold to {username!r} "
                    f"on {share_name!r}; ambiguous -- refusing")
            # Last point at which nothing has changed yet -- where an
            # injected refusal has to happen for it to leave the device
            # untouched.
            self._refuse_if_injected()
            if matching_users:
                matching_users[0]["ShareUserAccess"] = access_code
            else:
                users.append(
                    {"ShareUsername": username,
                     "ShareUserAccess": access_code})
            share_users_node["ShareUser"] = (users[0] if len(users) == 1
                                             else users)
            target_share["ShareUsers"] = share_users_node
            share_config_node["ConfigVersionBasis"] = str(
                int(share_config_node.get("ConfigVersionBasis", "0") or 0)
                + 1)
            self._record_event(
                SEVERITY_INFO,
                f"{username!r} on {share_name!r} set to access "
                f"{access_code} (mock driver)")
            return self._write_ending()

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
        # The SDK's own pattern, borrowed rather than restated: two
        # copies of one rule is how the simulated and hardware paths
        # drift apart.
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
        with self._lock:
            share_config_node = self._shares["DRINASConfig"]["DRIShareConfig"]
            share_element = share_config_node["Shares"]["Share"]
            shares = (share_element if isinstance(share_element, list)
                      else [share_element])
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
            share_config_node["Shares"]["Share"] = (
                shares[0] if len(shares) == 1 else shares)
            share_config_node["ConfigVersionBasis"] = str(
                int(share_config_node.get("ConfigVersionBasis", "0") or 0)
                + 1)
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
            share_config_node = self._shares["DRINASConfig"]["DRIShareConfig"]
            share_element = share_config_node["Shares"]["Share"]
            shares = (share_element if isinstance(share_element, list)
                      else [share_element])
            exact_matches = [
                share_node for share_node in shares
                if (share_node.get("ShareName") or "").strip() == share_name]
            case_folded_matches = [
                share_node for share_node in shares
                if (share_node.get("ShareName") or "").strip().lower()
                == share_name.lower()]
            if len(exact_matches) == 1:
                target_share = exact_matches[0]
            elif len(case_folded_matches) == 1:
                target_share = case_folded_matches[0]
            elif not case_folded_matches:
                existing_names = sorted(
                    (share_node.get("ShareName") or "").strip()
                    for share_node in shares)
                raise ValueError(
                    f"no share named {share_name!r} on this device "
                    f"(have: {existing_names})")
            else:
                raise ValueError(
                    f"{len(case_folded_matches)} shares fold to "
                    f"{share_name!r}; ambiguous -- refusing")
            self._refuse_if_injected()
            shares.remove(target_share)
            share_config_node["Shares"]["Share"] = (
                shares[0] if len(shares) == 1 else shares)
            share_config_node["ConfigVersionBasis"] = str(
                int(share_config_node.get("ConfigVersionBasis", "0") or 0)
                + 1)
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
            shares = (self._shares.get("DRINASConfig", {})
                      .get("DRIShareConfig", {})
                      .get("Shares") or {}).get("Share") or []
            shares = shares if isinstance(shares, list) else [shares]
            target_share = next(
                (share_node for share_node in shares
                 if (share_node.get("ShareName") or "").lower()
                 == (share or "").lower()), None)
            if target_share is None:
                return {"ok": False, "win32_error": self._ERROR_BAD_NET_NAME,
                        "error": (f"The simulated Drobo has "
                                  f"no share called {share!r}.")}

            account = next((name for name in self._MOCK_ACCOUNTS
                            if name.lower() == (username or "").lower()), None)
            if account is None or self._MOCK_ACCOUNTS[account] != password:
                return {"ok": False, "win32_error": self._ERROR_LOGON_FAILURE,
                        "error": "The username or password is wrong."}

            users = ((target_share.get("ShareUsers") or {})
                     .get("ShareUser") or [])
            users = users if isinstance(users, list) else [users]
            access_by_user = {
                (user_node.get("ShareUsername") or "").lower():
                    str(user_node.get("ShareUserAccess", ""))
                for user_node in users}
            access = access_by_user.get(account.lower(),
                                        access_by_user.get("everyone", "0"))
            if access == "0":
                return {"ok": False, "win32_error": self._ERROR_ACCESS_DENIED,
                        "error": (f"{account} has no access to "
                                  f"{target_share.get('ShareName')}.")}
            return {"ok": True, "win32_error": 0, "error": ""}

    def get_config(self, section: str) -> dict:
        """Stand in for config_cmd.get_config
        against a simulated device.
        """
        try:
            return {
                "shares": self._shares,
                "network": self._MOCK_NETWORK,
                "admin": self._MOCK_ADMIN,
            }[section]
        except KeyError:
            raise ValueError(f"unknown section {section!r}") from None

    def _record_event(self, severity: str, message: str) -> None:
        self._event_log.append(EventLogEntry(
            ts=time.time(), severity=severity, message=message))

    # -- fault injection -----------------------------------------------

    def fail_bay(self, bay: int) -> None:
        with self._lock:
            self._bay_state_overrides[bay] = STATE_FAILED

    def warn_bay(self, bay: int) -> None:
        with self._lock:
            self._bay_state_overrides[bay] = STATE_WARNING

    def pull_bay(self, bay: int) -> None:
        with self._lock:
            self._bay_state_overrides[bay] = "pulled"

    def fail_battery(self, state: str = STATE_FAILED) -> None:
        """Simulate cache-battery trouble.
        state is STATE_WARNING or STATE_FAILED.
        """
        with self._lock:
            self._battery_override = state
            self._record_event(
                SEVERITY_WARNING if state == STATE_WARNING else SEVERITY_INFO,
                f"Cache battery reports {state} (mock driver)",
            )

    def fail_power(self, state: str = STATE_FAILED) -> None:
        with self._lock:
            self._power_override = state
            self._record_event(SEVERITY_WARNING,
                               f"PSU reports {state} (mock driver)")

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
        with self._lock:
            self._bay_state_overrides.clear()
            self._battery_override = None
            self._power_override = None
            self._write_fault = WRITE_FAULT_NONE

    # -- driver interface ----------------------------------------------

    def read_snapshot(self) -> Snapshot:
        with self._lock:
            elapsed_seconds = time.time() - self._started_at
            # Creep usage upward slowly so the capacity graph has
            # something to draw, and wobble temperatures on a slow sine
            # plus noise.
            self._used_bytes += int(gigabytes_to_bytes(0.002))
            drives: list[DriveSlot] = []
            raw_bytes = 0
            for bay_spec in self._bay_specs:
                bay = bay_spec["bay"]
                override = self._bay_state_overrides.get(bay)
                if override == "pulled" or not bay_spec["present"]:
                    drives.append(DriveSlot(
                        bay=bay, present=False, state=STATE_EMPTY))
                    continue
                raw_bytes += bay_spec["capacity_bytes"]
                drive_temperature_c = (
                    bay_spec["baseline_temperature_c"]
                    + 3.0 * math.sin(elapsed_seconds / 240.0 + bay)
                    + random.uniform(-0.4, 0.4)
                )
                state = override or STATE_OK
                note = ""
                if state == STATE_FAILED:
                    note = "simulated failure (mock driver)"
                    drive_temperature_c = None
                elif state == STATE_WARNING:
                    note = "simulated SMART warning (mock driver)"
                drives.append(
                    DriveSlot(
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
                )

            healthy_raw_bytes = sum(
                d.capacity_bytes for d in drives
                if d.present and d.state != STATE_FAILED
            )
            # Single-disk redundancy: usable is roughly raw minus the
            # largest drive.
            largest_drive_bytes = max(
                (d.capacity_bytes for d in drives if d.present), default=0)
            usable_bytes = max(healthy_raw_bytes - largest_drive_bytes, 0)
            used_bytes = min(self._used_bytes, usable_bytes)
            capacity = Capacity(
                raw_bytes=raw_bytes,
                usable_bytes=usable_bytes,
                used_bytes=used_bytes,
                free_bytes=max(usable_bytes - used_bytes, 0),
                protection_bytes=largest_drive_bytes,
            )

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
                # Two volumes, where the real 5N reports one.
                # Deliberate: a single-volume pack is the case that
                # hides layout bugs, and the whole point of the
                # simulator is to show the states real hardware won't.
                # Field values mirror what the device actually sends,
                # including the undocumented partition_type/format.
                volumes=[
                    Volume(lun=0, unique_id="1000", name="",
                           max_size_bytes=70368744177664,
                           used_bytes=used_bytes // 2,
                           partition_count=2, partition_type=3,
                           partition_format=64, share_state=0),
                    Volume(lun=1, unique_id="1001", name="Media",
                           max_size_bytes=70368744177664,
                           used_bytes=used_bytes // 2,
                           partition_count=1, partition_type=3,
                           partition_format=64, share_state=0),
                ],
                max_volumes=16,
                battery=battery,
                fan=fan,
                power=power,
                performance=performance,
                event_log=list(self._event_log),
            )

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
            charge = max(0.0, 35.0 + 2.0 * math.sin(elapsed_seconds / 300.0))
            return BatteryInfo(
                state=STATE_WARNING, charge_pct=round(charge, 1),
                note="cache battery is weak (mock driver) -- "
                     "consider a replacement",
            )
        charge = (self._battery_charge_baseline_pct
                  + 1.5 * math.sin(elapsed_seconds / 600.0)
                  + random.uniform(-0.3, 0.3))
        charge = max(0.0, min(100.0, charge))
        return BatteryInfo(
            state=STATE_OK, charge_pct=round(charge, 1), note="")

    def _read_fan(self, elapsed_seconds: float) -> FanInfo:
        """eCmdGetFanInfo. RPM drifts a little; stalls if the fan bay
        was also given a state via the drive overrides (not modelled --
        fans just idle around their baseline for now)."""
        rpm = (self._fan_rpm_baseline
               + 60.0 * math.sin(elapsed_seconds / 180.0)
               + random.uniform(-15.0, 15.0))
        rpm = max(0, int(rpm))
        state = STATE_OK if rpm > 800 else STATE_WARNING
        return FanInfo(rpm=rpm, state=state)

    def _read_power(self) -> PowerInfo:
        """eCmdGetPowerInfo."""
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
        baseline_read_mb_per_s = 45.0 + 20.0 * math.sin(elapsed_seconds / 90.0)
        baseline_write_mb_per_s = (
            18.0 + 8.0 * math.sin(elapsed_seconds / 130.0 + 1.0))
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
        super().__init__(drobo_config)
        self.host = (drobo_config.get("host") or "").strip()
        self.port = int(drobo_config.get("port", esatm.DEFAULT_PORT))
        self.timeout = float(drobo_config.get("timeout", 8.0))
        self.hostname = (drobo_config.get("hostname") or "").strip()
        self.expected_esa_id = (
            drobo_config.get("expected_esa_id") or "").strip()
        self.try_sweep = bool(drobo_config.get("discovery_sweep", False))

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
        self._resolved_host = host
        self._resolved_port = port
        if not self._known_esa_id:
            self._known_esa_id = esa_id

    def _discover(self) -> discovery.VerificationResult:
        last_known_host = self._resolved_host or (
            self.host if not self._uses_auto_discovery else "")
        try:
            verified = discovery.resolve(
                expected_esa_id=self._known_esa_id,
                hostname=self.hostname,
                last_known_ip=last_known_host,
                port=self.port,
                try_sweep=self.try_sweep,
                timeout=self.timeout,
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
            # The advice leads with the two checks that settle most
            # cases. This text used to be read mainly in the console the
            # agent left behind when it exited, under a footer from
            # Run-Agent.bat saying the same thing; the agent no longer
            # exits here, so the text is now what the "cannot reach"
            # alert says, and it has to carry the first thing to check
            # itself. The config.json route is for a network that blocks
            # mDNS, which is the rarer case.
            raise DroboNotFoundError(
                "Could not find the Drobo automatically (tried mDNS"
                + (f", {len(self.manual_discovery_ips)} "
                   f"manually-added address(es)"
                   if self.manual_discovery_ips else "")
                + (f", hostname {self.hostname!r}" if self.hostname else "")
                + (f", last-known IP {last_known_host!r}"
                   if last_known_host else "")
                + (", subnet sweep" if self.try_sweep else "")
                + f"). {exc}\n"
                "Check the Drobo is switched on and this PC is on the same "
                "network. If both are true and this network blocks mDNS, set "
                '"drobo": {"driver": "drobo5n", "host": "<drobo-ip>"} in '
                "config.json, or find the IP with:  "
                "py tools/drobo_probe.py mdns"
            ) from exc
        self._remember_working_address(
            verified.identity["esa_id"], verified.candidate.host,
            verified.candidate.port)
        if verified.first_run:
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
        return verified

    def connect(self) -> None:
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
        snapshot = esatm.read_snapshot(self.host, self.port, self.timeout)
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
        host, port = self._resolved_host, self._resolved_port
        if not host:
            # connect() was never called (or auto mode hasn't resolved
            # yet): discover now rather than failing with "no host
            # configured".
            verified = self._discover()
            host, port = verified.candidate.host, verified.candidate.port

        try:
            snapshot = esatm.read_snapshot(host, port, self.timeout)
        except (OSError, esatm.FrameError):
            # The address that used to work doesn't anymore -- almost
            # always a DHCP lease change. Re-run discovery instead of
            # dying, so a Drobo that moved to a new IP self-heals
            # without a restart, then retry once against whatever
            # discovery finds. If discovery also comes up empty, that
            # exception (already framed clearly) is what the caller sees
            # -- this never masks a real outage as success.
            verified = self._discover()
            host, port = verified.candidate.host, verified.candidate.port
            snapshot = esatm.read_snapshot(host, port, self.timeout)

        self._remember_working_address(snapshot.device.serial, host, port)
        snapshot.device.ip = host
        self._read_sysinfo(host)
        snapshot.device.temperature_c = self._temperature_c
        snapshot.device.temperature_at = self._temperature_read_at
        if (self._uptime_seconds is not None
                and self._uptime_read_at is not None):
            # Uptime arrives with the temperature, then keeps counting
            # on its own -- no need to ask the device again just to
            # watch a clock.
            seconds_since_read = time.time() - self._uptime_read_at
            snapshot.device.uptime_seconds = int(
                self._uptime_seconds + seconds_since_read)
        if self._performance is not None:
            # measured=True even when every counter is zero: an idle
            # array really does report zeros, and that is a reading, not
            # a gap. measured_at is when they arrived, which after a
            # failed fetch is earlier than this poll -- the UIs show
            # that age.
            snapshot.performance = PerformanceInfo(
                read_mb_per_s=self._performance.get("read_mb_per_s") or 0,
                write_mb_per_s=self._performance.get("write_mb_per_s") or 0,
                iops=self._performance.get("iops") or 0,
                tier_iops=self._performance.get("tier_iops") or 0,
                measured=True,
                measured_at=self._performance_read_at,
            )
        self._read_event_log(host)
        snapshot.event_log = list(self._event_log)
        return snapshot

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
                host, self._known_esa_id, timeout=min(self.timeout, 6.0))
        except (OSError, config_cmd.ConfigError, sysinfo.SysInfoError):
            return  # unavailable right now; keep whatever we already had
        if sysinfo_reply.get("temperature_c") is not None:
            self._temperature_c = sysinfo_reply["temperature_c"]
            self._temperature_read_at = now
        if sysinfo_reply.get("uptime_seconds") is not None:
            self._uptime_seconds = sysinfo_reply["uptime_seconds"]
            self._uptime_read_at = now

        performance = sysinfo_reply.get("performance")
        if performance and any(v is not None for v in performance.values()):
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
                host, self._known_esa_id, timeout=min(self.timeout, 6.0))
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
    driver_name = (drobo_config.get("driver") or "mock").lower()
    if driver_name not in DRIVERS:
        raise ValueError(
            f"unknown driver {driver_name!r}; choose from {sorted(DRIVERS)}")
    return DRIVERS[driver_name](drobo_config)
