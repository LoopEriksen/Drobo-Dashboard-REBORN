#!/usr/bin/env python3
"""
test_nasd_dissect.py - self-test for nasd_dissect.py, and for
nasd_tap.py's start-up (its port is bound before any capture file is
made).

There is no real Drobo to capture from in CI, so this builds a small,
in-memory packet capture with a framing we CHOOSE and therefore KNOW:

    DRINASD4  +  4-byte big-endian length (payload only)  +  XML body

...repeated for a couple of "frames", split across multiple out-of-order
TCP segments to also exercise sequence-number reassembly, then written
out as both a classic pcap file and a pcapng file. The dissector is then
run against each and must recover:

  - the signature, its offset, and that it is DRINASD4
  - the length field's offset/size/endianness
  - the exact XML payload of each frame

This proves the tool works without ever touching hardware. It does not
prove the real Drobo's framing matches this -- that still needs a
genuine capture (see docs/protocol-map.md, "Still open").

Run:
    py tools/test_nasd_dissect.py
"""

from __future__ import annotations

import contextlib
import io
import shutil
import socket
import struct
import sys
import tempfile
import os

import nasd_dissect
import nasd_tap

# Documentation-range addresses (RFC 5737 TEST-NET-1), deliberately not
# a private 192.168.x range: fixtures written in that form are
# indistinguishable from addresses copied out of a real capture of a
# real network, and this project's rule is that no tracked file carries
# a real one. A documentation range makes the synthetic-ness
# self-evident. These are synthetic endpoints in a synthetic capture;
# any pair of addresses works.
CLIENT_IP = "203.0.113.23"
SERVER_IP = "203.0.113.50"
CLIENT_PORT = 51000
SERVER_PORT = 5000

XML_FRAME_1 = (b'<?xml version="1.0"?><DRINETTM><Command>10</Command>'
               b'<Params/></DRINETTM>')
XML_FRAME_2 = (b'<?xml version="1.0"?><DRINETTM><Command>85</Command>'
               b'<Params/></DRINETTM>')

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)
        print(f"  FAIL: {message}")
    else:
        print(f"  ok:   {message}")


# ---------------------------------------------------------------------------
# Build the KNOWN framing:
# signature + 4-byte BE length + XML, per frame.
# ---------------------------------------------------------------------------
def build_known_stream() -> bytes:
    stream = bytearray()
    for xml_payload in (XML_FRAME_1, XML_FRAME_2):
        stream += nasd_dissect.SIGNATURE_DRINASD4
        stream += struct.pack(">I", len(xml_payload))
        stream += xml_payload
    return bytes(stream)


# ---------------------------------------------------------------------------
# Minimal Ethernet + IPv4 + TCP frame builders (checksums left as 0 --
# nasd_dissect.py never validates them, it only reads the fields it
# needs).
# ---------------------------------------------------------------------------
def ethernet_frame(ip_packet: bytes) -> bytes:
    destination_mac = bytes.fromhex("001122334455")
    source_mac = bytes.fromhex("665544332211")
    return destination_mac + source_mac + struct.pack(">H", 0x0800) + ip_packet


def ipv4_packet(source_ip: str, destination_ip: str, segment: bytes) -> bytes:
    total_length = 20 + len(segment)
    header = struct.pack(
        ">BBHHHBBH", (4 << 4) | 5, 0, total_length, 0, 0, 64, 6, 0)
    header += socket.inet_aton(source_ip) + socket.inet_aton(destination_ip)
    return header + segment


def tcp_segment(source_port: int, destination_port: int, seq: int, flags: int,
                payload: bytes) -> bytes:
    header = struct.pack(
        ">HHIIBBHHH", source_port, destination_port, seq, 0, (5 << 4),
        flags, 65535, 0, 0)
    return header + payload


PSH_ACK = 0x18


def build_frames_for_chunks(chunks: list[tuple[int, bytes]]) -> list[bytes]:
    """chunks: list of (seq, payload_bytes) -- built out of arrival
    order on purpose, to prove reassembly sorts by sequence number, not
    by packet order in the file."""
    frames = []
    for seq, payload in chunks:
        segment = tcp_segment(CLIENT_PORT, SERVER_PORT, seq, PSH_ACK, payload)
        packet = ipv4_packet(CLIENT_IP, SERVER_IP, segment)
        frames.append(ethernet_frame(packet))
    return frames


# ---------------------------------------------------------------------------
# Classic pcap writer
# ---------------------------------------------------------------------------
def write_pcap_classic(path: str, link_frames: list[bytes]) -> None:
    with open(path, "wb") as capture_file:
        capture_file.write(struct.pack(">IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0,
                                       65535, 1))
        for i, frame in enumerate(link_frames):
            capture_file.write(struct.pack(
                ">IIII", 1_700_000_000 + i, 0, len(frame), len(frame)))
            capture_file.write(frame)


# ---------------------------------------------------------------------------
# pcapng writer
# ---------------------------------------------------------------------------
#
# NOTE ON ENDIANNESS -- this is why this file exists in two flavours.
#
# These builders were originally big-endian only, and that hid a real
# bug for weeks. pcapng records its byte order per section, and the
# dissector was reading every block TYPE as big-endian regardless. The
# section header block survived that because 0x0A0D0D0A is a
# byte-palindrome, and block LENGTHS were read correctly, so the parser
# walked the whole file happily and simply never recognised a packet
# block -- reporting "Read 0 link-layer frames", which looks exactly
# like an empty capture rather than a broken parser.
#
# Every capture tool on Windows writes LITTLE-endian, so the tool failed
# on every real file while these tests stayed green. So: both orders are
# built and both are run, and the little-endian case is the one that
# matters.
#
def _pcapng_block(block_type: int, body: bytes, endian: str = ">") -> bytes:
    total_length = 4 + 4 + len(body) + 4
    return (struct.pack(endian + "II", block_type, total_length) + body
            + struct.pack(endian + "I", total_length))


def _section_header_block(endian: str = ">") -> bytes:
    # The byte-order magic is the same NUMBER either way -- it is the
    # bytes on disk that differ, which is the whole point of it.
    body = struct.pack(endian + "IHHq", 0x1A2B3C4D, 1, 0, -1)
    return _pcapng_block(0x0A0D0D0A, body, endian)


def _interface_description_block(linktype: int = 1,
                                 snapshot_length: int = 65535,
                                 endian: str = ">") -> bytes:
    body = struct.pack(endian + "HHI", linktype, 0, snapshot_length)
    return _pcapng_block(0x00000001, body, endian)


def _enhanced_packet_block(interface_id: int, frame: bytes,
                           endian: str = ">") -> bytes:
    pad = (-len(frame)) % 4
    padded = frame + b"\x00" * pad
    body = (struct.pack(
        endian + "IIIII", interface_id, 0, 0, len(frame), len(frame))
            + padded)
    return _pcapng_block(0x00000006, body, endian)


def write_pcapng(path: str, link_frames: list[bytes],
                 endian: str = ">") -> None:
    with open(path, "wb") as capture_file:
        capture_file.write(_section_header_block(endian))
        capture_file.write(_interface_description_block(endian=endian))
        for frame in link_frames:
            capture_file.write(_enhanced_packet_block(0, frame, endian))


# ---------------------------------------------------------------------------
# The test itself
# ---------------------------------------------------------------------------
def run_against(path: str, label: str) -> None:
    print(f"\n=== dissecting {label} ({path}) ===")
    packets = nasd_dissect.read_capture(path)
    check(len(packets) > 0,
          f"{label}: capture file parsed into >0 link-layer frames")

    streams = nasd_dissect.collect_streams(packets)
    check(len(streams) == 1,
          f"{label}: exactly one TCP stream recovered (got {len(streams)})")
    if not streams:
        return
    stream = next(iter(streams.values()))
    check(stream["client"] == (CLIENT_IP, CLIENT_PORT),
          f"{label}: client endpoint identified correctly")
    check(stream["server"] == (SERVER_IP, SERVER_PORT),
          f"{label}: server endpoint identified correctly")

    client_bytes = nasd_dissect.reassemble(stream["to_server"])
    expected = build_known_stream()
    check(client_bytes == expected,
          f"{label}: reassembled client->server stream matches "
          "the known bytes exactly")

    login = nasd_dissect.infer_login_frame(client_bytes)
    check(login is not None, f"{label}: a login frame was inferred at all")
    if login is None:
        return
    check(login["signature"] == nasd_dissect.SIGNATURE_DRINASD4,
          f"{label}: recovered signature is DRINASD4")
    check(login["signature_offset"] == 0,
          f"{label}: signature located at offset 0")
    check(login["pad"] == 0,
          f"{label}: length field placed immediately after "
          "the signature (no padding)")
    check(login["length_size"] == 4,
          f"{label}: length field recovered as 4 bytes")
    check(login["endian"] == ">",
          f"{label}: length field recovered as big-endian")
    check(login["includes_header"] is False,
          f"{label}: length recovered as payload-only (excludes header)")
    check(login["payload"] == XML_FRAME_1,
          f"{label}: first frame's XML payload recovered exactly")

    remainder = client_bytes[login["frame_end_absolute"]:]
    command_framing = nasd_dissect.infer_command_frames(remainder)
    check(command_framing is not None,
          f"{label}: command-frame region after the login frame was decoded")
    if command_framing is None:
        return
    check(command_framing["signature"] == nasd_dissect.SIGNATURE_DRINASD4,
          f"{label}: command frame(s) recovered as also carrying DRINASD4 "
          "(repeat-signature framing)")
    check(len(command_framing["frames"]) == 1,
          f"{label}: exactly one command frame recovered "
          f"(got {len(command_framing['frames'])})")
    if command_framing["frames"]:
        check(command_framing["frames"][0]["payload"] == XML_FRAME_2,
              f"{label}: second frame's XML payload recovered exactly")


# ----------------------------------------------------------------------
# No DRINASD login frame: the command port, and an empty capture
# ----------------------------------------------------------------------
# The command port (5001) frames everything as DRINETTM, so no DRINASD
# login frame is ever found there. Until this was fixed, that path --
# and a capture with nothing on the port at all -- died with NameError
# on `args`, after the stream output but before any -o report was
# written.
def run_dissector(argv: list[str]) -> tuple[object, str]:
    """Run nasd_dissect.main(argv); return its result and its output.

    The result is main()'s return code, or the exception it raised.
    """
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            result = nasd_dissect.main(argv)
    except Exception as exc:  # noqa: BLE001 -- the crash IS what is tested
        result = exc
    return result, printed.getvalue()


def run_without_login_frame(temp_dir: str) -> None:
    print("\n=== no login frame: --port 5001, and an empty capture ===")
    request_payload = b"<TMCmd><CmdID>30</CmdID><Params></Params></TMCmd>\x00"
    frame = (b"DRINETTM" + struct.pack(">II", 0, len(request_payload))
             + request_payload)
    segment = tcp_segment(CLIENT_PORT, 5001, 1000, PSH_ACK, frame)
    command_capture = os.path.join(temp_dir, "command-port.pcap")
    write_pcap_classic(command_capture,
                       [ethernet_frame(ipv4_packet(CLIENT_IP, SERVER_IP,
                                                   segment))])
    empty_capture = os.path.join(temp_dir, "empty.pcap")
    write_pcap_classic(empty_capture, [])

    for path, label in ((command_capture, "a DRINETTM stream"),
                        (empty_capture, "an empty capture")):
        report_path = path + ".json"
        result, text = run_dissector([path, "--port", "5001",
                                      "-o", report_path])
        check(result == 0, f"{label} on --port 5001 runs to the end "
                           f"(got {result!r})")
        check("port 5000" not in text and "port-5000" not in text,
              f"{label}: no message names port 5000 when 5001 was asked "
              "for")
        check(os.path.exists(report_path),
              f"{label}: the -o report is still written")
    _, text = run_dissector([empty_capture, "--port", "5001"])
    check("Nothing on port 5001" in text,
          "an empty capture says which port it looked on")


def run_preview_checks() -> None:
    """--max-preview counts characters, as its help says, not bytes."""
    print("\n=== payload previews are cut by characters ===")
    accented = "\u00e9" * 10
    preview = nasd_dissect._preview(accented.encode("utf-8"), 5)
    check(preview == "\u00e9" * 5,
          f"five characters of a two-byte script are five characters "
          f"(got {preview!r})")
    check("\ufffd" not in preview,
          "a preview never ends in a character cut in half")
    check(nasd_dissect._preview(b"<TMCmd/>", 400) == "<TMCmd/>",
          "a payload shorter than the limit is shown whole")


def _hold_loopback_port() -> socket.socket:
    """Bind an ephemeral 127.0.0.1 port that nasd_tap cannot take over.

    On Windows a socket that sets SO_REUSEADDR, as nasd_tap's listener
    does, may bind a port another socket of the same account holds,
    unless the holder set SO_EXCLUSIVEADDRUSE; without it the "port in
    use" path would never be reached there. CPython's own
    socket_helper.bind_port sets it for the same reason.
    """
    port_holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        port_holder.setsockopt(socket.SOL_SOCKET,
                               socket.SO_EXCLUSIVEADDRUSE, 1)
    port_holder.bind(("127.0.0.1", 0))
    port_holder.listen(1)
    return port_holder


def _run_tap_without_accepting(tap_arguments: list[str]) -> tuple[int, bool]:
    """Run nasd_tap.main with its accept loop replaced; say if it ran.

    The real loop waits for Dashboard with no timeout, so a bind that
    unexpectedly succeeds would hang the suite (and the CI job) instead
    of failing a check. The stand-in closes what the real loop closes.
    """
    accept_loop_ran = False
    real_accept_loop = nasd_tap._accept_until_interrupted

    def closing_stand_in(listener, tap) -> None:
        nonlocal accept_loop_ran
        accept_loop_ran = True
        listener.close()
        tap.close()

    nasd_tap._accept_until_interrupted = closing_stand_in
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = nasd_tap.main(tap_arguments)
    finally:
        nasd_tap._accept_until_interrupted = real_accept_loop
    return result, accept_loop_ran


def run_tap_bind_failure_checks(temp_dir: str) -> None:
    """nasd_tap leaves no empty capture files when it cannot listen.

    nasd_tap is the capture tool that records what this dissector reads,
    so its one failure that writes files is checked here. The port is
    held by this test on loopback; the Drobo address is never contacted
    on this path.
    """
    print("\n=== nasd_tap: a port it cannot bind leaves nothing behind ===")
    capture_dir = os.path.join(temp_dir, "tap-captures")
    os.makedirs(capture_dir)
    port_holder = _hold_loopback_port()
    try:
        held_port = port_holder.getsockname()[1]
        result, accept_loop_ran = _run_tap_without_accepting([
            "--drobo", "127.0.0.1", "--listen", "127.0.0.1",
            "--listen-port", str(held_port), "--outdir", capture_dir])
    finally:
        port_holder.close()
    check(not accept_loop_ran,
          "the held port is not bound, so no connection is awaited")
    check(result == 1, f"a port already in use exits 1 (got {result!r})")
    leftover_files = os.listdir(capture_dir)
    check(leftover_files == [],
          f"no capture files are created (found {leftover_files})")


def main() -> int:
    # The synthetic captures go in a temp folder that is removed however
    # the run ends. Until 2026-09-16 every run left its folder behind in
    # TEMP.
    temp_dir = tempfile.mkdtemp(prefix="nasd_dissect_test_")
    try:
        return run_checks(temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def run_checks(temp_dir: str) -> int:
    known_stream = build_known_stream()
    # Split into three chunks and deliberately list them out of arrival
    # order, so the dissector has to sort by TCP sequence number to get
    # the bytes back in the right order.
    # partway into frame 1's XML
    split_a = len(nasd_dissect.SIGNATURE_DRINASD4) + 4 + 5
    # partway into frame 2's XML
    split_b = len(known_stream) - 12
    chunk1 = known_stream[0:split_a]
    chunk2 = known_stream[split_a:split_b]
    chunk3 = known_stream[split_b:]
    base_seq = 5000
    chunks = [
        (base_seq + split_a, chunk2),   # out of order on purpose
        (base_seq, chunk1),
        (base_seq + split_b, chunk3),
    ]
    link_frames = build_frames_for_chunks(chunks)

    pcap_path = os.path.join(temp_dir, "known.pcap")
    pcapng_path = os.path.join(temp_dir, "known.pcapng")
    pcapng_le_path = os.path.join(temp_dir, "known-little-endian.pcapng")
    write_pcap_classic(pcap_path, link_frames)
    write_pcapng(pcapng_path, link_frames, endian=">")
    write_pcapng(pcapng_le_path, link_frames, endian="<")

    print(f"Synthesized {len(known_stream)}-byte known stream, split into "
          f"{len(chunks)} out-of-order TCP segments.")
    print(f"Temp captures written to {temp_dir} (removed when the test ends)")

    run_against(pcap_path, "classic pcap")
    run_against(pcapng_path, "pcapng (big-endian)")
    # The one that actually matters: every capture tool on Windows
    # writes little-endian, and this case failed silently -- reading
    # zero packets -- until 2026-07-26. Without it the suite passes
    # while the tool is useless on every real file.
    run_against(pcapng_le_path, "pcapng (little-endian, as real tools write)")
    run_without_login_frame(temp_dir)
    run_preview_checks()
    run_tap_bind_failure_checks(temp_dir)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    # Make sure `import nasd_dissect` finds the sibling module
    # regardless of the working directory this is invoked from.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
