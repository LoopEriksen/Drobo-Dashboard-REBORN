#!/usr/bin/env python3
"""
nasd_tap.py - sit between Drobo Dashboard and the Drobo, and write down
everything they say to each other.

WHY THIS EXISTS
---------------
This tool was built while the client->device request framing was still
the project's biggest unknown: we knew what the Drobo *says* (it pushes
a DRINASD status greeting the moment you connect) but not how to *ask it
questions*. That framing has since been recovered -- directly, by
sending commands ourselves and reading the replies, not by tapping
Dashboard -- see docs/protocol-map.md, "THE COMMAND PROTOCOL -- SOLVED",
and sdk/drobo_nasd/config_cmd.py / sysinfo.py for the working
implementation. So this tool is no longer the way to learn the protocol.

What it can still do: watch whether Dashboard itself ever gets a real
answer out of the battery/fan/PSU/performance screens on this hardware.
Our own sweep (tools/nasd_sweep.py, docs/command-surface.md) sends those
commands directly and mostly gets empty replies or timeouts; tapping
Dashboard would show whether the *original* client fares any
differently, which would settle whether that's a sensor this chassis
lacks or a request shape we're still getting wrong. Nobody has run it
for that purpose yet.

The obvious route is Wireshark, but capturing packets on Windows needs
the Npcap kernel driver installed and an elevated shell. This tool needs
neither. It is an ordinary userspace TCP relay: Dashboard connects to
it, it connects to the real Drobo, and it copies bytes between them
while logging both directions.

Because it sees the stream *after* TCP reassembly, the output is cleaner
than a packet capture -- no retransmits, no segmentation, no reassembly
guesswork.

WHAT IT DOES NOT DO
-------------------
It never modifies a byte. Every byte Dashboard sends reaches the Drobo
unchanged and vice versa, so the Drobo cannot tell the difference. It
originates nothing of its own: it has no opinion about the protocol and
never injects a command.

HOW TO USE IT
-------------
1. Run it (no admin needed):

       py tools/nasd_tap.py --drobo 10.0.0.50

   It listens on 127.0.0.1:5000 by default.

2. In Drobo Dashboard, add a Drobo manually by IP and enter  127.0.0.1
   (Dashboard supports manual discovery by address --
   eCmdAddManualDiscoveryIP exists in the firmware, and the UI exposes
   it.)

3. Click around Dashboard: the status page, the drive/bay detail, and
   the tools pages. Those are the screens that fetch battery, fan, PSU
   and performance -- fields our own commands mostly can't get real data
   for yet either (see docs/command-surface.md); performance is the
   exception, reachable directly, just not wired into the driver.

4. Stop with Ctrl+C. Two files land in docs/captures/:
       nasd-tap-<stamp>.jsonl    every chunk, with direction and timing
       nasd-tap-<stamp>.bin      raw client->server bytes, concatenated

PRIVACY
-------
This protocol is unencrypted. If you type a Drobo password into
Dashboard while the tap is running, that password is in the log in plain
text. Change it to something disposable first, or scrub the files before
sharing. See docs/captures/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import threading
import time

SIGNATURE = b"DRINASD\x00"
HEADER_BYTES = 16

_print_lock = threading.Lock()


def hexdump(data: bytes, limit: int = 160) -> list[str]:
    lines = []
    for offset in range(0, min(len(data), limit), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"    {offset:04x}  {hex_part}  {ascii_part}")
    if len(data) > limit:
        lines.append(f"    ... {len(data) - limit} more bytes")
    return lines


def describe_chunk(data: bytes) -> str:
    """
    Best-effort one-line summary. The server's frames are understood;
    the client's are exactly what we're trying to learn, so this stays
    descriptive rather than asserting a structure we haven't confirmed.
    """
    if len(data) >= HEADER_BYTES and data[:8] == SIGNATURE:
        major, minor = data[8], data[9]
        flags = struct.unpack(">H", data[10:12])[0]
        length = struct.unpack(">I", data[12:16])[0]
        return (f"DRINASD frame  ver={major}.{minor} flags=0x{flags:04x} "
                f"len={length} (total {len(data)})")
    if data[:8].rstrip(b"\x00").startswith(b"DRINASD"):
        return f"DRINASD-like header, variant signature {data[:8]!r}"
    printable = sum(1 for b in data[:64] if 32 <= b < 127)
    if data.lstrip()[:1] == b"<":
        return "bare XML"
    if printable > len(data[:64]) * 0.8:
        return f"mostly text: {data[:48]!r}"
    return f"binary, {len(data)} bytes"


class Tap:
    def __init__(self, drobo_host: str, drobo_port: int, jsonl_path: str,
                 bin_path: str):
        self.drobo_host = drobo_host
        self.drobo_port = drobo_port
        self.jsonl_file = open(jsonl_path, "w", encoding="utf-8")
        self.client_bytes_file = open(bin_path, "wb")
        self.started_at = time.time()
        self.chunk_counts = {"c2s": 0, "s2c": 0}
        self.byte_counts = {"c2s": 0, "s2c": 0}
        self._lock = threading.Lock()

    def record(self, direction: str, data: bytes, connection_id: int) -> None:
        with self._lock:
            self.chunk_counts[direction] += 1
            self.byte_counts[direction] += len(data)
            self.jsonl_file.write(json.dumps({
                "t": round(time.time() - self.started_at, 4),
                "conn": connection_id,
                "dir": direction,
                "len": len(data),
                "summary": describe_chunk(data),
                "hex": data.hex(),
            }) + "\n")
            self.jsonl_file.flush()
            # The client->server direction is the unknown one; keep a
            # raw copy so the dissector has clean bytes to work from.
            if direction == "c2s":
                self.client_bytes_file.write(data)
                self.client_bytes_file.flush()

        arrow = "-->" if direction == "c2s" else "<--"
        direction_label = ("Dashboard --> Drobo" if direction == "c2s"
                           else "Drobo --> Dashboard")
        with _print_lock:
            print(f"\n[{time.time() - self.started_at:8.3f}s] "
                  f"conn{connection_id} {arrow} {direction_label}"
                  f"  {len(data)} bytes")
            print(f"    {describe_chunk(data)}")
            for line in hexdump(data):
                print(line)
            sys.stdout.flush()

    def close(self) -> None:
        self.jsonl_file.close()
        self.client_bytes_file.close()


def relay_one_direction(source: socket.socket, destination: socket.socket,
                        direction: str, tap: Tap,
                        connection_id: int) -> None:
    """Copy bytes one way, logging as we go. Never alters the stream."""
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            tap.record(direction, data, connection_id)
            destination.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, destination):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle_connection(client: socket.socket, client_address, tap: Tap,
                      connection_id: int) -> None:
    with _print_lock:
        print(f"\n=== conn{connection_id}: Dashboard connected from "
              f"{client_address[0]}:{client_address[1]} ===")
        print(f"    opening upstream to {tap.drobo_host}:{tap.drobo_port}")
        sys.stdout.flush()
    try:
        upstream = socket.create_connection(
            (tap.drobo_host, tap.drobo_port), timeout=10)
    except OSError as exc:
        with _print_lock:
            print(f"    !! could not reach the Drobo: {exc}")
        client.close()
        return

    upstream.settimeout(None)
    client.settimeout(None)

    threads = [
        threading.Thread(
            target=relay_one_direction,
            args=(client, upstream, "c2s", tap, connection_id),
            daemon=True),
        threading.Thread(
            target=relay_one_direction,
            args=(upstream, client, "s2c", tap, connection_id),
            daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with _print_lock:
        print(f"=== conn{connection_id} closed ===")
        sys.stdout.flush()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drobo", required=True,
                        help="the real Drobo's IP address")
    parser.add_argument("--drobo-port", type=int, default=5000)
    parser.add_argument("--listen", default="127.0.0.1",
                        help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--listen-port", type=int, default=5000)
    parser.add_argument("--outdir", default=None)
    args = parser.parse_args(argv)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = args.outdir or os.path.join(repo_root, "docs", "captures")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = os.path.join(output_dir, f"nasd-tap-{timestamp}.jsonl")
    bin_path = os.path.join(output_dir, f"nasd-tap-{timestamp}.bin")

    tap = Tap(args.drobo, args.drobo_port, jsonl_path, bin_path)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((args.listen, args.listen_port))
    except OSError as exc:
        print(f"Could not listen on {args.listen}:{args.listen_port} -- {exc}")
        print("If the agent is running on this port, stop it first.")
        return 1
    listener.listen(8)

    print(f"nasd tap listening on {args.listen}:{args.listen_port}")
    print(f"  forwarding to the real Drobo at {args.drobo}:{args.drobo_port}")
    print(f"  writing {os.path.basename(jsonl_path)} "
          f"and {os.path.basename(bin_path)}")
    print()
    print("  Now: in Drobo Dashboard, add a Drobo manually by IP and enter")
    print(f"       {args.listen}")
    print("  Then click the status page, the drive detail, "
          "and the tools pages.")
    print("  Ctrl+C when done.")
    print()

    connection_id = 0
    try:
        while True:
            client, client_address = listener.accept()
            connection_id += 1
            threading.Thread(target=handle_connection,
                             args=(client, client_address, tap,
                                   connection_id),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n\nstopping.")
    finally:
        listener.close()
        tap.close()

    print(f"  Dashboard -> Drobo : {tap.chunk_counts['c2s']} chunks, "
          f"{tap.byte_counts['c2s']} bytes")
    print(f"  Drobo -> Dashboard : {tap.chunk_counts['s2c']} chunks, "
          f"{tap.byte_counts['s2c']} bytes")
    print(f"\n  wrote {jsonl_path}")
    print(f"        {bin_path}")
    if tap.chunk_counts["c2s"] == 0:
        print("\n  Nothing came from Dashboard -- it never connected here.")
        print("  Check that you added 127.0.0.1 as a manual Drobo address.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
