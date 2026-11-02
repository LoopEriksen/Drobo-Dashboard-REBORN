"""
The agent's HTTP API.

Plain JSON over loopback (or the LAN, if you turn that on). The Windows
app and the iOS app both talk to this and neither knows the Drobo
protocol exists.

Every endpoint except /api/ping requires the access token from
config.json, sent as the  X-Agent-Token  header. /api/events also
accepts ?token= because browser EventSource can't set headers.

    GET  /                      the web dashboard (no auth - it holds no
                                 data)
    GET  /api/ping              liveness + agent version (no auth)
    GET  /api/status            everything: device, capacity, drives,
                                 state
    GET  /api/device            identity and firmware only
    GET  /api/drives            per-bay detail
    GET  /api/volumes           the volumes the pack is split into
    GET  /api/capacity          capacity numbers only
    GET  /api/battery           cache-battery health
    GET  /api/health            pack/device-level health values (health
                                 telemetry) -- error counts,
                                 relayout/double-degraded, raw status
                                 codes
    GET  /api/droboconfig[?section=]  the Drobo's own settings, read on
                                 demand rather than on every poll:
                                 network (the default), shares, admin or
                                 general
    GET  /api/discover[?host=]  Drobos visible on this network, each
                                 already identified, so a front-end can
                                 list them and let you pick one instead
                                 of typing an address. ?host= adds one
                                 typed address to the search, for
                                 networks that filter mDNS entirely
    GET  /api/netcheck[?host=]  whether this PC is even on the same
                                 network as a given Drobo -- the usual
                                 reason one "vanishes"
    GET  /api/shares            the file shares, with UNC paths a
                                 front-end can open or map, plus why
                                 Windows may refuse them
    GET  /api/droboapps         software installed ON the Drobo (name,
                                 version, running state, whether it has
                                 a web interface)
    GET  /api/leds              LED brightness as the device reports it,
                                 plus whether the unit is in shop-demo
                                 mode (in which case its capacity
                                 numbers are fabricated)
    GET  /api/spindown          drive spin-down as the device stores it
                                 -- whether it is enabled, and the
                                 delay, from the same General block the
                                 spin-down write edits
    GET  /api/update            whether a newer version exists. DORMANT
                                 unless config.json's "updates" block is
                                 switched on -- it then only ever TELLS
                                 you; downloading and installing stay a
                                 human decision
    GET  /api/emailalerts       whether email alerts are configured --
                                 never the credentials, only host and
                                 recipients
    POST /api/emailalerts/test  send a test email through that
                                 configuration
    GET  /api/backup/status     progress of a running or finished backup
    POST /api/backup/plan       what a backup WOULD do. Copies nothing
    POST /api/backup/start      actually run it. Needs "confirm": true
    GET  /api/drobosync         Drobo-to-Drobo replication state. Built
                                 and dormant -- it needs a second Drobo,
                                 so it reports "not configured" rather
                                 than failing
    GET  /api/eventlog          the Drobo's own device event log (not
                                 the SSE stream)
    GET  /api/performance       throughput stats (read/write bytes per
                                 second)
    GET  /api/alerts[?all=1]    active alerts (or the full history)
    GET  /api/history[?limit=N]  trimmed series for graphing
    GET  /api/events            server-sent events: live snapshots and
                                 alerts
    GET  /api/photos/stats      how much has been backed up
    GET  /api/photos/session?id=<sha256>  how much of a chunked upload
                                 the agent already holds, so it can be
                                 resumed
    GET  /api/pair              url + token for the phone
    GET  /api/pair.svg          the same, as a scannable QR code
    POST /api/photos/have       {"hashes":[...]} -> {"missing":[...]}
    POST /api/photos/rescan     rebuild the dedupe index from the files
                                 actually on the Drobo -- run after
                                 deleting any by hand
    POST /api/photos            raw file body; X-Filename, X-Taken-At
                                 headers
    POST /api/photos/begin      start a chunked, resumable upload (the
                                 path videos take): {"filename", "size",
                                 "sha256", "taken_at"}
    POST /api/photos/chunk      raw body: the next chunk; X-Upload-Id
                                 and X-Offset headers
    POST /api/photos/finish     check the whole file against its
                                 SHA-256, then file it; X-Upload-Id
                                 header
    POST /api/identify          flash the Drobo's lights, so you can
                                 tell which box is which
    POST /api/config/brightness  {"value": N} set LED brightness
    POST /api/config/spindown   {"delay": N} or {"enabled": false} --
                                 the drive spin-down setting
    POST /api/config/share-access  {"share","user","access"} one user's
                                 access on one share
    POST /api/shares/create     {"name": N} create a share. It starts
                                 with no access for anyone until
                                 share-access grants some
    POST /api/shares/delete     {"name": N, "confirm_name": N} delete a
                                 share AND everything stored in it.
                                 confirm_name must repeat the name
                                 exactly (case counts) or nothing
                                 touches the device
    POST /api/droboapps/action  {"name","action"} start or stop an
                                 installed DroboApp. These seven are the
                                 only endpoints that change anything on
                                 the device, and every 200 from the
                                 config writes means the change was READ
                                 BACK, not merely accepted
    POST /api/mock/<action>/<bay>  mock driver only: fail|warn|pull, and
                                 reset
    POST /api/mock/sign-in      {"share": S, "username": U,
                                 "password": P} mock driver only: would
                                 this login open this share on the
                                 simulated Drobo? Its account is
                                 admin / admin. Answers {"ok",
                                 "win32_error", "error"} and never
                                 echoes the password.
    POST /api/mock/battery/<warning|failed>  mock driver only:
                                 cache-battery fault injection
    POST /api/mock/power/<warning|failed>  mock driver only: PSU fault
                                 injection
    POST /api/mock/write/<none|hangs_up|refused|unconfirmable>  mock
                                 driver only: how every simulated
                                 settings or share write ends from now
                                 on, until /api/mock/reset
"""

from __future__ import annotations

import functools
import json
import queue
import re
import socket
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree

from . import backup, drivers, notify, qr, updates, webui
from drobo_nasd import shares as nasd_shares
from drobo_nasd import (config_cmd, discovery, droboapps, drobosync, esatm,
                        identify as nasd_identify, netcheck, sharewrite,
                        sysinfo)
from drobo_nasd.shareedit import ShareEditError
from .drivers import MockDriver, SimulatedWriteUnconfirmedError
from .photos import PhotoStoreError

#: One backup at a time, for the whole agent. Two robocopy processes
#: writing the same destination would interleave and neither result
#: would be trustworthy, so the runner is a module-level singleton
#: rather than per-request.
_BACKUP_RUNNER = backup.BackupRunner()

VERSION = "1.2.0"
MAX_JSON_BODY_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_CHUNK_BYTES = 64 * 1024 * 1024

#: /api/history's answer when ?limit= is absent or not a number (two
#: hours at the default ten-second poll, and Monitor.history_points' own
#: default), and the most one request may ask for: a bound on one
#: answer's size, which only matters when config.json's history_length
#: is raised above it; the default 8640 points fit inside it.
DEFAULT_HISTORY_POINTS = 720
MAX_HISTORY_POINTS = 20000

#: How long an update check stays cached when config.json's "updates"
#: block names no check_seconds: one day.
DEFAULT_UPDATE_CHECK_SECONDS = 86400

#: Seconds of silence on /api/events before a keep-alive comment frame
#: goes out (see _stream_events).
SSE_KEEPALIVE_SECONDS = 15

#: Read allowance over PhotoStore's single-upload limit for POST
#: /api/photos. A file just over the limit is still read, so it is
#: refused by PhotoStore.ingest with its own message in megabytes
#: rather than by the body reader's bare byte count.
SINGLE_UPLOAD_SLACK_BYTES = 1024

#: Where _pair_url "connects" a UDP socket to learn which local address
#: routes off this machine. Connecting a UDP socket sends nothing; it
#: only makes the OS pick the outgoing interface.
_ROUTE_PROBE_ADDRESS = ("8.8.8.8", 53)

#: The host /api/shares builds UNC paths with under the simulator: the
#: address the simulator's canned network config gives its Drobo (see
#: drivers.py), so the simulated paths look like a real device's.
_SIMULATED_SHARES_HOST = "10.0.0.5"

#: The live 5N reports its LED brightness as 59. The firmware command
#: table names eCmdGetDimming and gives its id, and documents NOTHING
#: about the scale -- so whether 59 means 59%, 59 of 255, or Drobo's own
#: step 59 is unknown. Shown with the number rather than quietly
#: rendered as a percentage, because inventing a unit is how a
#: measurement becomes a lie.
DIMMING_SCALE_NOTE = (
    "The Drobo reports this as a bare number. Its firmware never says what "
    "the scale is -- percent, 0-255, or its own steps -- so it is shown "
    "exactly as reported rather than converted into a unit we would be "
    "guessing at.")

#: Same honesty problem as the dimming scale, one field over. The
#: 2026-08-06 capture showed SpindownDelay as 0 (disabled) and 14 (the
#: unit's default); minutes and a dropdown index are both consistent
#: with that, so the number ships as stored rather than dressed in a
#: unit.
SPINDOWN_UNIT_NOTE = (
    "The Drobo stores this delay as a bare number (14 on a stock unit). Its "
    "firmware never says what the unit is -- minutes and a menu position are "
    "both consistent with what has been captured -- so it is shown exactly "
    "as stored rather than converted into a unit we would be guessing at.")

# --- how a configuration write can end --------------------------------
# Four outcomes, named once here and reported in every write answer as
# "outcome", so the agent, both UIs and the tests share one vocabulary
# instead of each reading English prose to work out what happened.
#
# The middle two exist because of a measured hardware fact (2026-08-25):
# a share create was sent to the real 5N, the device closed the socket
# without ever answering, every layer above reported the write as failed
# -- and the share had been created. So a write that applied must never
# render as "failed", and a write nobody can settle must never render as
# "fine".

#: The device acknowledged the write AND it reads back changed.
WRITE_VERIFIED = "verified"

#: The device never acknowledged the write, but it reads back changed
#: anyway. A SUCCESS: on this project the read-back is the arbiter of
#: truth, not the acknowledgement. Kept distinct from WRITE_VERIFIED so
#: the answer can say so out loud rather than implying a conversation
#: that did not happen.
WRITE_UNACKNOWLEDGED = "verified_unacknowledged"

#: Nothing was applied -- the device refused the write, could not be
#: reached, or took it and reads back unchanged.
WRITE_FAILED = "failed"

#: Neither confirmed nor refuted: the write went out and the read-back
#: that would settle it could not be completed. The change MAY be on the
#: device.
WRITE_UNKNOWN = "unknown"

#: Added to the success message of a WRITE_UNACKNOWLEDGED write.
UNACKNOWLEDGED_WRITE_NOTE = (
    "The Drobo did not answer to say it accepted this -- it closed the "
    "connection instead -- but reading the setting back afterwards shows the "
    "change really is there.")

#: Added to the error message of a WRITE_UNKNOWN write. Appended always,
#: not only when the SDK's own wording lacks it: saying it twice costs a
#: sentence, leaving it out costs somebody a blind retry of a write that
#: already applied.
UNKNOWN_WRITE_GUIDANCE = (
    "Nothing here can say whether the change was applied, and it may well "
    "have been. Re-read what the Drobo now holds before sending this again.")

#: Exception types meaning "the write went out and its outcome could not
#: be established" -- WRITE_UNKNOWN, never a failure. Every one of these
#: MUST be caught before the refusal branches in
#: _attempt_config_write (which _config_write calls), and
#: ConfigWriteOutcomeUnknownError especially: it subclasses
#: ConfigWriteError purely so old callers keep catching it, so an
#: `except ConfigWriteError` reached first would report an unsettled
#: write as a plain failure -- the one answer this whole path exists to
#: prevent.
#:
#: Listed by name rather than discovered, so that if the SDK ever
#: renames one this file fails loudly at import instead of quietly
#: mis-filing the write as failed. A new SDK type for this condition has
#: to be added here.
_UNCONFIRMED_WRITE_ERRORS = (
    # brightness and spin-down writes
    config_cmd.ConfigWriteOutcomeUnknownError,
    # share access, create and delete
    sharewrite.ShareWriteOutcomeUnknownError,
    # the mock driver's injected ending
    SimulatedWriteUnconfirmedError,
)


def _write_outcome(result) -> tuple[str, bool]:
    """
    Which of the four outcomes an SDK write's RESULT describes, and
    whether the device acknowledged it.

    config_cmd.decided_by_readback() is the SDK's own reader for its own
    shape: true exactly when the device never answered and the read-back
    settled the write instead. The fallbacks after it exist for shapes
    it cannot judge -- a no-op write that sent nothing (acknowledged
    None), and any result whose flag is missing entirely.

    Anything left unrecognised is WRITE_UNKNOWN, never a success,
    because the two mistakes do not cost the same: "unknown" costs the
    user one re-read, while a wrong "verified" tells them a change is on
    their Drobo when it may not be.
    """
    if not isinstance(result, dict):
        return WRITE_UNKNOWN, False
    if config_cmd.decided_by_readback(result):
        acknowledged = False
    else:
        acknowledged = result.get("acknowledged")
        if not isinstance(acknowledged, bool):
            # A result_code of None is how a device that answered
            # nothing shows up; no result_code at all means nothing was
            # sent for it to answer, which is a success in its own right
            # (see the SDK's no-op write).
            acknowledged = (result["result_code"] is not None
                            if "result_code" in result else True)
    verified = result.get("verified")
    if verified is True:
        return ((WRITE_VERIFIED if acknowledged else WRITE_UNACKNOWLEDGED),
                acknowledged)
    if verified is False:
        return WRITE_FAILED, acknowledged
    return WRITE_UNKNOWN, acknowledged


def _identify_message(interval, simulated: bool = False) -> str:
    """
    What to tell the user after an Identify,
    given what was actually sent.

    Three outcomes, and one honesty problem. Identify is a MODE, not a
    pulse: Drobo Dashboard's own warning says the lights blink
    continuously for fifteen minutes and the same button stops them.
    "Sent, the lights should be flashing" -- which is what this said
    until 2026-08-06 -- describes a brief flash, so somebody who wanted
    a brief flash gets fifteen minutes of blinking and no idea how to
    stop it.

    The honesty problem is the unit. Dashboard's warning says fifteen
    minutes, and the one captured press (2026-07-25) sent 900 --
    seconds, most likely, but 900 has never been sent from here and no
    light has been seen to move on this unit. (This said Dashboard
    "sends fifteen of something" until 2026-09-26; it does not.) So the
    duration is attributed to the original product rather than promised
    here, and the way to stop it is offered regardless -- which is
    useful whichever reading turns out to be right.

    The reply names the value sent and the value the original Dashboard
    sent, because they differ. Until 2026-10-29 it said the original
    Dashboard "used this value", which was untrue for the default: the
    one captured press (2026-07-25) carried IdentifyInterval 900, and
    DEFAULT_INTERVAL is 15, this project's own guess, kept until 900 has
    been sent while someone watches the unit (see identify.py).

    Still unobserved, recorded 2026-09-26 and left for a separate
    decision: the reply says the lights "should be blinking now", which
    nobody has seen happen on this unit.
    """
    device_label = "Simulated Drobo" if simulated else "The Drobo"
    if interval == nasd_identify.STOP_INTERVAL:
        return f"Stop sent. {device_label}'s lights should go back to normal."
    if interval is None:
        return ("Sent with no interval -- the control case. If the lights "
                "stay still now but blink when an interval is sent, that "
                "settles what the parameter does.")
    return (f"Sent with interval {interval}. {device_label}'s lights "
            f"should be blinking now. The original Dashboard sent 900, "
            f"and its own warning says the lights blink for 15 minutes, "
            f"so use Stop when you have found the box.")


def _app_action_message(name: str, action: str, read_back: dict | None,
                        simulated: bool = False) -> str:
    """
    What to tell the user after a DroboApp Start/Stop,
    given the re-read.

    The device ACCEPTS the action before the service has finished
    changing state -- the SDK deliberately skips read-back verification
    for exactly that reason (see droboapps.droboapps_action). So the
    re-read is reported as what the device says RIGHT NOW, and a state
    that has not flipped yet is described as still settling, never as a
    failure: telling somebody their Stop failed when the service is
    halfway down invites a retry of a write that already took.
    """
    device_label = "The simulated Drobo" if simulated else "The Drobo"
    if read_back is None:
        return (f"{action} was accepted, but {name!r}'s state could not be "
                f"re-read to confirm it -- refresh the app list in a moment "
                f"to see where it landed.")
    state = "running" if read_back.get("running") else "stopped"
    if read_back.get("running") == (action == "Start"):
        return f"{action} sent. {device_label} now reports {name!r} {state}."
    return (f"{action} sent and accepted, but the device still reports "
            f"{name!r} {state}. Apps take a moment to change state -- "
            f"refresh the list shortly.")


class _ApiRequestHandler(BaseHTTPRequestHandler):
    """
    One HTTP request to the agent: authenticate it, route it, answer it.

    http.server builds a handler for every connection, and with
    HTTP/1.1 keep-alive one handler answers each request on that
    connection in turn. So whatever has to outlive a connection lives
    on the class, not on the instance: the monitor, photo store and
    token that build_http_server() injects, and the caches and locks
    that keep repeated requests from turning into repeated connections
    to a device that tires easily. Per-request state may live on the
    instance only if do_GET/do_POST reset it before anything reads it,
    as _split_request_url does for `query_params`; anything set on only
    some routes would otherwise leak into the next request on the same
    connection.
    """

    server_version = f"DroboAgent/{VERSION}"
    protocol_version = "HTTP/1.1"

    # /api/droboconfig opens a second connection to the device's command
    # port. Bounded so a slow or sulking Drobo can't stall an API worker
    # thread.
    COMMAND_TIMEOUT_SECONDS = 8.0

    # How long a /api/shares answer stays good for.
    #
    # This is a cache for the device's benefit, not ours. Each call
    # costs two command-port connections, and on 2026-07-26 that port
    # was measurably worn out by heavy use -- eCmdGetSysInfo stopped
    # answering entirely after about ninety connections in an hour,
    # while the array stayed perfectly healthy (see
    # docs/command-surface.md). Share names and network settings change
    # roughly never, so a refreshing dashboard has no business asking
    # again every few seconds.
    SHARES_CACHE_SECONDS = 60.0
    # (time.time() it was cached at, payload), or None.
    _shares_cache: tuple[float, dict] | None = None
    # Socket timeout for one request, in seconds (http.server's own
    # attribute, so it keeps its name). Without it, a client that sends
    # a Content-Length and then stalls holds a handler thread for ever.
    # Per recv/send call, not per request, so a slow phone upload is
    # unaffected.
    timeout = 60

    _shares_lock = threading.Lock()

    # Network discovery costs a few seconds of listening, so the front
    # page doesn't get to re-run it on every render.
    DISCOVERY_LISTEN_SECONDS = 3.0
    DISCOVERY_CACHE_SECONDS = 30.0
    # (time.time() it was cached at, payload), or None.
    _discover_cache: tuple[float, dict] | None = None
    _discover_lock = threading.Lock()

    # The update check, cached for its configured interval. A dashboard
    # left open in a tab must not turn a daily check into a per-second
    # one. (time.time() it was cached at, payload), or None.
    _update_cache: tuple[float, dict] | None = None
    _update_lock = threading.Lock()

    # injected by build_http_server()
    monitor = None
    photo_store = None
    token = ""

    # -- plumbing ------------------------------------------------------

    #: A token supplied in the URL, in any of the forms a request line
    #: can carry one. Scrubbed before anything is printed: see
    #: log_message.
    _TOKEN_IN_URL = re.compile(r"([?&]token=)[^&\s\"]*", re.I)

    def log_message(self, message_format: str, *args) -> None:
        """
        A quieter access log than http.server's, with any token in the
        URL scrubbed out and the event stream left out entirely.
        """
        # getattr, not self.path: http.server logs a timeout BEFORE it
        # has read a request line, and at that point the handler has no
        # path attribute at all. A client that opens a spare keep-alive
        # connection and never sends on it reaches the 60-second timeout
        # above, and this line used to raise AttributeError there: a
        # traceback in the operator's console for a connection nobody
        # used. Seen against the simulator, 2026-09-14.
        if (getattr(self, "path", "") or "").startswith("/api/events"):
            return
        # The access log prints the whole request line, query string
        # included, and a token still reaches us in the URL on the two
        # routes that need it that way -- /api/events, and `/?token=`
        # from the QR code that /api/pair.svg hands out. So the agent
        # was printing its own access token into the console window that
        # Run-Agent.bat tells testers to copy into a public issue when
        # the agent stops. Found by a privacy audit, 2026-09-08.
        #
        # Scrubbed rather than refused, because a browser cannot set a
        # header on an EventSource and the phone pairing flow depends on
        # the URL form. Everything else takes the header only (see
        # _authorised); the token still works here, it just stops being
        # written down.
        line = self._TOKEN_IN_URL.sub(r"\1<redacted>", message_format % args)
        print(f"[api] {self.address_string()} {line}", flush=True)

    def _authorised(self, query_params: dict, *,
                    allow_query: bool = False) -> bool:
        """
        Whether the request carries the agent's token: in the
        X-Agent-Token header, or in ?token= only where the caller opts
        in -- which today is /api/events alone, because an EventSource
        cannot set a header.

        Header-only is what stops a token that has already escaped into
        a URL (the QR code from /api/pair.svg, a pairing link forwarded
        to a phone, a screenshot in a bug report) from being a
        one-request CSRF. A page on any other site can make your browser
        POST to 127.0.0.1 with a plain <form>, and a form cannot set
        X-Agent-Token; if it could put the token in the query string
        instead, that single request would be enough to fire
        /api/backup/start with mirror on, or /api/shares/delete. A
        cross-site fetch() *can* set the header, but then it is a
        preflighted request -- and this handler answers no OPTIONS and
        sends no Access-Control-* headers, so the preflight fails and
        the real request is never sent. Header-only is therefore the
        whole fix.
        """
        supplied_token = self.headers.get("X-Agent-Token", "")
        if not supplied_token and allow_query:
            supplied_token = (query_params.get("token") or [""])[0]
        if not supplied_token:
            return False
        # Compare the utf-8 bytes, not the strings. compare_digest
        # refuses two str arguments unless both are pure ASCII -- it
        # raises TypeError -- and the supplied half is whatever the
        # caller sent: a header byte >= 0x80 reaches us as latin-1 text,
        # and ?token= can carry any %-escape. So the gate used to raise
        # inside itself and drop a traceback in the operator's console
        # instead of answering a clean 401, and a non-ASCII token typed
        # into config.json by hand broke every request the same way.
        # Bytes keep the same constant-time compare and the same answer
        # for the ASCII tokens config.py generates.
        try:
            supplied_token_bytes = supplied_token.encode("utf-8")
        except UnicodeEncodeError:  # lone surrogate; cannot be the token
            return False
        return secrets.compare_digest(supplied_token_bytes,
                                      self.token.encode("utf-8"))

    def _send_unauthorised(self, query_params: dict) -> None:
        """
        The 401, saying what changed when the token came in the URL.

        ?token= used to be accepted everywhere. Somebody's bookmark,
        curl one-liner or phone client was very likely built from the
        pairing URL, and a bare "invalid token" would send them hunting
        for a typo in a token that is perfectly good.
        """
        message = "missing or invalid X-Agent-Token"
        if query_params.get("token"):
            message += ("; ?token= is accepted on /api/events only -- "
                        "send the token as the X-Agent-Token header")
        return self._send_json({"error": message}, 401)

    def _send_json(self, payload, status: int = 200) -> None:
        """
        Answer with `payload` as indented JSON. Marked no-store because
        these answers are live readings: a browser serving a stored copy
        back would show a state the Drobo is no longer in.
        """
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200,
                   content_type: str = "text/plain") -> None:
        """Answer with `text`, UTF-8 encoded: the page shell, the QR."""
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_not_found(self, path: str) -> None:
        """The 404 for a route neither table lists, naming the path."""
        return self._send_json({"error": "not found", "path": path}, 404)

    def _send_bad_json(self, exc: Exception) -> None:
        """The 400 for a request body that is not JSON (or too big)."""
        return self._send_json({"error": f"bad JSON: {exc}"}, 400)

    def _send_command_port_unreachable(self, exc: OSError) -> None:
        """
        The 502 for a command port that could not be reached at all, as
        opposed to one that answered with a refusal.
        """
        return self._send_json(
            {"error": f"could not reach the command port: {exc}"}, 502)

    def _pair_url(self) -> str:
        """
        The address the phone should use. Prefer whatever host the
        browser reached us on, but if that's loopback the phone can't
        use it -- fall back to this machine's LAN address.
        """
        host = (self.headers.get("Host", "") or "").split(":")[0]
        if host in ("", "localhost", "127.0.0.1", "::1", "[::1]"):
            udp_route_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                udp_route_probe.connect(_ROUTE_PROBE_ADDRESS)
                host = udp_route_probe.getsockname()[0]
            except OSError:
                host = "127.0.0.1"
            finally:
                udp_route_probe.close()
        return f"http://{host}:{self.server.server_address[1]}"

    def _read_body(self, max_body_bytes: int) -> bytes:
        """
        The request body, refusing one larger than `max_body_bytes`.

        The limit is checked against Content-Length BEFORE reading, so a
        client cannot make the agent buffer more than it allows. A
        missing, zero or unparseable Content-Length reads as an empty
        body: there is nothing trustworthy to read by, and every caller
        already copes with one: the JSON routes read it as {}, and the
        upload routes refuse it as an empty file or chunk.
        """
        try:
            content_length_bytes = int(
                self.headers.get("Content-Length", "0"))
        except ValueError:
            return b""
        if content_length_bytes <= 0:
            return b""
        if content_length_bytes > max_body_bytes:
            raise ValueError(
                f"body of {content_length_bytes} bytes exceeds the "
                f"{max_body_bytes} byte limit")
        return self.rfile.read(content_length_bytes)

    def _read_json_body(self) -> dict:
        """
        The request body parsed as a JSON object, an empty body counting
        as {}. Raises ValueError (bad JSON, a body over
        MAX_JSON_BODY_BYTES, or JSON that is not an object), which every
        caller answers with _send_bad_json.

        Every route reads its fields with .get, so a body that is valid
        JSON but not an object ([], 1, null) used to raise
        AttributeError there, and the route dropped the connection with
        no answer (until 2026-11-02).
        """
        body = json.loads(self._read_body(MAX_JSON_BODY_BYTES) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("the request body must be a JSON object")
        return body

    def _drain_request_body(self) -> None:
        """
        Read and drop the body of a request about to be refused.

        Without this, a POST that arrives with a body gets its 401 while
        those bytes are still unread on a keep-alive connection; the
        server then closes and Windows aborts the socket, so the client
        sees a connection error instead of a clean 401 with a reason.
        Found by the config-write tests; it affected every
        unauthenticated POST.
        """
        try:
            self._read_body(MAX_JSON_BODY_BYTES)
        except Exception:  # noqa: BLE001 - the answer is 401 regardless
            pass

    def _split_request_url(self) -> str:
        """
        The request's path, without a trailing slash, after storing its
        query string in self.query_params for the route to read.
        """
        parsed_url = urllib.parse.urlparse(self.path)
        self.query_params = urllib.parse.parse_qs(parsed_url.query)
        return parsed_url.path.rstrip("/") or "/"

    def _query_value(self, name: str, default: str = "") -> str:
        """The first value of one query parameter, or `default`."""
        return (self.query_params.get(name) or [default])[0]

    # -- routing -------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - required name
        """
        Answer a GET: the two open routes first, then the token check,
        then the route table.

        /api/ping says only that the agent is alive, and the dashboard
        shell carries no data of its own -- everything on it arrives
        from the authenticated API -- so both are served without a
        token. That lets you bookmark the dashboard and be prompted for
        the token once. /api/events alone may take the token in the URL
        (see _authorised).
        """
        path = self._split_request_url()
        if path == "/api/ping":
            return self._serve_ping()
        if path == "/":
            return self._send_text(webui.PAGE, content_type="text/html")
        if not self._authorised(self.query_params,
                                allow_query=(path == "/api/events")):
            return self._send_unauthorised(self.query_params)
        route = self._get_routes().get(path)
        if route is None:
            return self._send_not_found(path)
        return route()

    def _get_routes(self) -> dict:
        """Each authenticated GET route and the method serving it."""
        return {
            "/api/status": self._serve_status,
            "/api/device": self._serve_device,
            "/api/volumes": self._serve_volumes,
            "/api/drives": self._serve_drives,
            "/api/capacity": self._serve_capacity,
            "/api/battery": self._serve_battery,
            "/api/health": self._serve_health,
            "/api/droboconfig": self._serve_droboconfig,
            "/api/shares": self._serve_shares,
            "/api/discover": self._serve_discovery,
            "/api/netcheck": self._serve_netcheck,
            "/api/droboapps": self._serve_droboapps,
            "/api/update": self._serve_update,
            "/api/leds": self._serve_leds,
            "/api/spindown": self._serve_spindown,
            "/api/backup/status": self._serve_backup_status,
            "/api/emailalerts": self._serve_email_alerts,
            "/api/drobosync": self._serve_drobosync,
            "/api/eventlog": self._serve_event_log,
            "/api/performance": self._serve_performance,
            "/api/alerts": self._serve_alerts,
            "/api/history": self._serve_history,
            "/api/photos/stats": self._serve_photo_stats,
            "/api/photos/session": self._serve_photo_upload_progress,
            "/api/pair": self._serve_pairing,
            "/api/pair.svg": self._serve_pairing_qr,
            "/api/events": self._stream_events,
        }

    def do_POST(self) -> None:  # noqa: N802
        """
        Answer a POST: the token check, then the route table, then the
        mock driver's controls.

        No allow_query here, deliberately: nothing this handler writes
        may be reachable by a request a cross-site <form> can forge.
        See _authorised.
        """
        path = self._split_request_url()
        if not self._authorised(self.query_params):
            self._drain_request_body()
            return self._send_unauthorised(self.query_params)
        route = self._post_routes().get(path)
        if route is not None:
            return route()
        if path.startswith("/api/mock/"):
            return self._mock_control(path)
        return self._send_not_found(path)

    def _post_routes(self) -> dict:
        """Every POST route with a fixed path, and its method."""
        return {
            "/api/backup/plan": self._serve_backup_plan,
            "/api/backup/start": self._serve_backup_start,
            "/api/emailalerts/test": self._serve_email_test,
            "/api/photos/rescan": self._serve_photo_rescan,
            "/api/photos/have": self._serve_photos_have,
            "/api/photos": self._receive_photo,
            "/api/photos/begin": self._serve_photo_upload_begin,
            "/api/photos/chunk": self._serve_photo_upload_chunk,
            "/api/photos/finish": self._serve_photo_upload_finish,
            "/api/identify": self._serve_identify,
            "/api/config/brightness": self._serve_brightness_write,
            "/api/config/spindown": self._serve_spindown_write,
            "/api/config/share-access": self._serve_share_access_write,
            "/api/shares/create": self._serve_share_create,
            "/api/shares/delete": self._serve_share_delete,
            "/api/droboapps/action": self._serve_droboapps_action,
            "/api/mock/sign-in": self._mock_sign_in,
        }

    # -- GET: readings from the last poll ------------------------------

    def _serve_ping(self) -> None:
        """Liveness and version; one of two routes needing no token."""
        return self._send_json(
            {"ok": True, "agent": "drobo-agent", "version": VERSION,
             "uptime_seconds": int(time.time() - self.monitor.started_at)}
        )

    def _serve_status(self) -> None:
        return self._send_json(self.monitor.snapshot_dict())

    def _serve_device(self) -> None:
        return self._send_json(
            self.monitor.snapshot_dict().get("device", {}))

    def _serve_volumes(self) -> None:
        """
        The volumes (Drobo calls them LUNs) the pack is carved into --
        what Windows shows as drive letters. Read straight from the
        status greeting, so this costs no extra command and no extra
        connection to a device that tires easily.
        """
        snapshot = self.monitor.snapshot_dict()
        return self._send_json({
            "volumes": snapshot.get("volumes", []),
            "max_volumes": snapshot.get("max_volumes")})

    def _serve_drives(self) -> None:
        return self._send_json(
            {"drives": self.monitor.snapshot_dict().get("drives", [])})

    def _serve_capacity(self) -> None:
        return self._send_json(
            self.monitor.snapshot_dict().get("capacity", {}))

    def _serve_battery(self) -> None:
        return self._send_json(
            self.monitor.snapshot_dict().get("battery", {}))

    def _serve_health(self) -> None:
        return self._send_json(
            self.monitor.snapshot_dict().get("pack_health", {}))

    def _serve_event_log(self) -> None:
        return self._send_json(
            {"events": self.monitor.snapshot_dict().get("event_log", [])})

    def _serve_performance(self) -> None:
        return self._send_json(
            self.monitor.snapshot_dict().get("performance", {}))

    def _serve_alerts(self) -> None:
        """Active alerts, or every alert ever raised with ?all=1."""
        want_all = self._query_value("all", "0") not in ("0", "", "false")
        alerts = (self.monitor.all_alerts() if want_all
                  else self.monitor.active_alerts())
        return self._send_json({"alerts": alerts})

    def _serve_history(self) -> None:
        """The last ?limit= poll results, trimmed for graphing."""
        try:
            max_points = int(self._query_value(
                "limit", str(DEFAULT_HISTORY_POINTS)))
        except ValueError:
            max_points = DEFAULT_HISTORY_POINTS
        return self._send_json({"points": self.monitor.history_points(
            max(1, min(max_points, MAX_HISTORY_POINTS)))})

    # -- GET: read from the device on demand ---------------------------

    def _serve_droboconfig(self) -> None:
        """
        One section of the Drobo's own settings (network by default).

        On demand, not on every poll: this opens a second connection to
        the command port, and configuration changes rarely.
        """
        section = self._query_value("section", "network")
        # The simulator answers from its own canned config, in the same
        # shape a real device returns, so demo mode shows a populated
        # Settings tab instead of "no live Drobo connection yet".
        if getattr(self.monitor.driver, "simulate_config", False):
            return self._serve_simulated_droboconfig(section)
        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        try:
            return self._send_json({
                "section": section,
                "config": config_cmd.get_config(
                    host, esa_id, section,
                    timeout=self.COMMAND_TIMEOUT_SECONDS),
            })
        except config_cmd.ConfigError as exc:
            return self._send_json(
                {"error": str(exc), "section": section}, 502)
        except OSError as exc:
            return self._send_command_port_unreachable(exc)

    def _serve_simulated_droboconfig(self, section: str) -> None:
        """The simulator's canned config; an unknown section is 400."""
        try:
            return self._send_json({
                "section": section,
                "config": self.monitor.driver.get_config(section),
            })
        except ValueError as exc:
            return self._send_json(
                {"error": str(exc), "section": section}, 400)

    def _serve_droboapps(self) -> None:
        """
        What's installed ON the Drobo. On-demand, not polled: the app
        list changes when someone installs something, which is roughly
        never, and the command port does not enjoy being asked twice.
        """
        driver = self.monitor.driver
        if getattr(driver, "simulate_config", False):
            return self._send_json(
                {**driver.get_droboapps(), "simulated": True})
        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        try:
            return self._send_json(
                droboapps.get_droboapps(
                    host, esa_id,
                    timeout=self.COMMAND_TIMEOUT_SECONDS))
        except (sysinfo.SysInfoError, OSError) as exc:
            return self._send_json({"error": str(exc), "apps": []}, 502)

    def _serve_update(self) -> None:
        """
        Whether a newer version exists. Dormant unless config.json's
        "updates" block is switched on; with it off this touches no
        network at all -- see updates.check.

        Cached, because the answer changes at most daily and this is
        rendered on a dashboard that refreshes every few seconds.
        Without the cache, leaving a browser tab open would hammer
        whatever host ends up serving the manifest.
        """
        with _ApiRequestHandler._update_lock:
            updates_config = self.monitor.config.get("updates", {})
            cache_seconds = float(updates_config.get(
                "check_seconds", DEFAULT_UPDATE_CHECK_SECONDS))
            cached_payload = self._fresh_cached_payload(
                _ApiRequestHandler._update_cache, cache_seconds)
            if cached_payload is not None:
                return self._send_json(cached_payload)
            status = updates.check(
                self.monitor.config.get("updates", {}), VERSION)
            payload = status.to_dict()
            # A disabled check is not worth caching for a day: flipping
            # the setting on should take effect on the next refresh, not
            # tomorrow.
            if status.state != updates.STATE_DISABLED:
                _ApiRequestHandler._update_cache = (time.time(), payload)
        return self._send_json(payload)

    @staticmethod
    def _fresh_cached_payload(cache_entry, max_age_seconds: float):
        """
        The payload of a (cached_at_seconds, payload) cache entry while
        it is younger than `max_age_seconds`; None when there is no
        entry or it has gone stale.
        """
        if not cache_entry:
            return None
        cached_at_seconds, cached_payload = cache_entry
        if time.time() - cached_at_seconds < max_age_seconds:
            return cached_payload
        return None

    def _serve_leds(self) -> None:
        """
        LED brightness, and whether the unit is in shop-demo mode.

        eCmdGetDimming and eCmdGetDemoModeInfo: two of the seven
        commands that answer with real data on this firmware. Both had
        working parsers and no way to reach them until 2026-07-28 -- a
        measurement nobody could see.

        On demand, never polled: neither changes without someone
        changing it, and each costs a command-port connection on a
        device that stops answering under sustained use.
        """
        driver = self.monitor.driver
        if getattr(driver, "simulate_config", False):
            return self._send_json({
                "dimming": driver.get_dimming(),
                **driver.get_demo_mode(),
                "scale_note": DIMMING_SCALE_NOTE,
                "simulated": True,
            })
        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        return self._send_json(self._read_leds_from_device(host, esa_id))

    def _read_leds_from_device(self, host: str, esa_id: str) -> dict:
        """
        The /api/leds answer from two device reads, each allowed to
        fail on its own.
        """
        payload: dict = {"scale_note": DIMMING_SCALE_NOTE}
        try:
            payload["dimming"] = droboapps.get_dimming(
                host, esa_id, timeout=self.COMMAND_TIMEOUT_SECONDS)
        except (sysinfo.SysInfoError, OSError) as exc:
            payload["dimming"] = None
            payload["error"] = str(exc)
        try:
            payload.update(droboapps.get_demo_mode(
                host, esa_id, timeout=self.COMMAND_TIMEOUT_SECONDS))
        except (sysinfo.SysInfoError, OSError):
            # Best-effort. A missing demo-mode read must not cost the
            # brightness reading, which is the half anyone came for.
            payload.setdefault("demo_mode", None)
            payload.setdefault("scale_factor", None)
        return payload

    def _serve_spindown(self) -> None:
        """
        The spin-down state, read from the same <General> block the
        write path (/api/config/spindown) edits and verifies against --
        one confirmed reader on both sides, so the read and the write
        can never disagree about where the truth lives. On demand, never
        polled: each ask costs a command-port connection, and the
        setting changes only when somebody changes it.
        """
        driver = self.monitor.driver
        if getattr(driver, "simulate_config", False):
            return self._send_json({**driver.get_spindown(),
                                    "delay_note": SPINDOWN_UNIT_NOTE,
                                    "simulated": True})
        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        # One device read serves both facts. The SDK's public
        # get_spindown() returns only the delay, and "enabled" is the
        # FeatureStates bit beside it (measured 2026-08-12) -- so the
        # block is fetched once and both fields taken from it, borrowing
        # the SDK's own block reader the same way the mock driver
        # borrows its private constants, rather than second-guessing the
        # wrapper shape here.
        try:
            general_block = droboapps._extract_general_block(
                config_cmd.get_config(
                    host, esa_id, "general",
                    timeout=self.COMMAND_TIMEOUT_SECONDS))
        except config_cmd.ConfigError as exc:
            # Covers the whole family: SysInfoError and the SDK's
            # unrecognised-block refusal both subclass ConfigError.
            return self._send_json({"error": str(exc)}, 502)
        except OSError as exc:
            return self._send_command_port_unreachable(exc)
        feature_states = self._int_or_none(
            general_block.get("FeatureStates"))
        return self._send_json({
            "enabled": self._spindown_enabled(feature_states),
            "delay": self._int_or_none(general_block.get("SpindownDelay")),
            "delay_note": SPINDOWN_UNIT_NOTE,
        })

    @staticmethod
    def _spindown_enabled(feature_states: int | None) -> bool | None:
        """
        Whether FeatureStates has the spin-down bit set. None means "the
        field would not parse", which is not the same claim as off/0 --
        same rule as parse_dimming.
        """
        if feature_states is None:
            return None
        return bool(feature_states & droboapps._SPINDOWN_ENABLED_BIT)

    def _serve_drobosync(self) -> None:
        """
        Drobo-to-Drobo replication. Built and dormant by decision
        (ROADMAP, 2026-07-28): it needs two units and only a single unit
        is available, so it reports "not configured" honestly rather
        than being absent.
        """
        driver = self.monitor.driver
        if getattr(driver, "simulate_config", False):
            return self._send_json(
                {**driver.get_drobosync(), "simulated": True})
        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        try:
            return self._send_json(drobosync.summary(
                host, esa_id, timeout=self.COMMAND_TIMEOUT_SECONDS))
        except (drobosync.DroboSyncError, OSError) as exc:
            return self._send_json({"error": str(exc), "configured": False,
                                    "reads": {}}, 502)

    # -- GET: the agent's own settings and state -----------------------

    def _serve_backup_status(self) -> None:
        job = _BACKUP_RUNNER.job
        return self._send_json(job.to_dict() if job else
                               {"running": False,
                                "summary": "No backup has been run."})

    def _serve_email_alerts(self) -> None:
        """
        Whether email alerts are set up, WITHOUT ever returning the
        credentials that make them work. The Windows app renders this on
        its Preferences tab; it must be able to say "configured" without
        being told the password, which is why this reports a summary
        rather than the config block.
        """
        email_config = self.monitor.config.get("notify", {}).get(
            "email", {})
        email_enabled = "email" in self.monitor.config.get(
            "notify", {}).get("backends", [])
        email_configured = bool(email_enabled and email_config.get("host")
                                and email_config.get("to"))
        recipients = email_config.get("to", "")
        if isinstance(recipients, list):
            recipients = ", ".join(recipients)
        return self._send_json({
            "configured": email_configured,
            "enabled": email_enabled,
            # Host and recipients are not secrets and are the two things
            # somebody needs to confirm they set it up the way they
            # meant. The password is never included, at any verbosity.
            "host": email_config.get("host", ""),
            "to": recipients,
            "summary": (f"Sending to {recipients} via "
                        f"{email_config.get('host', '')}."
                        if email_configured else
                        "Not configured."),
        })

    def _serve_photo_stats(self) -> None:
        return self._send_json(self.photo_store.stats())

    def _serve_photo_upload_progress(self) -> None:
        """How much of a chunked upload the agent already holds."""
        upload_id = (self._query_value("id")
                     or self.headers.get("X-Upload-Id") or "").lower()
        try:
            return self._send_json(
                self.photo_store.session_progress(upload_id))
        except PhotoStoreError as exc:
            return self._send_json({"error": str(exc)}, 400)

    def _serve_pairing(self) -> None:
        """Everything the phone needs to connect, in one payload."""
        return self._send_json({
            "agent": "drobo-agent", "version": VERSION,
            "url": self._pair_url(),
            "token": self.token,
            "photos_enabled": self.photo_store.enabled,
        })

    def _serve_pairing_qr(self) -> None:
        """
        The pairing link as a QR code. It carries the token, so this
        stays behind auth like everything else.
        """
        return self._send_text(
            qr.svg(f"{self._pair_url()}/?token={self.token}"),
            content_type="image/svg+xml")

    # -- POST: the agent's own actions ---------------------------------

    def _serve_backup_plan(self) -> None:
        """What a backup WOULD do. Always safe: copies nothing."""
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        source, destination, mirror = self._backup_arguments(body)
        return self._send_json(
            backup.plan(source, destination, mirror).to_dict())

    def _serve_backup_start(self) -> None:
        """
        Run a backup. Requires "confirm": true ON TOP of being a
        separate endpoint from /plan -- two deliberate acts before
        anything is written, because the cost of an accidental run with
        mirror on is somebody's backup deleting itself.
        """
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        source, destination, mirror = self._backup_arguments(body)
        if body.get("confirm") is not True:
            return self._send_json(
                {"error": "Refusing to start without \"confirm\": true. "
                          "Call /api/backup/plan first and show the "
                          "user what it says -- especially the "
                          "mirror warning."}, 400)
        try:
            job = _BACKUP_RUNNER.start(source, destination, mirror)
        except backup.BackupError as exc:
            return self._send_json({"error": str(exc)}, 409)
        return self._send_json(job.to_dict())

    @staticmethod
    def _backup_arguments(body) -> tuple[str, str, bool]:
        """(source, destination, mirror) from a backup request body."""
        source = str(body.get("source", "")).strip()
        destination = str(body.get("destination", "")).strip()
        mirror = bool(body.get("mirror", False))
        return source, destination, mirror

    def _serve_email_test(self) -> None:
        """
        Send a test email through the configured alert settings.

        POST, not GET: this sends real mail, and a GET must never have a
        side effect -- browsers and link prefetchers will happily follow
        one. Same rule that put /api/identify on POST.
        """
        notify_config = self.monitor.config.get("notify", {})
        if "email" not in notify_config.get("backends", []):
            return self._send_json(
                {"error": "Email alerts are not switched on. Add "
                          "\"email\" to the notify backends in the "
                          "agent's config.json."}, 409)
        mailer = notify.build({**notify_config, "backends": ["email"]})
        email_notifiers = [notifier
                           for notifier in getattr(mailer, "members", [])
                           if notifier.name == "email"]
        if not email_notifiers:
            return self._send_json(
                {"error": "Email is switched on but incompletely "
                          "configured -- check host and to in "
                          "config.json. The agent log says which "
                          "field is missing."}, 409)
        try:
            email_notifiers[0].send(
                "Drobo Dashboard REBORN: test",
                "This is a test. If you are reading it, alerts about "
                "your Drobo will reach you here.",
                "info")
        # Already sanitised by EmailNotifier.
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": str(exc)}, 502)
        return self._send_json(
            {"ok": True, "message": "Sent. Check your inbox."})

    def _serve_photo_rescan(self) -> None:
        """
        Re-read what is actually on the Drobo and rebuild the dedupe
        index from it. On demand rather than scheduled: it hashes every
        backed-up file, which is real I/O over SMB.

        Worth running after deleting photos off the Drobo by hand --
        otherwise the index still lists them, `have` answers "already
        got it", and the phone never re-sends. See PhotoStore.rescan.
        """
        try:
            return self._send_json(self.photo_store.rescan())
        except PhotoStoreError as exc:
            return self._send_json({"error": str(exc)}, 400)

    def _serve_photos_have(self) -> None:
        """Which of the phone's file hashes the agent does not hold."""
        try:
            payload = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        hashes = payload.get("hashes")
        if not isinstance(hashes, list):
            return self._send_json(
                {"error": "expected {\"hashes\": [...]}"}, 400)
        return self._send_json({"missing": self.photo_store.missing(
            [str(hash_text) for hash_text in hashes])})

    def _receive_photo(self) -> None:
        """
        One photo in a single request: the body is the file, named by
        X-Filename and dated by X-Taken-At (unix seconds).
        """
        filename = self.headers.get("X-Filename", "")
        if not filename:
            return self._send_json(
                {"error": "X-Filename header is required"}, 400)
        taken_at_unix_seconds = None
        taken_at_header = self.headers.get("X-Taken-At", "")
        if taken_at_header:
            try:
                taken_at_unix_seconds = float(taken_at_header)
            except ValueError:
                return self._send_json(
                    {"error": "X-Taken-At must be unix seconds"}, 400)
        try:
            photo_bytes = self._read_body(
                self.photo_store.max_single_upload_bytes
                + SINGLE_UPLOAD_SLACK_BYTES)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 413)
        try:
            return self._send_json(self.photo_store.ingest(
                filename, photo_bytes, taken_at_unix_seconds))
        except PhotoStoreError as exc:
            return self._send_json({"error": str(exc)}, 400)

    # Chunked upload -- the path videos take: begin, chunk..., finish.

    def _serve_photo_upload_begin(self) -> None:
        """Start a chunked upload: filename, size and SHA-256."""
        try:
            begin_request = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        try:
            return self._send_json(self.photo_store.begin(
                str(begin_request.get("filename", "")),
                int(begin_request.get("size", 0)),
                str(begin_request.get("sha256", "")).lower(),
                float(begin_request["taken_at"])
                if begin_request.get("taken_at") else None))
        except (PhotoStoreError, ValueError, TypeError) as exc:
            return self._send_json({"error": str(exc)}, 400)

    def _serve_photo_upload_chunk(self) -> None:
        """The next chunk of an upload, placed at X-Offset."""
        upload_id = (self.headers.get("X-Upload-Id") or "").lower()
        try:
            offset_bytes = int(self.headers.get("X-Offset", "-1"))
        except ValueError:
            return self._send_json(
                {"error": "X-Offset must be an integer"}, 400)
        if offset_bytes < 0:
            return self._send_json(
                {"error": "X-Upload-Id and X-Offset "
                          "headers are required"}, 400)
        try:
            chunk_bytes = self._read_body(MAX_UPLOAD_CHUNK_BYTES)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 413)
        try:
            return self._send_json(self.photo_store.append(
                upload_id, offset_bytes, chunk_bytes))
        except PhotoStoreError as exc:
            return self._send_json({"error": str(exc)}, 400)

    def _serve_photo_upload_finish(self) -> None:
        """Check the whole upload against its SHA-256, then file it."""
        upload_id = (self.headers.get("X-Upload-Id") or "").lower()
        try:
            return self._send_json(self.photo_store.finish(upload_id))
        except PhotoStoreError as exc:
            return self._send_json({"error": str(exc)}, 400)

    # -- POST: writes that reach the Drobo -----------------------------

    def _serve_identify(self) -> None:
        """
        Flash the Drobo's lights so you can tell which box is which.

        POST, not GET, and deliberately so: this changes something on
        the Drobo (the first endpoint here that ever did). A GET must
        never have a side effect -- browsers and link prefetchers will
        happily follow one.
        """
        try:
            interval = self._parse_identify_interval(
                self._query_value("interval"))
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)

        # Checked HERE, before the simulated/real fork, so both arms
        # enforce the same rule. Left to the packet builder it would
        # only bind the hardware path, and a simulator that accepts what
        # the device rejects certifies things that do not work.
        try:
            nasd_identify.validate_interval(interval)
        except nasd_identify.IdentifyParameterError as exc:
            return self._send_json({"error": str(exc)}, 400)

        driver = self.monitor.driver
        if getattr(driver, "simulate_config", False):
            # The simulator "blinks" by saying it did. Lets the whole
            # path -- button, endpoint, result handling -- be exercised
            # without hardware, which is how it was built in the first
            # place. It takes the interval too, so the simulated path
            # proves the value is carried rather than dropped on the way
            # through.
            driver.identify(interval)
            return self._send_json({
                "ok": True, "simulated": True, "interval": interval,
                "message": _identify_message(interval, simulated=True)})
        return self._identify_on_device(interval)

    @staticmethod
    def _parse_identify_interval(interval_text: str) -> int | None:
        """
        ?interval=N sets <IdentifyInterval>. Omitted uses the SDK
        default; 0 is the best guess at stopping a running blink;
        "none" sends the historical empty <Params>, which is the control
        case for proving the interval is what makes the lights move.
        Exposed rather than kept internal precisely so that comparison
        can be run without editing code. Raises ValueError, worded for
        the caller, for anything else that is not a whole number.
        """
        interval_text = interval_text.strip().lower()
        if not interval_text:
            return nasd_identify.DEFAULT_INTERVAL
        if interval_text == "none":
            return None
        try:
            return int(interval_text)
        except ValueError:
            raise ValueError(
                f"interval must be a whole number or "
                f"'none', got {interval_text!r}") from None

    def _identify_on_device(self, interval: int | None) -> None:
        """Send Identify to the real Drobo and report how it went."""
        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        try:
            result = nasd_identify.identify(
                host, esa_id, timeout=self.COMMAND_TIMEOUT_SECONDS,
                interval=interval)
        except nasd_identify.IdentifyParameterError as exc:
            # Caller asked for something we will not send. Must be 400
            # and not 502 -- checked before IdentifyError, which it
            # subclasses.
            return self._send_json({"error": str(exc)}, 400)
        except nasd_identify.IdentifyError as exc:
            return self._send_json({"error": str(exc)}, 502)
        except OSError as exc:
            return self._send_command_port_unreachable(exc)
        return self._send_json({"ok": True, **result,
                                "message": _identify_message(interval)})

    # --- device configuration WRITES ----------------------------------
    # Together with /api/identify above and /api/droboapps/action below,
    # the only endpoints that change anything on the Drobo. Brightness,
    # spin-down and share permissions wrap SDK writes that were
    # adversarially audited and verified on real hardware (2026-08-12);
    # share create/delete wrap SDK writes that mirror the 2026-08-25
    # capture of the original Dashboard byte-for-byte in structure but
    # have not themselves been run against hardware yet. Every one is
    # POST, requires the token, and reports the SDK's own read-back
    # verification -- so "ok" here means "the device reads back the new
    # value", not merely "accepted" (RESULT_ACCEPTED means received; two
    # writes this project made were accepted and silently did the wrong
    # thing). Since 2026-08-25 the reverse case is known too: a device
    # that answers NOTHING and applies the write anyway. Every answer
    # therefore carries an "outcome" field -- see the WRITE_* constants
    # and _config_write for the four it can hold.

    def _serve_brightness_write(self) -> None:
        """{"value": N} sets the LED brightness."""
        return self._config_write(
            lambda body: self._parse_whole_number_field(body, "value"),
            lambda host, esa_id, value: droboapps.set_dimming(
                host, esa_id, value, timeout=self.COMMAND_TIMEOUT_SECONDS),
            simulate_write=lambda driver, value: driver.set_dimming(value),
            describe_success=lambda value: (
                f"LED brightness set to {value}."))

    def _serve_spindown_write(self) -> None:
        """
        {"delay": N} sets a NONZERO delay (enabling spin-down if it was
        off); {"enabled": false} disables it. The SDK refuses a bare
        delay of 0 because the device coerces it -- measured
        2026-08-12.
        """
        return self._config_write(
            lambda body: self._parse_spindown_request(body),
            self._write_spindown_on_device,
            simulate_write=lambda driver, spindown_delay: (
                driver.set_spindown(spindown_delay)),
            describe_success=lambda spindown_delay: (
                "Drive spin-down turned off." if spindown_delay is None
                else f"Drive spin-down delay set to {spindown_delay}."))

    def _write_spindown_on_device(self, host: str, esa_id: str,
                                  spindown_delay: int | None):
        """The SDK write for a parsed spin-down request (None = off)."""
        if spindown_delay is None:
            return droboapps.disable_spindown(
                host, esa_id, timeout=self.COMMAND_TIMEOUT_SECONDS)
        return droboapps.set_spindown(
            host, esa_id, spindown_delay,
            timeout=self.COMMAND_TIMEOUT_SECONDS)

    def _serve_share_access_write(self) -> None:
        """
        {"share": name, "user": name, "access": "0"|"1"} -- one user's
        access on one share. The SDK refuses an unconfirmed code and
        refuses ambiguous (case-colliding) share or user names.
        """
        return self._config_write(
            lambda body: self._parse_share_access_request(body),
            self._write_share_access_on_device,
            simulate_write=lambda driver, share_user_access: (
                driver.set_share_access(*share_user_access)),
            describe_success=lambda share_user_access: (
                f"{share_user_access[1]!r} on {share_user_access[0]!r} "
                f"set to access {share_user_access[2]}."))

    def _write_share_access_on_device(self, host: str, esa_id: str,
                                      share_user_access: tuple):
        """The SDK write for a parsed (share, user, access) request."""
        share, user, access = share_user_access
        return sharewrite.set_share_user_access(
            host, esa_id, share, user, access,
            timeout=self.COMMAND_TIMEOUT_SECONDS)

    def _serve_share_create(self) -> None:
        """
        {"name": ...} -- create one share. The SDK write mirrors the
        2026-08-25 capture of the original Dashboard creating a share,
        and a 200 means the re-read LISTS the new share -- which starts
        with an empty <ShareUsers/>, no access for anyone, until
        /api/config/share-access grants it. The name is validated in the
        parse step as well as by the SDK/mock, so the simulated and
        hardware paths refuse identical requests with identical codes.
        """
        return self._config_write(
            self._parse_new_share_name,
            lambda host, esa_id, name: sharewrite.create_share(
                host, esa_id, name, volume=self._new_share_volume(),
                timeout=self.COMMAND_TIMEOUT_SECONDS),
            simulate_write=lambda driver, name: driver.create_share(name),
            describe_success=lambda name: (
                f"Share {name!r} created. It starts "
                f"with no access for anyone."))

    def _serve_share_delete(self) -> None:
        """
        {"name": ..., "confirm_name": ...} -- delete one share, and with
        it everything the share holds. confirm_name must repeat the name
        EXACTLY (case-sensitive, untrimmed); the mismatch 400 comes from
        the parse step, before _command_target and before the simulate
        fork, so a failed confirmation can never touch any device. The
        SDK write mirrors the 2026-08-25 capture: the config resent with
        the share's node absent plus <ShareDeleteConfirm>Y</...>
        appended.
        """
        return self._config_write(
            self._parse_share_delete_request,
            lambda host, esa_id, name: sharewrite.delete_share(
                host, esa_id, name, timeout=self.COMMAND_TIMEOUT_SECONDS),
            simulate_write=lambda driver, name: driver.delete_share(name),
            describe_success=lambda name: (
                f"Share {name!r} deleted, along with "
                f"everything it held."))

    def _config_write(self, parse_request, perform_write, simulate_write,
                      describe_success):
        """
        One shape for every config write endpoint, so the error-code
        discipline cannot drift between them:

            400  the caller asked for something we will not send
                 (bad JSON, missing/invalid field, an SDK parameter
                 refusal)
            503  no live Drobo yet (command port needs address + serial)
            502  WRITE_FAILED -- the device refused it, could not
                 be reached, or took it and reads back unchanged.
                 Nothing applied
            502  WRITE_UNKNOWN -- it went out and could not be settled
                 either way. Still an error status, because the caller
                 must not treat it as done, but worded as "may have
                 applied, re-read first" and never as "nothing happened"
            200  WRITE_VERIFIED or WRITE_UNACKNOWLEDGED -- the device
                 reads back the new value. The second means it never
                 acknowledged the write and the read-back is the only
                 reason we know it applied (measured on hardware
                 2026-08-25); it is a success, and the body says so
                 plainly with "acknowledged": false and a message that
                 states it

        Every answer past the parse step carries "outcome" -- one of the
        WRITE_* constants above -- so neither UI has to infer any of
        this from the wording, and 502 alone never has to mean "red".

        `parse_request(body) -> parsed_request` validates the JSON and
        returns the typed argument(s); raising ValueError is the 400
        path. `perform_write(host, esa_id, parsed_request)` is the real
        SDK write. `simulate_write(driver, parsed_request)` is the mock
        driver's stand-in, so the whole UI path can be exercised without
        hardware -- and it is given the SAME validated request, so a
        value the device would refuse is refused here too. Both return
        the SDK's result shape and raise the SDK's exception types, so
        the classification in _attempt_config_write runs once for both
        rather than as two sets of rules that can drift.
        `describe_success(parsed_request)` is the human sentence for
        success.
        """
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        try:
            parsed_request = parse_request(body)
        except (ValueError, TypeError) as exc:
            return self._send_json({"error": str(exc)}, 400)

        driver = self.monitor.driver
        simulated = bool(getattr(driver, "simulate_config", False))
        if simulated:
            run_write = functools.partial(
                simulate_write, driver, parsed_request)
        else:
            host, esa_id, unavailable_body = self._command_target()
            if unavailable_body:
                return self._send_json(unavailable_body, 503)
            run_write = functools.partial(
                perform_write, host, esa_id, parsed_request)
        return self._attempt_config_write(
            run_write, simulated, describe_success, parsed_request)

    def _attempt_config_write(self, run_write, simulated: bool,
                              describe_success, parsed_request):
        """
        Run the write and answer for it. The except clauses below are an
        ordered classification, and the order is the point: see each
        one, and _UNCONFIRMED_WRITE_ERRORS for why the unsettled types
        must be caught before every refusal branch.
        """
        try:
            result = run_write()
        except ValueError as exc:
            # How the mock driver refuses a parameter -- the caller's
            # fault, exactly like a parse_request() refusal, so 400.
            # Left to propagate on the hardware path: the SDK has its
            # own refusal types below, and a bare ValueError out of it
            # would be a bug in the SDK rather than a bad request, which
            # a 400 would blame on the wrong party.
            if not simulated:
                raise
            return self._write_problem(str(exc), 400, simulated)
        except _UNCONFIRMED_WRITE_ERRORS as exc:
            # The write went out and could not be settled. Decided by
            # TYPE, before any wording is examined -- it has to be: that
            # message quotes the transport failure that broke the
            # read-back, and a ConnectionRefusedError puts both
            # "refused" and "ConnectionRefusedError" into the text,
            # which the heuristic below would read as the caller's
            # fault. A 400 there would be the worst possible answer,
            # because 400 invites the caller to fix the request and send
            # it again, and this is the one case that must NOT be
            # blindly retried: the write most likely APPLIED and only
            # the confirmation was lost.
            return self._write_unsettled(str(exc), simulated)
        except (droboapps.GeneralWriteError, ShareEditError) as exc:
            # The SDK's other refusals are domain-specific. A parameter
            # it will not send is the caller's fault (400); an
            # accepted-but-unapplied or ambiguous-on-device condition is
            # the device's (502). The SDK words refusals with
            # "refus"/"must"; follow its judgement. "no share named" is
            # the caller's fault too -- asking to edit or delete a share
            # the device does not list -- and the pinned share
            # create/delete contract makes that a 400.
            return self._write_problem(
                str(exc), self._refusal_http_status(exc), simulated)
        except config_cmd.ConfigWriteError as exc:
            # The SDK's "the device said no" -- a refusal, an
            # unreachable command port, or a write its own read-back
            # proved did not land. Its unsettled subclass is in
            # _UNCONFIRMED_WRITE_ERRORS and was caught above, so
            # everything reaching here really did change nothing.
            return self._write_problem(str(exc), 502, simulated)
        except config_cmd.ConfigError as exc:
            # NOT ConfigWriteError (caught above). The SDK types
            # everything that happens after the frame is on the wire, so
            # what reaches here is its READ failing before the write
            # went out -- but this layer cannot prove that, and the cost
            # of the two guesses is not equal. On 2026-08-25 the 5N
            # answered a share create with exactly this kind of error
            # (ConfigError: short header: 0 of 16 bytes) and had applied
            # the write. "May have applied, go and look" is the only
            # answer that is never a lie.
            return self._write_unsettled(str(exc), simulated)
        except OSError as exc:
            # The command port could not be reached -- raised by the
            # SDK's own read before anything is sent. Nothing applied.
            return self._write_problem(
                f"could not reach the command port: {exc}", 502, simulated)
        return self._answer_write_result(
            result, simulated, describe_success, parsed_request)

    @staticmethod
    def _refusal_http_status(exc: Exception) -> int:
        """
        400 when the SDK's refusal wording blames the request, else 502.
        """
        lowered_reason = str(exc).lower()
        if ("refus" in lowered_reason
                or "must" in lowered_reason
                or "no share named" in lowered_reason):
            return 400
        return 502

    def _answer_write_result(self, result, simulated: bool,
                             describe_success, parsed_request):
        """
        Answer for a write the SDK returned from, by the outcome its
        result describes -- a 200 only for the two verified outcomes.
        """
        outcome, acknowledged = _write_outcome(result)
        if outcome == WRITE_UNKNOWN:
            return self._write_unsettled(
                "the write was sent, but the Drobo's answer did not settle "
                "whether it applied.", simulated)
        if outcome == WRITE_FAILED:
            # The SDK raises for this today. If a future shape returns
            # it instead, it still must not answer 200.
            return self._write_problem(
                "the Drobo took the write and then read back unchanged. "
                "Received, not applied.", 502, simulated)

        message = describe_success(parsed_request)
        if outcome == WRITE_UNACKNOWLEDGED:
            message += "  " + UNACKNOWLEDGED_WRITE_NOTE
        if simulated:
            message += " (simulated)"
        response_body = self._write_success_body(
            result, outcome, acknowledged, message, simulated)
        self._invalidate_shares_cache()
        return self._send_json(response_body)

    @staticmethod
    def _write_success_body(result: dict, outcome: str, acknowledged: bool,
                            message: str, simulated: bool) -> dict:
        """
        The 200 body: the outcome and message, plus the SDK result's own
        verification fields where it has them.
        """
        # The SDK's own flag wins where it has one, so a write that sent
        # NOTHING (already set that way) reports null rather than
        # claiming an acknowledgement nobody ever gave. Only the
        # classification needs that reduced to a bool.
        response_body = {
            "ok": True, "outcome": outcome, "message": message,
            "acknowledged": result.get("acknowledged", acknowledged)}
        if simulated:
            response_body["simulated"] = True
        for field_name in ("verified", "read_back", "result_code",
                           "unchanged"):
            if field_name in result:
                response_body[field_name] = result[field_name]
        return response_body

    def _write_problem(self, detail: str, http_status: int, simulated: bool):
        """
        A write that changed nothing: the agent's own outcome flag
        beside the SDK's plain-English reason, so a UI never has to read
        the prose to choose between red and amber. `http_status` is
        whatever it was before this helper existed -- 400 the caller's
        fault, 502 the device's.
        """
        response_body = {"error": detail, "outcome": WRITE_FAILED}
        if simulated:
            response_body["simulated"] = True
        return self._send_json(response_body, http_status)

    def _write_unsettled(self, detail: str, simulated: bool):
        """
        A write nobody can settle. 502, because the caller must not
        treat it as done -- but flagged WRITE_UNKNOWN so a UI shows a
        warning rather than a failure, and worded so it can never be
        read as "nothing happened".
        """
        error_text = f"{detail}  {UNKNOWN_WRITE_GUIDANCE}"
        if simulated:
            error_text += " (simulated)"
        response_body = {"error": error_text, "outcome": WRITE_UNKNOWN}
        if simulated:
            response_body["simulated"] = True
        # The device may well have changed. Whatever /api/shares is
        # holding is no longer known to be true, so the next read must
        # go to the device -- the re-read this answer tells the user to
        # do has to be a real one.
        self._invalidate_shares_cache()
        return self._send_json(response_body, 502)

    @classmethod
    def _invalidate_shares_cache(cls):
        """
        A config write just changed the device. /api/shares serves a
        cached copy for SHARES_CACHE_SECONDS seconds -- rightly, to keep
        browser refreshes from becoming connection storms on a 2013
        embedded box -- but after a share permission write that cache is
        WRONG, and the Shares tab would keep showing the old access for
        up to a minute. That looks exactly like "the write did not work"
        (it caught this project out once: the read-back said 0 after a
        verified write to 1). Drop the cache so the next read is live.
        Invalidated on every config write, not just share writes --
        brightness and spin-down do not touch shares today, but the cost
        of one extra read is nothing and the cost of a stale answer is a
        confused user.
        """
        with cls._shares_lock:
            cls._shares_cache = None

    @staticmethod
    def _parse_whole_number_field(body, key):
        """
        body[key] as an int, refusing a bool: JSON true is an int to
        Python, and "brightness true" must not quietly become 1.
        """
        value = body.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected {\"" + key + "\": <whole number>}")
        return value

    @staticmethod
    def _parse_spindown_request(body):
        """
        Returns None to mean 'disable', or a positive int delay. A delay
        of 0 is refused rather than read as "off": the device coerces a
        stored 0 (measured 2026-08-12), so turning spin-down off has its
        own request shape, {"enabled": false}.
        """
        if body.get("enabled") is False:
            return None
        delay = body.get("delay")
        if isinstance(delay, bool) or not isinstance(delay, int) or delay <= 0:
            raise ValueError("expected {\"delay\": <positive whole number>} "
                             "or {\"enabled\": false}")
        return delay

    @staticmethod
    def _parse_share_access_request(body):
        """
        (share, user, access) from the request. The access code is only
        checked for being digits here; which codes are confirmed is the
        SDK's (and the mock's) call, so the rule lives in one place.
        """
        share = str(body.get("share", "")).strip()
        user = str(body.get("user", "")).strip()
        access = str(body.get("access", "")).strip()
        if not share or not user or not access.isdigit():
            raise ValueError("expected {\"share\": name, \"user\": name, "
                             "\"access\": \"0\" or \"1\"}")
        return (share, user, access)

    @staticmethod
    def _parse_new_share_name(body):
        """The new share's name, held to the SDK's own rule HERE as well
        as in the SDK and the mock, so the simulated and hardware paths
        refuse identical requests with identical codes. Borrows the
        SDK's pattern rather than restating it -- two copies of one rule
        is how the layers drift apart -- and, like the SDK, refuses
        surrounding whitespace instead of stripping it: a silently
        renamed share could never be verified against the device's
        read-back."""
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("expected {\"name\": \"<share name>\"}")
        if name != name.strip():
            raise ValueError(
                f"invalid share name {name!r}: surrounding whitespace is "
                f"refused rather than silently stripped -- a stripped name "
                f"could never be verified against the device's read-back")
        if not sharewrite._SHARE_NAME_PATTERN.match(name):
            raise ValueError(
                f"invalid share name {name!r}: must be "
                f"{sharewrite._SHARE_NAME_RULE}")
        return name

    @staticmethod
    def _parse_share_delete_request(body):
        """The name of the share to delete, but only once confirm_name
        repeats it EXACTLY -- case-sensitive, untrimmed. Deleting a
        share destroys everything stored in it, so a failed confirmation
        must be refused here in the parse step, where nothing has
        touched any device yet."""
        name = body.get("name")
        confirm = body.get("confirm_name")
        if (not isinstance(name, str) or not name
                or not isinstance(confirm, str)):
            raise ValueError(
                "expected {\"name\": <share name>, \"confirm_name\": "
                "<the same name, retyped>} -- deleting a share destroys "
                "everything stored in it, so the name has to be typed twice")
        if confirm != name:
            raise ValueError(
                f"confirm_name does not repeat the share name exactly (upper "
                f"and lower case count), so nothing was sent to the Drobo. "
                f"Deleting {name!r} would destroy everything stored in it; "
                f"retype its name exactly to confirm.")
        return name

    def _new_share_volume(self) -> str | None:
        """
        The <VolumeName> a created share should carry, from the agent's
        own status data -- or None to leave the choice to the SDK.

        Needed because the SDK derives the volume from the freshly-read
        share config, and on the only unit this project can read that
        config names no volume at all (stored share nodes never carry
        one), so derivation there refuses. What the 2026-08-25 capture
        shows the original Dashboard sending for that unit -- Volume1 --
        matches its status greeting's single, unnamed volume at LUN 0
        rendered as "Volume" + (lun + 1). That rendering is trusted only
        for the captured shape: exactly one volume. A device reporting
        zero or several falls through to the SDK, which refuses rather
        than guesses; a single volume the device itself named is passed
        under its own name.
        """
        volumes = self.monitor.snapshot_dict().get("volumes") or []
        if len(volumes) != 1:
            return None
        volume = volumes[0]
        name = (volume.get("name") or "").strip()
        if name:
            return name
        lun = volume.get("lun")
        return f"Volume{lun + 1}" if isinstance(lun, int) else None

    @staticmethod
    def _int_or_none(field_text) -> int | None:
        """A device config field as a number, or None when it will not
        parse. None is 'the device did not say', which is a different
        claim from 0 -- the same rule parse_dimming follows for
        brightness."""
        try:
            return int(field_text) if isinstance(field_text, str) else None
        except ValueError:
            return None

    # -- POST: DroboApps -----------------------------------------------

    @staticmethod
    def _app_read_back(app_listing: dict, name: str) -> dict | None:
        """One app's row out of an app-list read -- the read-back for
        /api/droboapps/action. None when the list no longer carries the
        name, which the caller reports as unconfirmed rather than
        inventing a state."""
        for app in app_listing.get("apps", []):
            if app.get("name") == name:
                return {"name": app["name"], "running": app.get("running"),
                        "status": app.get("status", "")}
        return None

    def _serve_droboapps_action(self):
        """
        POST /api/droboapps/action {"name": ...,
        "action": "Start"|"Stop"}.

        Start or stop something installed ON the Drobo, via the vetted
        SDK write droboapps_action (wire format CONFIRMED from the
        2026-08-06 capture, both directions). Same error-code discipline
        as _config_write -- 400 caller / 503 no device / 502 device --
        and a 200 carries the device's own re-read of the app list. But
        the read-back is REPORTED, not asserted: a service takes a
        moment to change state, so an immediate re-read can honestly
        still say "stopped" right after an accepted Start.

        The name must be an app the device CURRENTLY reports installed
        -- checked against a fresh list read here, and then the SDK
        write is told check_installed=False, because its own check would
        repeat the identical read and the command port measurably wears
        out under connection volume (see SHARES_CACHE_SECONDS). Three
        connections total: the list, the action, and the re-read that
        becomes the read-back.

        The re-read is best-effort BY DESIGN: once the device has
        accepted the action, a failed confirmation must be reported as
        "accepted, unconfirmed", never as a failure that tempts a retry.
        """
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        name = str(body.get("name", "")).strip()
        action = body.get("action")
        if action not in ("Start", "Stop") or not name:
            return self._send_json(
                {"error": "expected {\"name\": <installed app>, "
                          "\"action\": \"Start\" or \"Stop\"} -- the "
                          "verbs are case-sensitive and only these two "
                          "are confirmed"}, 400)

        driver = self.monitor.driver
        if getattr(driver, "simulate_config", False):
            return self._simulated_droboapps_action(driver, name, action)

        host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)
        refusal_body, refusal_status = self._check_app_installed(
            host, esa_id, name)
        if refusal_body:
            return self._send_json(refusal_body, refusal_status)
        try:
            result = droboapps.droboapps_action(
                host, esa_id, name, action,
                timeout=self.COMMAND_TIMEOUT_SECONDS, check_installed=False)
        except droboapps.DroboAppsActionError as exc:
            # Verb and name were validated above, so whatever is left is
            # the device refusing, unreachable, or answering with the
            # wrong code.
            return self._send_json({"error": str(exc)}, 502)
        except OSError as exc:
            return self._send_command_port_unreachable(exc)
        return self._send_json(self._droboapps_action_body(
            host, esa_id, name, action, result))

    def _simulated_droboapps_action(self, driver, name: str,
                                    action: str) -> None:
        """The simulator's Start/Stop, answered like the device's."""
        try:
            driver.droboapps_action(name, action)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        app_listing = driver.get_droboapps()
        read_back = self._app_read_back(app_listing, name)
        return self._send_json({
            "ok": True, "simulated": True, "name": name, "action": action,
            "read_back": read_back,
            "apps": app_listing.get("apps", []),
            "sdk_version": app_listing.get("sdk_version", ""),
            "message": _app_action_message(name, action, read_back,
                                           simulated=True)})

    def _check_app_installed(self, host: str, esa_id: str, name: str):
        """
        (refusal_body, http_status) when `name` cannot be acted on --
        the app list would not read, or does not carry it -- else
        (None, None).
        """
        try:
            installed_apps = droboapps.get_droboapps(
                host, esa_id,
                timeout=self.COMMAND_TIMEOUT_SECONDS).get("apps", [])
        except (sysinfo.SysInfoError, OSError) as exc:
            return ({"error": f"could not read the app list to check "
                              f"{name!r} against: {exc}"}, 502)
        if name not in {app["name"] for app in installed_apps}:
            installed_names = sorted(app["name"] for app in installed_apps)
            return ({"error": f"{name!r} is not an installed DroboApp on "
                              f"this device (installed: {installed_names}); "
                              f"refusing to act on a name the device did "
                              f"not report"}, 400)
        return None, None

    def _droboapps_action_body(self, host: str, esa_id: str, name: str,
                               action: str, result: dict) -> dict:
        """
        The 200 body for an accepted action, with a best-effort re-read
        of the app list as its read-back.
        """
        app_listing = None
        try:
            app_listing = droboapps.get_droboapps(
                host, esa_id, timeout=self.COMMAND_TIMEOUT_SECONDS)
        except (sysinfo.SysInfoError, OSError):
            pass  # accepted, unconfirmed -- the message says exactly that
        read_back = (self._app_read_back(app_listing, name) if app_listing
                     else None)
        response_body = {
            "ok": True, "name": name, "action": action,
            "result_code": result.get("result_code"),
            "read_back": read_back,
            "message": _app_action_message(name, action, read_back)}
        if app_listing is not None:
            response_body["apps"] = app_listing.get("apps", [])
            response_body["sdk_version"] = app_listing.get("sdk_version", "")
        return response_body

    # -- where the command port is -------------------------------------

    def _command_target(self):
        """
        Where to send a command, or why we can't yet.

        The command port needs both the device's address and its serial,
        and the serial only arrives from a successful status read -- so
        a fresh agent that hasn't polled yet genuinely cannot ask the
        Drobo anything. Returns (host, esa_id, error_dict); exactly one
        of esa_id / error is set.

        A driver whose last poll found another Drobo at our address is
        refused too. Clearing the address would not be enough: with a
        typed IP the fallback below is that same address. Until
        2026-11-02 the poll refused the stranger but these routes still
        logged in to it with our serial.
        """
        driver = self.monitor.driver
        if getattr(driver, "answering_as_another_drobo", False):
            return "", "", {
                "error": "the Drobo's address now answers as another "
                         "Drobo -- nothing is sent to it until yours is "
                         "found again"
            }
        host = (getattr(driver, "_resolved_host", "")
                or getattr(driver, "host", ""))
        esa_id = getattr(driver, "_known_esa_id", "")
        if not host or not esa_id or host in ("auto", ""):
            return "", "", {
                "error": "no live Drobo connection yet -- the command port "
                         "needs the device's address and serial, which "
                         "come from a successful status read first"
            }
        return host, esa_id, None

    # -- GET: the network around the Drobo -----------------------------

    def _serve_netcheck(self) -> None:
        """
        Is this PC even on the same network as the Drobo?

        /api/status already carries this for the device the agent is
        monitoring, in its "network_hint" field. This endpoint exists
        for the case /api/status cannot cover: a front-end sitting on
        its device picker, having failed to find the Drobo it remembers,
        with an address from its own settings and no snapshot to attach
        it to. "Your usual Drobo isn't showing up" and "cannot reach the
        Drobo" have the same cause far more often than not -- mDNS
        doesn't cross a subnet boundary any more than TCP does -- so
        both screens deserve the same answer.

        ?host= defaults to whatever the agent's own driver is trying to
        reach.

        Purely local: it reads this machine's interface list and does
        arithmetic. Nothing is connected to, so an arbitrary host cannot
        be used to make the agent touch anything -- it is a subnet
        comparison, not a probe. Non-IPv4 input is answered honestly
        with "no opinion" rather than an error, because a hostname is a
        perfectly reasonable thing to ask about and the truthful answer
        is that this check can't speak to it.
        """
        target = (self._query_value("host")
                  or self.monitor.driver.target_host() or "").strip()
        result = netcheck.diagnose(target) if target else None
        if result is None:
            return self._send_json({
                "target": target,
                "checked": False,
                "same_network": None,
                "hint": None,
                "reason": "not an IPv4 address" if target
                          else "no address known for the Drobo yet",
            })
        return self._send_json({
            "target": result.target,
            "checked": True,
            "same_network": result.same_network,
            # Null on a match, so a front-end can render `hint` whenever
            # it is present without also having to check same_network
            # and get that test the right way round.
            "hint": None if result.same_network else result.to_dict(),
        })

    def _serve_discovery(self) -> None:
        """
        Every Drobo we can see on this network, already identified.

        This exists so the front page can show you your Drobo and let
        you click it, instead of asking you to know its IP address. Each
        entry is the result of actually connecting and reading the
        device's own greeting -- so the name, model and firmware shown
        are the device's own claims about itself, not a guess from an
        mDNS record.

        `is_current` marks the one the agent is already monitoring.

        `?host=<ip>` adds one address you typed to the search, for the
        networks where the automatic half cannot work. mDNS is
        multicast, and corporate networks, guest SSIDs and some consumer
        routers filter multicast outright -- on those, browsing returns
        nothing no matter how long it listens, and knowing your Drobo's
        address is the only way in. The typed address is probed exactly
        like a discovered one: connect, read the greeting, report what
        the device says about itself. It is listed with `found_by:
        "manual"` so a front-end can be honest about where it came from,
        and it earns no extra trust for having been typed.

        Read-only and safe to call repeatedly, but it does cost a few
        seconds of mDNS listening, so it is cached briefly. A request
        naming a host skips the cache in both directions -- it must
        actually go and look, and its answer must not become the cached
        answer for callers who didn't ask about that address.
        """
        manual_host = self._query_value("host").strip()
        with _ApiRequestHandler._discover_lock:
            cached_payload = (None if manual_host else
                              self._fresh_cached_payload(
                                  _ApiRequestHandler._discover_cache,
                                  self.DISCOVERY_CACHE_SECONDS))
            if cached_payload is not None:
                return self._send_json(cached_payload)

            payload, error_body = self._search_for_drobos(manual_host)
            if error_body:
                return self._send_json(error_body, 502)

            if manual_host:
                self._add_manual_host_report(payload, manual_host)
                # NOT cached: this answer is about one address somebody
                # asked after, and letting it become the cached reply
                # would serve it to the next caller who asked a
                # different question.
                return self._send_json(payload)

            _ApiRequestHandler._discover_cache = (time.time(), payload)
        return self._send_json(payload)

    def _search_for_drobos(self, manual_host: str):
        """
        (payload, None) with every Drobo found and identified, or
        (None, error_body) when the network could not be searched.
        """
        driver = self.monitor.driver
        current_host = (getattr(driver, "_resolved_host", "")
                        or getattr(driver, "host", ""))

        candidates, error_body = self._discovery_candidates(
            manual_host, driver, current_host)
        if error_body:
            return None, error_body
        devices_found = self._identify_candidates(candidates, current_host)

        # The simulated Drobo has no address, so there is nothing on the
        # network to find -- which left the device picker empty and the
        # dashboard unreachable in demo mode. Anyone trying this project
        # without hardware hit a dead end on the very first screen.
        #
        # Appended AFTER the real search rather than replacing it, so
        # the mock driver changes nothing about how discovery behaves.
        # (The first attempt at this short-circuited the whole method
        # and broke ten tests that use the mock driver to exercise the
        # mDNS paths -- the mock is the test harness for real discovery,
        # so it must not bypass it.)
        if isinstance(driver, MockDriver) and not manual_host:
            devices_found.append(self._simulated_device_entry())

        return {"devices": devices_found,
                "searched_for_seconds": self.DISCOVERY_LISTEN_SECONDS}, None

    def _discovery_candidates(self, manual_host: str, driver,
                              current_host: str):
        """
        (candidates, None) -- the addresses worth probing -- or
        (None, error_body) when the mDNS browse could not run.
        """
        if manual_host:
            # One address was named, so look at exactly that address.
            # The multicast browse is skipped entirely rather than run
            # and ignored: it costs DISCOVERY_LISTEN_SECONDS seconds,
            # and the whole reason somebody is typing an address is that
            # the browse comes back empty on their network. Making them
            # wait it out to confirm a host they already know would be
            # three seconds spent proving the thing that failed still
            # fails.
            return [discovery.Candidate(
                host=manual_host, port=esatm.DEFAULT_PORT,
                source="manual")], None
        try:
            candidates = discovery.discover_mdns(
                timeout=self.DISCOVERY_LISTEN_SECONDS)
        except OSError as exc:
            return None, {"error": f"could not search the network: {exc}",
                          "devices": []}

        # The address we already know works belongs in the list even if
        # mDNS was quiet -- on some Windows networks it always is.
        if current_host and current_host not in [
                candidate.host for candidate in candidates]:
            candidates.insert(0, discovery.Candidate(
                host=current_host,
                port=getattr(driver, "_resolved_port", esatm.DEFAULT_PORT),
                source="in-use"))
        return candidates, None

    def _identify_candidates(self, candidates, current_host: str) -> list:
        """
        The device entry of every candidate that answers as a Drobo,
        each host probed once.

        A candidate whose greeting carries malformed XML raises
        ElementTree.ParseError from verify_candidate, and is skipped
        like any other peer that is not a usable Drobo. Until 2026-11-02
        it was not caught, and the agent dropped the /api/discover
        connection without an answer.
        """
        devices_found: list[dict] = []
        hosts_seen: set[str] = set()
        for candidate in candidates:
            if candidate.host in hosts_seen:
                continue
            hosts_seen.add(candidate.host)
            try:
                # No expected_esa_id: we are listing what's out there,
                # not asserting which one is yours. Identity pinning
                # still happens in the driver when you actually connect.
                verified_candidate = discovery.verify_candidate(
                    candidate, timeout=self.DISCOVERY_LISTEN_SECONDS)
            except (OSError, discovery.IdentityMismatchError,
                    esatm.FrameError, ElementTree.ParseError):
                # answered badly or not at all;
                # not a Drobo we can use
                continue
            devices_found.append(self._identified_device_entry(
                candidate, verified_candidate.identity, current_host))
        return devices_found

    @staticmethod
    def _identified_device_entry(candidate, identity: dict,
                                 current_host: str) -> dict:
        """One /api/discover entry, from the device's own claims."""
        return {
            "host": candidate.host,
            "port": candidate.port,
            "found_by": candidate.source,
            "name": identity.get("name", ""),
            "model": identity.get("model", ""),
            "firmware": identity.get("firmware", ""),
            # The serial identifies the box permanently and is what pins
            # a future connection to THIS device. It is not a secret --
            # the Drobo hands it to anyone who connects.
            "esa_id": identity.get("esa_id", ""),
            "is_current": candidate.host == current_host,
        }

    def _simulated_device_entry(self) -> dict:
        """
        The simulated Drobo's /api/discover entry. Flagged `simulated`
        so a front-end can label it honestly rather than letting anyone
        believe they are looking at a real array.
        """
        device = self.monitor.snapshot_dict().get("device", {})
        return {
            "host": "simulated",
            "port": 0,
            "found_by": "mock driver",
            "name": device.get("name", "MockDrobo"),
            "model": device.get("model", "Drobo 5N"),
            "firmware": device.get("firmware", ""),
            "esa_id": device.get("esa_id", "") or "simulated-device",
            "is_current": True,
            "simulated": True,
        }

    @staticmethod
    def _add_manual_host_report(payload: dict, manual_host: str) -> None:
        """
        Say plainly whether the typed address worked. Without this, a
        wrong address and a network with no Drobos produce the same
        empty list, and the person who just typed one has no way to tell
        "nothing there" from "I got a digit wrong".
        """
        payload["manual_host"] = manual_host
        payload["manual_found"] = any(
            device["host"] == manual_host for device in payload["devices"])
        if not payload["manual_found"]:
            payload["manual_error"] = (
                f"Nothing at {manual_host} answered as a Drobo. "
                f"Check the address, that the Drobo is powered on, "
                f"and that this PC is on the same network as it."
            )

    def _serve_shares(self) -> None:
        """
        The shares, ready to open.

        Two device reads rather than one: the share list, and the
        network config for the port-speed check. The network read is
        best-effort -- a missing link report must not cost you the share
        list, which is the part you actually came for.
        """
        # The simulator stands in for the DEVICE, not for this method.
        # Only the fetch is swapped (see _read_config_section); the
        # caching, summarising and link check all still run exactly as
        # they do against real hardware.
        #
        # Doing it the other way -- short-circuiting the whole method
        # for the mock -- is a mistake this file has now made twice. The
        # mock driver is what the tests use to exercise the real paths,
        # so anything that jumps over those paths quietly stops testing
        # them.
        simulated = getattr(self.monitor.driver, "simulate_config", False)
        if simulated:
            host, esa_id, unavailable_body = _SIMULATED_SHARES_HOST, "", None
        else:
            host, esa_id, unavailable_body = self._command_target()
        if unavailable_body:
            return self._send_json(unavailable_body, 503)

        # One caller at a time, and everyone else gets the cached
        # answer. Two browser tabs refreshing must not become four
        # connections to a 2013 embedded box.
        with _ApiRequestHandler._shares_lock:
            cached_payload = self._fresh_cached_payload(
                _ApiRequestHandler._shares_cache, self.SHARES_CACHE_SECONDS)
            if cached_payload is not None:
                return self._send_json(cached_payload)
            try:
                shares_config = self._read_config_section(
                    "shares", host, esa_id, simulated)
            except config_cmd.ConfigError as exc:
                return self._send_json({"error": str(exc)}, 502)
            except OSError as exc:
                return self._send_command_port_unreachable(exc)
            payload = self._shares_payload(
                shares_config, host, esa_id, simulated)
            _ApiRequestHandler._shares_cache = (time.time(), payload)
        return self._send_json(payload)

    def _read_config_section(self, section: str, host: str, esa_id: str,
                             simulated: bool):
        """One config section, from the simulator or the device."""
        if simulated:
            return self.monitor.driver.get_config(section)
        return config_cmd.get_config(
            host, esa_id, section, timeout=self.COMMAND_TIMEOUT_SECONDS)

    def _shares_payload(self, shares_config, host: str, esa_id: str,
                        simulated: bool) -> dict:
        """The /api/shares answer, built from the shares config read."""
        share_summaries = nasd_shares.summarize(shares_config, host)
        link_health_report = {}
        try:
            link_health_report = nasd_shares.link_health(
                self._read_config_section(
                    "network", host, esa_id, simulated))
        except (config_cmd.ConfigError, OSError):
            pass

        # Read from the SAME config document the shares came from, not a
        # second fetch: the command port dislikes connection volume, and
        # two reads could disagree with each other if a write landed in
        # between. Returns None when the list is present but unreadable,
        # which access_advice treats as "don't know" rather than "none".
        accounts = nasd_shares.user_accounts(shares_config)

        return {
            "host": host,
            "shares": share_summaries,
            "accounts": accounts,
            "advice": nasd_shares.access_advice(share_summaries, accounts),
            "link": link_health_report,
        }

    # -- POST: the mock driver's controls ------------------------------

    def _mock_sign_in(self) -> None:
        """
        Check a share login
        against the simulated Drobo (MockDriver.check_sign_in).

        The Windows app sends its Connect dialog here, instead of to
        Windows, only when the agent is running the mock driver. Refused
        with 409 for any other driver, so it can never be pointed at a
        real device. The body is read and dropped like every other:
        nothing here logs it, and the answer carries no part of the
        password.
        """
        driver = self.monitor.driver
        try:
            body = self._read_json_body()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._send_bad_json(exc)
        if not isinstance(driver, MockDriver):
            return self._send_json(
                {"error": "sign-in simulation needs the mock driver"}, 409)
        share = str(body.get("share", "")).strip()
        username = str(body.get("username", "")).strip()
        password = body.get("password", "")
        if not share or not username:
            return self._send_json(
                {"error": "share and username are both required"}, 400)
        if not isinstance(password, str):
            return self._send_json({"error": "password must be a string"}, 400)
        return self._send_json(driver.check_sign_in(share, username, password))

    def _mock_control(self, path: str) -> None:
        """
        /api/mock/<action>[/<argument>]: inject a fault into the
        simulator, or reset it, then poll at once so the alerts it
        causes fire now rather than on the next scheduled poll.
        """
        driver = self.monitor.driver
        if not isinstance(driver, MockDriver):
            return self._send_json(
                {"error": "mock controls need the mock driver"}, 409)
        path_segments = path.split("/")[3:]  # after /api/mock/
        action = path_segments[0] if path_segments else ""
        argument = path_segments[1] if len(path_segments) > 1 else None
        bay_number = (int(argument)
                      if argument is not None and argument.isdigit()
                      else None)
        if not self._apply_mock_action(driver, action, argument, bay_number):
            return self._send_json(
                {"error": "use /api/mock/fail/<bay>, warn/<bay>, pull/<bay>, "
                          "battery/<warning|failed>, power/<warning|failed>, "
                          f"write/<{'|'.join(drivers.WRITE_FAULTS)}> "
                          "or /api/mock/reset"},
                400,
            )
        snapshot = self.monitor.poll_once()
        return self._send_json({"ok": True, "action": action,
                                "bay": bay_number,
                                "state": snapshot.overall_state})

    @staticmethod
    def _apply_mock_action(driver, action: str, argument: str | None,
                           bay_number: int | None) -> bool:
        """
        Apply one mock control; False when the action and its argument
        are not a combination the simulator offers.
        """
        if action == "reset":
            driver.reset()
            return True
        if action in ("fail", "warn", "pull") and bay_number:
            {"fail": driver.fail_bay, "warn": driver.warn_bay,
             "pull": driver.pull_bay}[action](bay_number)
            return True
        if action == "battery" and argument in ("warning", "failed"):
            driver.fail_battery(argument)
            return True
        if action == "power" and argument in ("warning", "failed"):
            driver.fail_power(argument)
            return True
        if action == "write" and argument in drivers.WRITE_FAULTS:
            # Arms how the simulator ENDS a config write, so both UIs
            # can be driven through all four outcomes -- including the
            # one measured on hardware, where the device applies the
            # change and never answers.
            driver.fail_write(argument)
            return True
        return False

    # -- GET: the live event stream ------------------------------------

    def _stream_events(self) -> None:
        """
        Server-sent events: the current snapshot at once, then every
        snapshot and alert the monitor publishes, until the client goes.

        A comment frame goes out after SSE_KEEPALIVE_SECONDS of silence,
        so a quiet connection still carries bytes: proxies and browsers
        drop an idle stream, and a write is how a client that has gone
        away is noticed and its queue released.
        """
        event_queue = self.monitor.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            # Send the current state immediately
            # so a new client isn't blank.
            self._send_server_sent_event(
                {"type": "snapshot", "data": self.monitor.snapshot_dict()})
            while True:
                self._send_next_event_or_keepalive(event_queue)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.monitor.unsubscribe(event_queue)

    def _send_next_event_or_keepalive(self, event_queue) -> None:
        """The next published event, or a keep-alive if none comes."""
        try:
            self._send_server_sent_event(
                event_queue.get(timeout=SSE_KEEPALIVE_SECONDS))
        except queue.Empty:
            self.wfile.write(b": keep-alive\n\n")  # comment frame
            self.wfile.flush()

    def _send_server_sent_event(self, payload: dict) -> None:
        """One SSE frame, its event type taken from payload["type"]."""
        body = json.dumps(payload)
        self.wfile.write(
            (f"event: {payload.get('type', 'message')}\n"
             f"data: {body}\n\n").encode())
        self.wfile.flush()


def build_http_server(config: dict, monitor, photo_store):
    """Build the agent's HTTP server without starting it.

    The caller decides how it runs: run_agent.py serves it on the main
    thread until Ctrl+C, while the test suites serve it on a background
    thread and shut it down when their checks are done. The monitor,
    photo store and token go onto the handler class because
    http.server makes a fresh handler for every connection, so a second
    server built in the same process replaces the first one's.
    """
    _ApiRequestHandler.monitor = monitor
    _ApiRequestHandler.photo_store = photo_store
    _ApiRequestHandler.token = config["agent"]["token"]
    bind_address = config["agent"]["bind"]
    port = int(config["agent"]["port"])
    httpd = ThreadingHTTPServer((bind_address, port), _ApiRequestHandler)
    httpd.daemon_threads = True
    return httpd
