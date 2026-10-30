#!/usr/bin/env python3
"""
nasd_probe.py - work out how to talk to nasd, without Wireshark.

We know from the firmware that a Drobo 5N runs a daemon called nasd on
TCP port 5000, that it speaks XML, and what all 106 of its commands are
called (see docs/protocol-map.md). What we do NOT know is the framing:
whether a request is length-prefixed, newline-terminated,
NUL-terminated, or just raw XML on the socket.

This tool tries each possibility against a real Drobo and records
exactly what comes back.

**Set expectations honestly**: firmware analysis has since shown that
nasd requires a signed binary header and a login exchange before it
accepts any command (see docs/protocol-map.md, "Connection model"). This
probe is therefore unlikely to get a successful command response on its
own. What it *will* do is show how the daemon behaves and how it rejects
each attempt, which narrows the header layout considerably — and it
costs one command to find out.

If it comes back with nothing useful, that is the expected outcome and
the answer is a Wireshark capture of the real Dashboard doing the
handshake.

    py nasd_probe.py 10.0.0.50
    py nasd_probe.py 10.0.0.50 -o docs/captures/nasd-probe.json
    py nasd_probe.py 10.0.0.50 --listen-only

SAFETY
------
This tool only ever sends commands that READ. The command list below is
a hand-picked subset of harmless ones -- version, status, system info.
It will not send anything that writes, formats, mounts, unmounts,
reboots, updates firmware, or touches the disk pack.

It specifically will NEVER send eCmdRunESACommand, eCmdRunCommand,
eCmdSendTMCommand, eCmdRouter, eCmdReadHostBuffer or eCmdWriteHostBuffer
-- those look like arbitrary command execution on a device that will
never get another security patch. They are named here so the list is
explicit, and they are blocked below.

Even so: this talks to the hardware. If you would rather observe than
poke, use --listen-only, which connects and reads without sending
anything.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import struct
import sys
import time

# ---------------------------------------------------------------------------
# Commands we are willing to send. Read-only, all of them.
# ---------------------------------------------------------------------------
SAFE_COMMANDS = [
    "eCmdGetVersion",
    "eCmdGetAllDevicesStatusXML",
    "eCmdGetSysInfo",
    "eCmdGetDevices",
]

# Never sent. Present so the refusal is explicit and greppable.
FORBIDDEN = {
    "eCmdRunESACommand", "eCmdRunCommand", "eCmdSendTMCommand", "eCmdRouter",
    "eCmdReadHostBuffer", "eCmdWriteHostBuffer", "eCmdInstallFirmware",
    "eCmdRevertFirmware", "eCmdFormatDevice", "eCmdFormatLUN", "eCmdReset",
    "eCmdShutdown", "eCmdRestart", "eCmdRepair", "eCmdForceRepair",
    "eCmdSetConfig", "eCmdSetAdmin", "eCmdClearConfig", "eCmdSetFlashConfig",
}

STATUS_PORT = 5000


# ---------------------------------------------------------------------------
# Candidate request bodies. The firmware shows an XML DOM and
# <Error>N</Error> responses, so these are shaped around that.
# ---------------------------------------------------------------------------
def candidate_bodies(command_name: str) -> list[tuple[str, bytes]]:
    """Every request-body shape worth trying, each with a short label.

    Each shape is a guess built from what the firmware gives away (an
    XML DOM, replies of the form <Error>N</Error>), not from a
    capture, so the probe tries them all and lets the replies decide.
    """
    return [
        ("bare-element", f"<{command_name}/>".encode()),
        ("command-element", f"<Command>{command_name}</Command>".encode()),
        ("command-attr", f'<Command name="{command_name}"/>'.encode()),
        ("esa-style",
         f"<ESA><Command>{command_name}</Command></ESA>".encode()),
        ("decl+command",
         f'<?xml version="1.0"?><Command>{command_name}</Command>'.encode()),
        ("tmcmd", f"<TMCmd><Cmd>{command_name}</Cmd></TMCmd>".encode()),
    ]


# ---------------------------------------------------------------------------
# Candidate framings.
#
# IMPORTANT -- read docs/protocol-map.md "Connection model" first.
# Firmware analysis has since shown that nasd expects a binary header
# carrying a SIGNATURE, then a login packet, before it will accept any
# command:
#
#    Command Conn login(from %s): got bad signature.
#    Command Conn login(from %s): did not receive complete login packet.
#    SledCommandListener: No session yet so refusing command connection
#
# So the plain framings below are expected to FAIL. They are kept
# because a rejection is itself information -- how nasd rejects us
# (silence? a reset? an error payload?) narrows down the header. The
# signature-prefixed variants are the ones with a real chance.
#
# The signature is "DRINASD4" (or the older "DRINASD"), found beside the
# SledDiscoveryAgent code in the firmware. Its position and the exact
# header layout are still unknown, so several placements are tried.
# ---------------------------------------------------------------------------
SIGNATURE_DRINASD4 = b"DRINASD4"
SIGNATURE_DRINASD = b"DRINASD"

FRAMINGS = [
    # plain -- expected to be refused,
    # but the manner of refusal is a clue
    ("raw", lambda body: body),
    ("newline", lambda body: body + b"\n"),
    ("nul", lambda body: body + b"\0"),
    ("len32-be", lambda body: struct.pack(">I", len(body)) + body),
    ("len32-le", lambda body: struct.pack("<I", len(body)) + body),
    ("len16-be", lambda body: struct.pack(">H", len(body)) + body),
    # signature-prefixed -- the shapes worth trying
    ("sig4+len32be",
     lambda body: SIGNATURE_DRINASD4 + struct.pack(">I", len(body)) + body),
    ("sig4+len32le",
     lambda body: SIGNATURE_DRINASD4 + struct.pack("<I", len(body)) + body),
    ("sig4+nul+len32be",
     lambda body: (SIGNATURE_DRINASD4 + b"\0" + struct.pack(">I", len(body))
                   + body)),
    ("sig+len32be",
     lambda body: (SIGNATURE_DRINASD + b"\0" + struct.pack(">I", len(body))
                   + body)),
    ("len32be+sig4",
     lambda body: (struct.pack(">I", len(body) + 8) + SIGNATURE_DRINASD4
                   + body)),
    ("sig4+pad8+len32be",
     lambda body: (SIGNATURE_DRINASD4 + b"\0" * 8
                   + struct.pack(">I", len(body)) + body)),
]


# The most one recv() call asks for while draining a reply.
RECEIVE_CHUNK_BYTES = 8192


def hexdump(data: bytes, limit_bytes: int = 512) -> list[str]:
    """Format up to `limit_bytes` of `data` as offset/hex/ASCII lines.

    The report keeps a hexdump rather than the raw bytes because a
    framing guess that gets refused may be refused in binary, and the
    dump stays readable in JSON.
    """
    lines = []
    for offset in range(0, min(len(data), limit_bytes), 16):
        row = data[offset:offset + 16]
        hex_part = " ".join(f"{byte_value:02x}" for byte_value in row)
        hex_part = hex_part.ljust(47)
        ascii_part = "".join(chr(byte_value) if 32 <= byte_value < 127
                             else "." for byte_value in row)
        lines.append(f"{offset:04x}  {hex_part}  {ascii_part}")
    return lines


def classify_response(reply_bytes: bytes) -> str | None:
    """Describe a reply in a few words, or None for no reply at all.

    A verdict string rather than a yes/no, because the summary ranks
    the "promising" attempts by what the verdict says (XML, an <Error>
    element) and a person reading the report wants the reason too.
    """
    if not reply_bytes:
        return None
    if b"<Error>" in reply_bytes:
        return "contains <Error> -- this is the nasd response format"
    if reply_bytes.lstrip()[:5] == b"<?xml":
        return "XML declaration"
    if re.match(rb"\s*<[A-Za-z]", reply_bytes):
        return "starts with an XML element"
    printable_count = sum(1 for byte_value in reply_bytes[:64]
                          if 9 <= byte_value <= 126)
    if printable_count > len(reply_bytes[:64]) * 0.9:
        return f"printable text: {reply_bytes[:60]!r}"
    return f"binary, {len(reply_bytes)} bytes"


def _receive_until_idle(sock: socket.socket, chunks: list[bytes],
                        idle_timeout_seconds: float,
                        max_bytes: int) -> None:
    """Append what `sock` sends to `chunks` until it closes or fills.

    Split out of read_available so that a timeout, which is how a
    quiet peer ends the read, can be caught there while the chunks
    received before it are kept.
    """
    received_count = 0
    while received_count < max_bytes:
        chunk = sock.recv(RECEIVE_CHUNK_BYTES)
        if not chunk:
            return
        chunks.append(chunk)
        received_count += len(chunk)
        sock.settimeout(idle_timeout_seconds)


def read_available(sock: socket.socket, first_timeout_seconds: float = 3.0,
                   idle_timeout_seconds: float = 0.6,
                   max_bytes: int = 65536) -> bytes:
    """Read until the peer goes quiet, and return what it sent.

    With no known framing there is no length to read up to, so the
    only end of a reply is silence: wait `first_timeout_seconds` for
    the first bytes, then `idle_timeout_seconds` after each later
    chunk. A timeout or socket error ends the read; it is not an
    error here.
    """
    sock.settimeout(first_timeout_seconds)
    chunks: list[bytes] = []
    try:
        _receive_until_idle(sock, chunks, idle_timeout_seconds, max_bytes)
    except (socket.timeout, TimeoutError):
        pass
    except OSError:
        pass
    return b"".join(chunks)


def connect_to_status_port(host: str, timeout_seconds: float = 5.0
                           ) -> socket.socket | None:
    """Open a connection to nasd's port, or print why not and give None.

    None rather than an exception, so a probe that loses the device
    part-way still writes down the attempts it already made.
    """
    try:
        sock = socket.create_connection((host, STATUS_PORT),
                                        timeout=timeout_seconds)
        sock.settimeout(timeout_seconds)
        return sock
    except OSError as exc:
        print(f"  ! could not connect to {host}:{STATUS_PORT} -- {exc}")
        return None


def probe_banner(host: str) -> dict:
    """Connect and say nothing. Does the Drobo speak first?"""
    print("\n== 1. does the server speak first? ==")
    banner_result = {"test": "banner", "bytes": 0}
    sock = connect_to_status_port(host)
    if not sock:
        banner_result["error"] = "connect failed"
        return banner_result
    try:
        reply_bytes = read_available(sock, first_timeout_seconds=4.0)
        banner_result["bytes"] = len(reply_bytes)
        _record_banner(banner_result, reply_bytes)
    finally:
        sock.close()
    return banner_result


def _record_banner(banner_result: dict, reply_bytes: bytes) -> None:
    """Add what the unprompted read showed to the result, and say it.

    Silence is a result worth recording too: it means the client has
    to speak first.
    """
    if not reply_bytes:
        print("  silence. The client speaks first.")
        banner_result["verdict"] = "silent -- client speaks first"
        return
    banner_result["hexdump"] = hexdump(reply_bytes)
    banner_result["verdict"] = classify_response(reply_bytes)
    print(f"  server sent {len(reply_bytes)} bytes unprompted -- "
          f"{banner_result['verdict']}")
    for line in hexdump(reply_bytes, 128):
        print("    " + line)


def _framed_attempts(command_name: str):
    """Yield (body name, framing name, payload) for every combination.

    Bodies are the outer loop and framings the inner one, which is the
    order the report lists them in.
    """
    for body_name, body in candidate_bodies(command_name):
        for framing_name, apply_framing in FRAMINGS:
            yield body_name, framing_name, apply_framing(body)


def _ensure_connection(sock: socket.socket | None, host: str,
                       persistent: bool) -> socket.socket | None:
    """Return the socket the next attempt should use.

    With --persistent an open socket is reused, to see whether nasd
    keeps a conversation going after a bad request; otherwise every
    attempt gets a fresh connection, so one refusal cannot colour the
    next. None means the connection could not be made.
    """
    if persistent and sock is not None:
        return sock
    if sock:
        sock.close()
    return connect_to_status_port(host)


def _send_one_attempt(sock: socket.socket, body_name: str,
                      framing_name: str,
                      payload: bytes) -> tuple[dict, bool]:
    """Send one framed payload, read the reply, and describe both.

    Returns the attempt's record and whether the send went through;
    after a failed send the caller drops the socket, since nothing
    more can be read from it.
    """
    attempt = {"body": body_name, "framing": framing_name,
               "sent": payload[:120].decode("latin1", "replace"),
               "sent_bytes": len(payload)}
    try:
        sock.sendall(payload)
    except OSError as exc:
        attempt["error"] = f"send failed: {exc}"
        return attempt, False
    reply_bytes = read_available(sock, first_timeout_seconds=2.5)
    attempt["received_bytes"] = len(reply_bytes)
    label = f"{body_name}/{framing_name}"
    if not reply_bytes:
        print(f"  {label:34s} -> (nothing)")
        return attempt, True
    attempt["hexdump"] = hexdump(reply_bytes)
    attempt["text"] = reply_bytes[:1024].decode("utf-8", "replace")
    attempt["verdict"] = classify_response(reply_bytes)
    print(f"  {label:34s} -> {len(reply_bytes):5d} bytes  "
          f"{attempt['verdict']}")
    return attempt, True


def probe_framings(host: str, command_name: str,
                   persistent: bool) -> list[dict]:
    """Send `command_name` in every body shape and framing, and record.

    The command is checked against FORBIDDEN before anything is sent,
    even though main only passes SAFE_COMMANDS: the refusal is meant to
    hold for any caller. If the device stops accepting connections
    part-way, the attempts made so far are returned.
    """
    print(f"\n== 2. framing sweep using {command_name} ==")
    if command_name in FORBIDDEN:
        raise SystemExit(
            f"refusing to send {command_name}: it is on the forbidden list")

    results = []
    sock = None
    for body_name, framing_name, payload in _framed_attempts(command_name):
        sock = _ensure_connection(sock, host, persistent)
        if not sock:
            return results
        attempt, sent = _send_one_attempt(sock, body_name, framing_name,
                                          payload)
        results.append(attempt)
        if not sent or not persistent:
            sock.close()
            sock = None
    if sock:
        sock.close()
    return results


def _parse_arguments(argv) -> argparse.Namespace:
    """Read the command line (the module docstring is the --help)."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="the Drobo's IP address")
    parser.add_argument("-o", "--out", default="nasd-probe.json")
    parser.add_argument("--command", default="eCmdGetVersion",
                        help="which safe command to probe with; "
                             f"one of {SAFE_COMMANDS}")
    parser.add_argument("--listen-only", action="store_true",
                        help="connect and read, but never send anything")
    parser.add_argument("--persistent", action="store_true",
                        help="reuse one connection for every attempt")
    return parser.parse_args(argv)


def _print_framing_summary(framings: list[dict], host: str) -> None:
    """Say how many attempts got a reply and which look most promising.

    An attempt is "promising" when its reply mentions Error or its
    verdict mentions XML, because those are the shapes the firmware
    says nasd answers in.
    """
    answered = [attempt for attempt in framings
                if attempt.get("received_bytes")]
    print("\n== summary ==")
    print(f"  {len(answered)} of {len(framings)} "
          "attempts got a reply")
    promising = [attempt for attempt in answered
                 if "Error" in (attempt.get("text") or "")
                 or "XML" in (attempt.get("verdict") or "")]
    if promising:
        print("  most promising:")
        for attempt in promising[:5]:
            print(f"    {attempt['body']}/{attempt['framing']}: "
                  f"{attempt['verdict']}")
        return
    if answered:
        print("  something answered, but nothing looked like XML. "
              "Check the hexdumps in the report.")
        return
    print("  nothing answered. "
          "Either the framing guesses are all wrong,")
    print("  or nasd expects authentication first. "
          "Fall back to Wireshark:")
    print(f"    tcp port {STATUS_PORT} and host {host}")


def _write_report(report: dict, report_path: str) -> None:
    """Write the report as JSON and say where it went."""
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2)
    print(f"\nWrote {report_path}")
    print("Keep that file -- a driver can be written from what it recorded.")


def main(argv=None) -> int:
    """Probe the banner, then (unless --listen-only) every framing.

    The command is checked against the read-only allow-list before the
    first connection, so a typo never reaches the device.
    """
    args = _parse_arguments(argv)

    if args.command not in SAFE_COMMANDS:
        raise SystemExit(
            f"{args.command!r} is not in the read-only allow-list.\n"
            f"Allowed: {', '.join(SAFE_COMMANDS)}")

    print(f"Probing nasd at {args.host}:{STATUS_PORT}")
    print("Only read-only commands will be sent.")

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": args.host, "port": STATUS_PORT, "command": args.command,
        "banner": probe_banner(args.host),
    }

    if args.listen_only:
        print("\n--listen-only: stopping without sending anything.")
    else:
        report["framings"] = probe_framings(
            args.host, args.command, args.persistent)
        _print_framing_summary(report["framings"], args.host)

    _write_report(report, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
