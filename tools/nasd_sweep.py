#!/usr/bin/env python3
"""
nasd_sweep.py - measure exactly which nasd commands answer
after a plain login.

This project used to believe (docs/protocol-map.md's now-corrected "Two
privilege tiers" section) that a Drobo 5N's command channel (TCP 5001)
had two gates: a plain login (just the device serial, sent twice -- see
config_cmd.py) and, behind that, a second, unreversed authentication
step (characterized but never confirmed as required -- see
docs/crypto-handshake.md). That was wrong for at least one important
case: eCmdGetSysInfo (temperature/uptime) needs no second step at all --
what looked like a gate was a parsing bug in this project's own code,
reading for a literal <Temperature> element when the reply nests an
entity-escaped XML document instead (see sysinfo.py). eCmdGetConfig is
proven to work with login alone, and this sweep's own results
(docs/command-surface.md) found several more commands answering under a
plain login too. This tool measures, empirically, how many of the other
102 commands answer that way -- so the project knows exactly how much of
the original Dashboard can be rebuilt *today*, without needing to
reverse anything further.

It reuses config_cmd.py's login/framing verbatim (_build_frame,
_login_payload, _read_frame, the TYPE_* constants) rather than
reinventing them -- that module is the one piece of this codebase
already confirmed against real hardware.

    py tools/nasd_sweep.py 10.0.0.5

SAFETY -- read this before touching ALLOWLIST
-----------------------------------------------------------------------------
This sends bytes to a real Drobo, which may hold the only copy of
somebody's data. Two traps in the command table make "just send
everything that sounds like a read" unsafe:

  - eCmdForceSingleInitiator (68) sounds like a query. It is a write.
  - eCmdGetNextUniqueLUNID (5) starts with "Get". It *allocates* an id
    as a side effect of asking.

So this tool uses a hardcoded ALLOWLIST of command NAMES -- never an
exclusion rule, never a "starts with Get" heuristic, never a numeric
range. Every name is resolved to its id through commands.COMMANDS[name]
at runtime; no integer command id is ever hardcoded here.
assert_allowlist_safe() runs at import time and refuses to let this
module load if the allowlist and commands.DO_NOT_SEND overlap, if a name
has no confirmed id, or if the list has a duplicate.

The allowlist itself is fixed by design review and is not meant to grow
by editing this file casually. Deliberately excluded, and why:

  - eCmdGetUpdate       -- may make the device dial a now-dead vendor
                           server and hang waiting for a response that
                           will never come.
  - eCmdGetNextUniqueLUNID -- allocates an id; not idempotent, not a
    read.
  - eCmdCacheBattery    -- the name is not unambiguously a read; could
                           be a battery self-test rather than a status
                           query.
  - eCmdModeSense       -- raw SCSI passthrough.
  - eCmdGetSavedFileContent, eCmdDownloadConfigFile,
    eCmdDownloadAllConfigFiles, eCmdGetAppDataFile -- file-content
                           pulls with unknown parameters; this tool
                           sends <Params></Params> empty and never
                           guesses parameters, so anything that needs a
                           filename/handle to mean anything is left out.
  - eCmdGetNASUsernamePassword -- the name says it hands back a
    credential.
  - everything else in the 103-entry table, including the whole
    DO_NOT_SEND group -- now both the arbitrary-execution escape hatches
    AND the destructive group (format, firmware flash, reset, repair,
    restart, every eCmdSet*, and more -- see commands.py) -- and every
    write/format/mount/reboot/firmware command not already covered by
    DO_NOT_SEND.

Every command in the allowlist is sent with an empty <Params></Params>
body -- this tool never guesses parameters for a command.

Each command gets its own fresh TCP connection and a short timeout, with
a small pause between commands so the sweep does not hammer the device.
If three commands in a row time out, the whole sweep stops and reports
that -- back-to-back timeouts are treated as a sign the device may be
unhappy, not something to push through.

After the sweep, the tool reconnects to TCP 5000 and confirms the device
still volunteers its normal <ESATMUpdate> greeting, and says so
explicitly.

REDACTION -- also mandatory
-----------------------------------------------------------------------------
An earlier version of this note claimed the plain login was enough to
make the device hand back a plaintext admin password from
eCmdGetAdminConfig. That was never measured, and it is wrong: measured
2026-07-26 on this project's firmware 4.3.1, <Password> comes back as
sixteen *identical* characters (a mask) and <EncryptedPassword> as a
single character -- see config_cmd.py's module docstring for the full
measurement. eCmdGetAdmin and eCmdGetAdminConfig stay in the allowlist
because the project needs to know *whether* they answer -- and every
response this tool receives is still run through the same
credential-stripping logic as config_cmd._strip_secrets() before it is
printed, logged, or written to any file, with no exception, regardless
of what this one firmware happened to send back. One firmware, one
device, one config section checked is not a promise about every
firmware, every model, or every section -- so the redaction stays. Raw
(unredacted) response bytes are never written to disk at all, and never
printed. The only file this tool writes is a JSON report under the OS
temp/scratch directory, outside the repository, and even that file only
ever holds redacted text -- it may still contain the device serial, disk
serials and the LAN IP (useful for debugging the sweep), but never a
password/secret/credential/key/token field, because those are stripped
before the record is ever created.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from xml.etree import ElementTree

# tools/, agent/ and sdk/ are siblings. The Drobo protocol lives in the
# SDK (sdk/drobo_nasd) -- the WORKING reference already confirmed
# against real hardware -- so point at that rather than reimplementing
# any of it.
_SDK_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "sdk")
sys.path.insert(0, os.path.abspath(_SDK_DIR))

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import esatm  # noqa: E402

DEFAULT_STATUS_PORT = 5000
# 5001, confirmed in config_cmd.py
DEFAULT_COMMAND_PORT = config_cmd.DEFAULT_PORT

# This many timeouts in a row stop the sweep: back-to-back silence is
# read as the device being unhappy, not something to push through.
MAX_CONSECUTIVE_TIMEOUTS = 3
# How long main waits for the status greeting that carries the serial.
GREETING_TIMEOUT_SECONDS = 8.0

# ---------------------------------------------------------------------------
# THE allowlist. See the module docstring for what is excluded and why.
# Do not add to this list without redoing the same-level safety review.
# ---------------------------------------------------------------------------
ALLOWLIST: list[str] = [
    "eCmdGetAdmin", "eCmdGetVersion", "eCmdGetDimming", "eCmdGetDiskPackId",
    "eCmdGetKernelInterfaceInfo", "eCmdGetConfig", "eCmdGetFirmwareInfo",
    "eCmdGetDroboSyncSettings", "eCmdGetDroboSyncSummaryLog",
    "eCmdGetDroboSyncDetailedLog", "eCmdGetDroboSyncLog",
    "eCmdGetReservedDriveLetters", "eCmdGetDemoModeInfo",
    "eCmdGetUserEmailSettings",
    "eCmdGetDevices", "eCmdIsAutoDiscoveryEnabled",
    "eCmdGetManualDiscoveryIPList",
    "eCmdIsSCSIConnAvailable", "eCmdGetSysInfo", "eCmdGetPerformance",
    "eCmdGetFanInfo",
    "eCmdGetPowerInfo", "eCmdGetControllerCard", "eCmdGetExpanderCard",
    "eCmdGetOSInfo",
    "eCmdGetTMDiagFilePath", "eCmdGetLunInitatorExtraInfo",
    "eCmdGetDroboAppsStatus",
    "eCmdGetAdminConfig", "eCmdGetSharesConfig", "eCmdGetEventLogs",
    "eCmdGetSessionIPAddress", "eCmdGetAllDevicesStatusXML",
    "eCmdGetRegisteredEmail",
    "eCmdGetUserGroupList", "eCmdGetUserEventLog",
    "eCmdGetDeviceNamesToShutdown",
    "eCmdGetDASDeviceNamesWithMountedVolumes",
    "eCmdGetDeviceMountedVolumesCount",
]


class SweepSafetyError(Exception):
    """The allowlist failed a safety check.
    Never caught -- meant to abort.
    """


def assert_allowlist_safe(allowlist: list[str] = ALLOWLIST) -> None:
    """
    Everything this module is allowed to send is checked here, at import
    time, before a socket is ever opened. Any failure aborts the whole
    program -- there is no code path that sends a command without this
    having passed first.
    """
    if len(set(allowlist)) != len(allowlist):
        duplicates = sorted({command_name for command_name in allowlist
                             if allowlist.count(command_name) > 1})
        raise SweepSafetyError(
            f"ALLOWLIST has duplicate entries: {duplicates}")

    overlap = sorted(set(allowlist) & set(commands.DO_NOT_SEND))
    if overlap:
        raise SweepSafetyError(
            "ALLOWLIST overlaps commands.DO_NOT_SEND -- "
            f"refusing to run: {overlap}"
        )

    unknown = sorted(command_name for command_name in allowlist
                     if command_name not in commands.COMMANDS)
    if unknown:
        raise SweepSafetyError(
            f"ALLOWLIST contains names with no confirmed numeric id: {unknown}"
        )


assert_allowlist_safe()


# ---------------------------------------------------------------------------
# One command, one fresh connection.
# ---------------------------------------------------------------------------
def _unwrap_escaped(
        element: ElementTree.Element) -> ElementTree.Element | None:
    """
    If this element's TEXT is really an entity-escaped XML document,
    parse it.

    This firmware sometimes answers with a document escaped into a text
    node (`&lt;Temperature&gt;42&lt;/Temperature&gt;`) rather than with
    real child elements. eCmdGetSysInfo does exactly this -- and because
    the first sweep only walked child elements, its whole reply counted
    as zero fields. That is how temperature came to be recorded as
    unobtainable and blamed on a crypto gate that has nothing to do with
    it.

    So the sweep now looks inside. Returns the parsed inner root, or
    None if the text is not an XML document.

    Note ElementTree has already turned `&lt;` back into `<` by the time
    we see the text, so this looks for a literal angle bracket, not for
    the entity.
    """
    text = (element.text or "").strip()
    if not text or not text.startswith("<"):
        return None
    inner_document = text
    if inner_document.startswith("<?xml"):
        declaration_end = inner_document.find("?>")
        if declaration_end != -1:
            inner_document = inner_document[declaration_end + 2:].strip()
    try:
        return ElementTree.fromstring(inner_document)
    except ElementTree.ParseError:
        return None


def _collect_tags(element: ElementTree.Element) -> set[str]:
    """Every element tag in this tree, inner documents included.

    An escaped inner document is walked as if it were ordinary
    children, because the first sweep did not and so counted
    eCmdGetSysInfo's whole reply as zero fields. Its tags are prefixed
    with the outer tag, and "<tag>/[escaped]" marks that the reply
    needed unwrapping, so the report shows where it happened.
    """
    tags = {element.tag}
    inner = _unwrap_escaped(element)
    if inner is not None:
        tags.add(f"{element.tag}/[escaped]")
        tags |= {f"{element.tag}/{tag}" for tag in _collect_tags(inner)}
    for child in element:
        tags |= _collect_tags(child)
    return tags


def _has_escaped_document(element: ElementTree.Element) -> bool:
    """True if any element in this tree
    hides an escaped XML document.
    """
    return any(_unwrap_escaped(node) is not None for node in element.iter())


def _redact_and_summarize(body: bytes) -> dict:
    """
    Turn a raw response body into a report-safe record: redact every
    credential-looking element BEFORE anything else happens to the data,
    then keep only what is safe to carry around -- the redacted value
    tree (may still hold serials/IPs, never secrets) and the plain set
    of element tag names (never carries a value at all).
    """
    text = body.rstrip(b"\x00").rstrip()
    if not text:
        return {"empty": True}
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        return {"parse_error": str(exc)}

    # Redact first. Nothing below this line ever sees an unredacted
    # secret. BOTH passes: one for real child elements, one for a
    # credential hidden inside an escaped inner document -- see
    # config_cmd for why that second pass has to exist.
    has_escaped_document = _has_escaped_document(root)
    config_cmd._strip_secrets(root)
    config_cmd._redact_escaped_document(root)

    return {
        "tags": sorted(_collect_tags(root)),
        "redacted_value": config_cmd._element_to_dict(root),
        "escaped_document": has_escaped_document,
    }


def _log_in(sock: socket.socket, esa_id: str) -> dict | None:
    """Send the plain login on `sock`; None if nasd accepted it.

    Otherwise the failed record itself, in the same shape
    send_one_command returns, so the caller can hand it straight back.
    """
    try:
        sock.sendall(config_cmd._build_frame(
            config_cmd.TYPE_LOGIN, config_cmd._login_payload(esa_id)))
        frame_type, _ = config_cmd._read_frame(sock)
    except (TimeoutError, socket.timeout):
        return {"status": "timeout", "phase": "login"}
    except (config_cmd.ConfigError, OSError) as exc:
        return {"status": "refused", "phase": "login", "detail": str(exc)}

    if frame_type != config_cmd.TYPE_LOGIN_OK:
        return {"status": "refused", "phase": "login",
                "detail": f"unexpected type 0x{frame_type:02x}"}
    return None


def _exchange_command(sock: socket.socket, esa_id: str, command_id: int
                      ) -> tuple[dict | None, int, bytes]:
    """Send one command with empty <Params></Params> and read one frame.

    Returns (failed record, 0, b"") when the send or the read fails,
    otherwise (None, frame type, body). The parameters are always
    empty, because this tool never guesses what a command needs.
    """
    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><ESAID>{esa_id}</ESAID>"
        f"<Params></Params></TMCmd>").encode() + b"\x00"
    try:
        sock.sendall(config_cmd._build_frame(config_cmd.TYPE_COMMAND,
                                             request_payload))
        frame_type, body = config_cmd._read_frame(sock)
    except (TimeoutError, socket.timeout):
        return {"status": "timeout", "phase": "command"}, 0, b""
    except (config_cmd.ConfigError, OSError) as exc:
        return ({"status": "refused", "phase": "command",
                 "detail": str(exc)}, 0, b"")
    return None, frame_type, body


def _record_reply(frame_type: int, body: bytes,
                  elapsed_seconds: float) -> dict:
    """Turn one command's reply frame into its (redacted) record.

    Every answered body goes through _redact_and_summarize, so nothing
    leaves this function holding an unredacted secret.
    """
    if frame_type != config_cmd.TYPE_RESULT:
        return {"status": "refused", "phase": "command",
                "detail": f"unexpected type 0x{frame_type:02x}",
                "elapsed_s": elapsed_seconds}
    if not body or not body.rstrip(b"\x00").strip():
        return {"status": "empty", "elapsed_s": elapsed_seconds}

    summary = _redact_and_summarize(body)
    summary["status"] = "answered"
    summary["elapsed_s"] = elapsed_seconds
    return summary


def send_one_command(host: str, port: int, esa_id: str, command_id: int,
                     timeout: float) -> dict:
    """
    Fresh TCP connection: login, send exactly one command with empty
    <Params></Params>, read exactly one response. Never reuses a socket
    across commands -- that is deliberate, so one command's misbehavior
    can never leak state into the next.

    `timeout` is in seconds and covers the connect and each read. The
    record's "elapsed_s" runs from before the connect to the end of
    the reply.
    """
    started_at = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return {"status": "refused", "phase": "connect", "detail": str(exc)}

    sock.settimeout(timeout)
    try:
        login_failure = _log_in(sock, esa_id)
        if login_failure is not None:
            return login_failure

        command_failure, frame_type, body = _exchange_command(
            sock, esa_id, command_id)
        if command_failure is not None:
            return command_failure

        elapsed_seconds = time.monotonic() - started_at
        return _record_reply(frame_type, body, elapsed_seconds)
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# The sweep itself.
# ---------------------------------------------------------------------------
def _print_record_line(record: dict) -> None:
    """Print one command's outcome under its "[n/N] name" line.

    An escaped inner document is called out, because those are the
    replies the first sweep could not see into.
    """
    status = record["status"]
    tag_count = len(record.get("tags", []))
    detail_suffix = f", {tag_count} fields" if status == "answered" else ""
    if record.get("escaped_document"):
        detail_suffix += ("  [escaped inner document -- "
                          "invisible to the first sweep]")
    print(f"    -> {status}{detail_suffix}", flush=True)


def run_sweep(host: str, esa_id: str, *, port: int = DEFAULT_COMMAND_PORT,
              timeout: float = 6.0, pause: float = 0.4,
              names: list[str] = ALLOWLIST) -> dict:
    """Send every command in `names` (one fresh login each), and record.

    A `pause` between commands so the sweep does not hammer the device,
    and MAX_CONSECUTIVE_TIMEOUTS in a row stop it, since back-to-back
    silence may mean the device is unhappy. `timeout` and `pause` are
    in seconds; `names` defaults to ALLOWLIST, checked at import.
    """
    results = []
    consecutive_timeouts = 0
    aborted = False

    for position, command_name in enumerate(names):
        # allowlist is pre-validated; always present
        command_id = commands.COMMANDS[command_name]
        print(f"[{position + 1}/{len(names)}] {command_name} "
              f"(id {command_id}) ...", flush=True)
        record = send_one_command(host, port, esa_id, command_id, timeout)
        record["name"] = command_name
        record["cmd_id"] = command_id
        results.append(record)
        _print_record_line(record)

        if record["status"] == "timeout":
            consecutive_timeouts += 1
        else:
            consecutive_timeouts = 0
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            print("\n!! three consecutive timeouts -- stopping the sweep. "
                  "The device may be unhappy; not sending the rest.",
                  flush=True)
            aborted = True
            break

        if position < len(names) - 1:
            time.sleep(pause)

    return {"results": results, "aborted": aborted, "sent": len(results),
            "planned": len(names)}


def verify_device_healthy(host: str, port: int = DEFAULT_STATUS_PORT,
                          timeout: float = 8.0) -> dict:
    """Reconnect to the status port and confirm the normal greeting
    still arrives. Read-only; this is the post-sweep health check."""
    try:
        payload = esatm.fetch_greeting(host, port=port, timeout=timeout)
    except (OSError, esatm.FrameError) as exc:
        return {"healthy": False, "detail": str(exc)}
    try:
        identity = esatm.parse_identity(payload)
    except ElementTree.ParseError as exc:
        return {"healthy": False, "detail": f"greeting did not parse: {exc}"}
    return {"healthy": bool(identity.get("esa_id")),
            "detail": "ESATMUpdate greeting received"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Read the command line (the module docstring is the --help)."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="the Drobo's IP address")
    parser.add_argument("--command-port", type=int,
                        default=DEFAULT_COMMAND_PORT)
    parser.add_argument("--status-port", type=int, default=DEFAULT_STATUS_PORT)
    parser.add_argument("--timeout", type=float, default=6.0,
                        help="per-command timeout, seconds")
    parser.add_argument("--pause", type=float, default=0.4,
                        help="pause between commands, seconds")
    parser.add_argument("-o", "--out", default=None,
                        help="where to write the redacted JSON report "
                        "(default: a scratch path OUTSIDE the repo)")
    return parser.parse_args(argv)


def _default_report_path() -> str:
    """Where the report goes when -o is not given.

    In ~/.nasd_sweep_scratch if that folder exists, else the home
    folder: either way outside the repository, so the report is never
    committed by accident.
    """
    scratch_dir = os.path.join(
        os.path.expanduser("~"), ".nasd_sweep_scratch")
    return (
        os.path.join(scratch_dir, "sweep-raw.json")
        if os.path.isdir(scratch_dir)
        else os.path.join(os.path.expanduser("~"), "sweep-raw.json"))


def _fetch_esa_id(host: str, status_port: int) -> str:
    """Read the device serial from the status greeting (no login).

    The command channel's login carries the serial, so the sweep cannot
    start without it; an empty one aborts rather than log in blind. A
    greeting that cannot be read -- a refused connect, a frame that is
    not a Drobo's, XML that does not parse -- stops the run with a plain
    message, where it used to end in a traceback. Nothing has been sent
    to the command port at this point.
    """
    print(f"Fetching identity from {host}:{status_port} "
          "(no login needed) ...")
    try:
        greeting = esatm.fetch_greeting(
            host, port=status_port, timeout=GREETING_TIMEOUT_SECONDS)
        identity = esatm.parse_identity(greeting)
    except (OSError, esatm.FrameError, ElementTree.ParseError) as exc:
        raise SystemExit(f"could not read the Drobo's greeting at "
                         f"{host}:{status_port}: {exc}")
    esa_id = identity.get("esa_id", "")
    if not esa_id:
        raise SystemExit(
            "could not read mESAID from the status greeting; aborting")
    print(f"Got esa_id (len {len(esa_id)}). "
          "Not printing it -- treat it as sensitive.")
    return esa_id


def _print_summary(sweep: dict) -> None:
    """Count the records by status, and say if the sweep was cut short.

    All four statuses are printed, zeros included, so two runs line up.
    """
    status_counts: dict[str, int] = {}
    for record in sweep["results"]:
        status_counts[record["status"]] = (
            status_counts.get(record["status"], 0) + 1)
    print("\n== summary ==")
    for status in ("answered", "empty", "refused", "timeout"):
        print(f"  {status:10s} {status_counts.get(status, 0)}")
    if sweep["aborted"]:
        print(f"  sweep ABORTED after {sweep['sent']} of "
              f"{sweep['planned']} commands")


def _probe_report_file(report_path: str) -> None:
    """Open the report file for writing once, so a refusal comes now.

    A file made only by this probe is removed again, so a run that
    stops before the report is written leaves no empty report file; a
    file that was already there is opened for appending and left as it
    was. A folder _prepare_report_path made for the report stays, empty,
    because removing folders is not this probe's job.
    Raises OSError (IsADirectoryError, PermissionError, ...) when the
    path cannot be written.
    """
    existed_before = os.path.exists(report_path)
    with open(report_path, "a", encoding="utf-8"):
        pass
    if not existed_before:
        os.remove(report_path)


def _prepare_report_path(requested_path: str | None) -> str | None:
    """The report's absolute path, proven writable; None (said why).

    Settled before the device is contacted, because the report is the
    sweep's only record. It used to be settled only after every command
    had been sent, so a folder that could not be made lost the whole
    run -- and a bare file name (`-o report.json`) did too, since its
    folder name is "" and os.makedirs("") fails. Making the folder was
    not enough on its own: an -o that names a folder, or a folder that
    cannot be written, failed the same way after the sweep, so the
    file itself is opened here too.
    """
    report_path = os.path.abspath(requested_path or _default_report_path())
    try:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        _probe_report_file(report_path)
    except OSError as exc:
        print(f"Cannot write the report to {report_path}: {exc}")
        return None
    return report_path


def _write_report(report_path: str, report: dict) -> None:
    """Write the redacted report as JSON and say where it went.

    `report_path` comes from _prepare_report_path, so it is absolute
    and its folder exists; the makedirs here only covers a folder
    removed while the sweep ran.
    """
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2)
    print(f"\nRedacted report written to {report_path}")
    print("(outside the repo -- this file is not meant to be committed)")


def main(argv: list[str] | None = None) -> int:
    """Read the serial, sweep the allowlist, check health, write report.

    The health check runs after the sweep whatever happened, because a
    device that stopped greeting is the one thing a sweep must report.
    The report's folder is made and its file opened before the device
    is contacted, so a path that cannot be written costs nothing (exit
    2).
    """
    args = _parse_arguments(argv)

    report_path = _prepare_report_path(args.out)
    if report_path is None:
        return 2

    esa_id = _fetch_esa_id(args.host, args.status_port)

    print(f"\nSending {len(ALLOWLIST)} allow-listed commands to "
          f"{args.host}:{args.command_port}, one fresh login each, "
          f"{args.timeout}s timeout, {args.pause}s pause between.\n")

    sweep = run_sweep(args.host, esa_id, port=args.command_port,
                      timeout=args.timeout, pause=args.pause)

    print("\nVerifying the device is still healthy on the status port ...")
    health = verify_device_healthy(args.host, port=args.status_port)
    print(f"  status-port health check: "
          f"{'OK -- greeting received' if health['healthy'] else 'PROBLEM'} "
          f"({health['detail']})")

    _print_summary(sweep)

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": args.host,
        "command_port": args.command_port,
        "status_port": args.status_port,
        "sweep": sweep,
        "post_sweep_health": health,
    }
    _write_report(report_path, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
