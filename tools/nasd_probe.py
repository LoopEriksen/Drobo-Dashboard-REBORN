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


def hexdump(data: bytes, limit: int = 512) -> list[str]:
    lines = []
    for offset in range(0, min(len(data), limit), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:04x}  {hex_part}  {ascii_part}")
    return lines


def classify_response(data: bytes) -> str | None:
    """Score a response.
    Returns a description if it looks meaningful.
    """
    if not data:
        return None
    if b"<Error>" in data:
        return "contains <Error> -- this is the nasd response format"
    if data.lstrip()[:5] == b"<?xml":
        return "XML declaration"
    if re.match(rb"\s*<[A-Za-z]", data):
        return "starts with an XML element"
    printable = sum(1 for b in data[:64] if 9 <= b <= 126)
    if printable > len(data[:64]) * 0.9:
        return f"printable text: {data[:60]!r}"
    return f"binary, {len(data)} bytes"


def read_available(sock: socket.socket, first_timeout: float = 3.0,
                   idle_timeout: float = 0.6, max_bytes: int = 65536) -> bytes:
    """Read until the peer goes quiet."""
    sock.settimeout(first_timeout)
    received = b""
    try:
        while len(received) < max_bytes:
            piece = sock.recv(8192)
            if not piece:
                break
            received += piece
            sock.settimeout(idle_timeout)
    except (socket.timeout, TimeoutError):
        pass
    except OSError:
        pass
    return received


def connect_to_status_port(host: str,
                           timeout: float = 5.0) -> socket.socket | None:
    try:
        sock = socket.create_connection((host, STATUS_PORT), timeout=timeout)
        sock.settimeout(timeout)
        return sock
    except OSError as exc:
        print(f"  ! could not connect to {host}:{STATUS_PORT} -- {exc}")
        return None


def probe_banner(host: str) -> dict:
    """Connect and say nothing. Does the Drobo speak first?"""
    print("\n== 1. does the server speak first? ==")
    result = {"test": "banner", "bytes": 0}
    sock = connect_to_status_port(host)
    if not sock:
        result["error"] = "connect failed"
        return result
    try:
        data = read_available(sock, first_timeout=4.0)
        result["bytes"] = len(data)
        if data:
            result["hexdump"] = hexdump(data)
            result["verdict"] = classify_response(data)
            print(f"  server sent {len(data)} bytes unprompted -- "
                  f"{result['verdict']}")
            for line in hexdump(data, 128):
                print("    " + line)
        else:
            print("  silence. The client speaks first.")
            result["verdict"] = "silent -- client speaks first"
    finally:
        sock.close()
    return result


def probe_framings(host: str, command_name: str,
                   persistent: bool) -> list[dict]:
    print(f"\n== 2. framing sweep using {command_name} ==")
    if command_name in FORBIDDEN:
        raise SystemExit(
            f"refusing to send {command_name}: it is on the forbidden list")

    results = []
    sock = None
    for body_name, body in candidate_bodies(command_name):
        for framing_name, apply_framing in FRAMINGS:
            payload = apply_framing(body)
            label = f"{body_name}/{framing_name}"
            if not persistent or sock is None:
                if sock:
                    sock.close()
                sock = connect_to_status_port(host)
                if not sock:
                    return results
            attempt = {"body": body_name, "framing": framing_name,
                       "sent": payload[:120].decode("latin1", "replace"),
                       "sent_bytes": len(payload)}
            try:
                sock.sendall(payload)
            except OSError as exc:
                attempt["error"] = f"send failed: {exc}"
                results.append(attempt)
                sock.close()
                sock = None
                continue
            data = read_available(sock, first_timeout=2.5)
            attempt["received_bytes"] = len(data)
            if data:
                attempt["hexdump"] = hexdump(data)
                attempt["text"] = data[:1024].decode("utf-8", "replace")
                attempt["verdict"] = classify_response(data)
                print(f"  {label:34s} -> {len(data):5d} bytes  "
                      f"{attempt['verdict']}")
            else:
                print(f"  {label:34s} -> (nothing)")
            results.append(attempt)
            if not persistent:
                sock.close()
                sock = None
    if sock:
        sock.close()
    return results


def main(argv=None) -> int:
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
    args = parser.parse_args(argv)

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

        answered = [attempt for attempt in report["framings"]
                    if attempt.get("received_bytes")]
        print("\n== summary ==")
        print(f"  {len(answered)} of {len(report['framings'])} "
              "attempts got a reply")
        promising = [attempt for attempt in answered
                     if "Error" in (attempt.get("text") or "")
                     or "XML" in (attempt.get("verdict") or "")]
        if promising:
            print("  most promising:")
            for attempt in promising[:5]:
                print(f"    {attempt['body']}/{attempt['framing']}: "
                      f"{attempt['verdict']}")
        elif answered:
            print("  something answered, but nothing looked like XML. "
                  "Check the hexdumps in the report.")
        else:
            print("  nothing answered. "
                  "Either the framing guesses are all wrong,")
            print("  or nasd expects authentication first. "
                  "Fall back to Wireshark:")
            print(f"    tcp port {STATUS_PORT} and host {args.host}")

    with open(args.out, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2)
    print(f"\nWrote {args.out}")
    print("Keep that file -- a driver can be written from what it recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
