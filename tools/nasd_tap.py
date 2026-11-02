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

# The most one recv() call takes off a socket while relaying.
RECEIVE_CHUNK_BYTES = 65536
# How long to wait for the real Drobo to accept the upstream connection.
UPSTREAM_CONNECT_TIMEOUT_SECONDS = 10
# Connections the listener queues while Dashboard opens several at once.
LISTEN_BACKLOG = 8

_print_lock = threading.Lock()


def hexdump(data: bytes, limit_bytes: int = 160) -> list[str]:
    """Format up to `limit_bytes` of `data` as indented hexdump lines.

    The console only shows the start of each chunk (the JSONL log keeps
    all of it), so a longer chunk ends with a "... N more bytes" line.
    """
    lines = []
    for offset in range(0, min(len(data), limit_bytes), 16):
        row = data[offset:offset + 16]
        hex_part = " ".join(f"{byte_value:02x}" for byte_value in row)
        hex_part = hex_part.ljust(47)
        ascii_part = "".join(chr(byte_value) if 32 <= byte_value < 127
                             else "." for byte_value in row)
        lines.append(f"    {offset:04x}  {hex_part}  {ascii_part}")
    if len(data) > limit_bytes:
        lines.append(f"    ... {len(data) - limit_bytes} more bytes")
    return lines


def describe_chunk(data: bytes) -> str:
    """
    Best-effort one-line summary. The server's frames are understood;
    the client's are exactly what we're trying to learn, so this stays
    descriptive rather than asserting a structure we haven't confirmed.
    """
    if len(data) >= HEADER_BYTES and data[:8] == SIGNATURE:
        major_version, minor_version = data[8], data[9]
        flags = struct.unpack(">H", data[10:12])[0]
        payload_length = struct.unpack(">I", data[12:16])[0]
        return (f"DRINASD frame  ver={major_version}.{minor_version} "
                f"flags=0x{flags:04x} "
                f"len={payload_length} (total {len(data)})")
    if data[:8].rstrip(b"\x00").startswith(b"DRINASD"):
        return f"DRINASD-like header, variant signature {data[:8]!r}"
    printable_count = sum(1 for byte_value in data[:64]
                          if 32 <= byte_value < 127)
    if data.lstrip()[:1] == b"<":
        return "bare XML"
    if printable_count > len(data[:64]) * 0.8:
        return f"mostly text: {data[:48]!r}"
    return f"binary, {len(data)} bytes"


class TrafficTap:
    """The capture files every relay thread writes each chunk to.

    One lock covers both capture files and the counters, because two
    threads per connection record at once and a JSONL line must never
    be interleaved with another. Only the client->server bytes are also
    kept raw: that was the unknown direction, and the dissector wants
    clean bytes to work from.
    """

    def __init__(self, drobo_host: str, drobo_port: int, jsonl_path: str,
                 client_bytes_path: str):
        """Open both capture files now, so a bad path fails at start."""
        self.drobo_host = drobo_host
        self.drobo_port = drobo_port
        self.jsonl_file = open(jsonl_path, "w", encoding="utf-8")
        self.client_bytes_file = open(client_bytes_path, "wb")
        self.started_at = time.time()
        self.chunk_counts = {"c2s": 0, "s2c": 0}
        self.byte_counts = {"c2s": 0, "s2c": 0}
        self._lock = threading.Lock()

    def record(self, direction: str, data: bytes, connection_id: int) -> None:
        """Log one relayed chunk to the files, then show it on screen.

        `direction` is "c2s" (Dashboard to Drobo) or "s2c" (back).
        """
        with self._lock:
            self._write_capture(direction, data, connection_id)
        self._print_chunk(direction, data, connection_id)

    def _write_capture(self, direction: str, data: bytes,
                       connection_id: int) -> None:
        """Count the chunk and append it to the files (lock held).

        Each file is flushed at once, so a capture cut short by Ctrl+C
        or a crash still holds everything relayed before it.
        """
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

    def _print_chunk(self, direction: str, data: bytes,
                     connection_id: int) -> None:
        """Show one chunk on screen, whole, under the print lock."""
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
        """Close both capture files."""
        self.jsonl_file.close()
        self.client_bytes_file.close()


def _shutdown_quietly(sock: socket.socket) -> None:
    """Shut both halves of `sock`, ignoring one that is already gone.

    Shutting down (rather than closing) wakes the other direction's
    blocked recv(), so both relay threads of a connection end together.
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _copy_until_closed(source: socket.socket, destination: socket.socket,
                       direction: str, tap: TrafficTap,
                       connection_id: int) -> None:
    """Record and forward each chunk until `source` closes its end.

    A socket error is left to the caller, which ends the relay the same
    way either way.
    """
    while True:
        chunk = source.recv(RECEIVE_CHUNK_BYTES)
        if not chunk:
            return
        tap.record(direction, chunk, connection_id)
        destination.sendall(chunk)


def relay_one_direction(source: socket.socket, destination: socket.socket,
                        direction: str, tap: TrafficTap,
                        connection_id: int) -> None:
    """Copy bytes one way, logging as we go. Never alters the stream."""
    try:
        _copy_until_closed(source, destination, direction, tap,
                           connection_id)
    except OSError:
        pass
    finally:
        for sock in (source, destination):
            _shutdown_quietly(sock)


def _relay_both_ways(client_socket: socket.socket,
                     upstream_socket: socket.socket, tap: TrafficTap,
                     connection_id: int) -> None:
    """Run one relay thread per direction and wait for both to end."""
    threads = [
        threading.Thread(
            target=relay_one_direction,
            args=(client_socket, upstream_socket, "c2s", tap,
                  connection_id),
            daemon=True),
        threading.Thread(
            target=relay_one_direction,
            args=(upstream_socket, client_socket, "s2c", tap,
                  connection_id),
            daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def handle_connection(client_socket: socket.socket, client_address,
                      tap: TrafficTap, connection_id: int) -> None:
    """Relay one Dashboard connection to the Drobo until either ends.

    Two daemon threads over blocking sockets, with no timeout once the
    upstream is open: a relay that buffered, re-chunked or timed out on
    its own would change the timing or the bytes it exists to record.
    """
    with _print_lock:
        print(f"\n=== conn{connection_id}: Dashboard connected from "
              f"{client_address[0]}:{client_address[1]} ===")
        print(f"    opening upstream to {tap.drobo_host}:{tap.drobo_port}")
        sys.stdout.flush()
    try:
        upstream_socket = socket.create_connection(
            (tap.drobo_host, tap.drobo_port),
            timeout=UPSTREAM_CONNECT_TIMEOUT_SECONDS)
    except OSError as exc:
        with _print_lock:
            print(f"    !! could not reach the Drobo: {exc}")
        client_socket.close()
        return

    upstream_socket.settimeout(None)
    client_socket.settimeout(None)

    _relay_both_ways(client_socket, upstream_socket, tap, connection_id)

    with _print_lock:
        print(f"=== conn{connection_id} closed ===")
        sys.stdout.flush()


def _parse_arguments(argv) -> argparse.Namespace:
    """Read the command line (the module docstring is the --help)."""
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
    return parser.parse_args(argv)


def _capture_paths(output_dir_argument: str | None) -> tuple[str, str]:
    """Create the capture folder and name this run's two files in it.

    The default folder is docs/captures, which .gitignore keeps out of
    the repository, because a capture can hold a password in plain
    text.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = output_dir_argument or os.path.join(repo_root, "docs",
                                                     "captures")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = os.path.join(output_dir, f"nasd-tap-{timestamp}.jsonl")
    client_bytes_path = os.path.join(output_dir, f"nasd-tap-{timestamp}.bin")
    return jsonl_path, client_bytes_path


def _open_listener(listen_host: str, listen_port: int
                   ) -> socket.socket | None:
    """Bind the port Dashboard connects to, or say why not (None).

    The socket is closed when the bind fails, rather than left for the
    garbage collector.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((listen_host, listen_port))
    except OSError as exc:
        listener.close()
        print(f"Could not listen on {listen_host}:{listen_port} -- {exc}")
        print("If the agent is running on this port, stop it first.")
        return None
    listener.listen(LISTEN_BACKLOG)
    return listener


def _print_instructions(args: argparse.Namespace, jsonl_path: str,
                        client_bytes_path: str) -> None:
    """Tell the person what to do in Dashboard while the tap runs."""
    print(f"nasd tap listening on {args.listen}:{args.listen_port}")
    print(f"  forwarding to the real Drobo at {args.drobo}:{args.drobo_port}")
    print(f"  writing {os.path.basename(jsonl_path)} "
          f"and {os.path.basename(client_bytes_path)}")
    print()
    print("  Now: in Drobo Dashboard, add a Drobo manually by IP and enter")
    print(f"       {args.listen}")
    print("  Then click the status page, the drive detail, "
          "and the tools pages.")
    print("  Ctrl+C when done.")
    print()


def _accept_until_interrupted(listener: socket.socket,
                              tap: TrafficTap) -> None:
    """Hand each incoming connection to its own thread until Ctrl+C.

    The listener and the capture files are closed however this ends,
    so the files on disk are complete.
    """
    connection_id = 0
    try:
        while True:
            client_socket, client_address = listener.accept()
            connection_id += 1
            threading.Thread(target=handle_connection,
                             args=(client_socket, client_address, tap,
                                   connection_id),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\n\nstopping.")
    finally:
        listener.close()
        tap.close()


def _print_totals(tap: TrafficTap, jsonl_path: str,
                  client_bytes_path: str) -> None:
    """Sum up what was relayed; say why if Dashboard sent nothing."""
    print(f"  Dashboard -> Drobo : {tap.chunk_counts['c2s']} chunks, "
          f"{tap.byte_counts['c2s']} bytes")
    print(f"  Drobo -> Dashboard : {tap.chunk_counts['s2c']} chunks, "
          f"{tap.byte_counts['s2c']} bytes")
    print(f"\n  wrote {jsonl_path}")
    print(f"        {client_bytes_path}")
    if tap.chunk_counts["c2s"] == 0:
        print("\n  Nothing came from Dashboard -- it never connected here.")
        print("  Check that you added 127.0.0.1 as a manual Drobo address.")


def main(argv=None) -> int:
    """Relay Dashboard's connections to the Drobo until Ctrl+C, logging.

    Returns 1 when the listening port cannot be bound (most often the
    agent already holds it), else 0. The port is bound before the
    capture files are made, so a run that cannot listen leaves nothing
    behind; the files used to be opened first, and a failed bind left
    two empty ones in the capture folder.
    """
    args = _parse_arguments(argv)
    listener = _open_listener(args.listen, args.listen_port)
    if listener is None:
        return 1

    try:
        jsonl_path, client_bytes_path = _capture_paths(args.outdir)
        tap = TrafficTap(args.drobo, args.drobo_port, jsonl_path,
                         client_bytes_path)
    except OSError:
        # A capture folder or file that cannot be made: release the
        # port before the error is reported.
        listener.close()
        raise

    _print_instructions(args, jsonl_path, client_bytes_path)
    _accept_until_interrupted(listener, tap)
    _print_totals(tap, jsonl_path, client_bytes_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
