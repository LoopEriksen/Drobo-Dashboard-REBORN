#!/usr/bin/env python3
"""
nasd_dissect.py - reconstruct nasd's request framing from a packet
capture.

Point this at a Wireshark capture of "tcp port 5000" taken while Drobo
Dashboard talks to a real 5N, and it will:

  1. Read the capture file -- classic pcap or pcapng, parsed by hand
     (stdlib only, no scapy/dpkt).
  2. Keep only TCP port 5000 traffic and reassemble the client->server
     and server->client byte streams, each in sequence-number order.
  3. Look for the "DRINASD"/"DRINASD4" signature strings that
     docs/protocol-map.md recovered from the firmware.
  4. Try a battery of plausible header shapes (signature position,
     length field offset/size/endianness, whether the length includes
     the header or just the payload) against the reassembled bytes, and
     report which shape actually predicts where each XML payload starts
     and ends.
  5. Print the first decoded XML payloads and a proposed NasdHeader
     layout.

This tool INFERS framing from evidence in one capture. Nothing it prints
is a substitute for `protocol-map.md`'s CONFIRMED/FIRMWARE/GUESS
discipline -- treat everything below as a lead to verify against more
captures, and say so when reporting results. See test_nasd_dissect.py
for proof this recovers a *known* framing when the ground truth is under
our control.

The capture-file readers are implemented from public format
specifications, not from anyone's parser source: classic pcap's
file/record layout has been a de facto standard since tcpdump, and
pcapng is documented by the IETF opsawg draft
(draft-tuexen-opsawg-pcapng) and by Wireshark's own published format
page. Written by hand against those documents, stdlib only -- no scapy,
no dpkt.

Usage:
    py tools/nasd_dissect.py docs/captures/03-idle-60s.pcapng
    py tools/nasd_dissect.py capture.pcap -o
        docs/captures/dissect-report.json

Limitations, stated up front:
  - No IP defragmentation, no TCP sequence-wraparound handling. Fine for
    the short, single-connection captures this project's playbook asks
    for.
  - IPv6 extension headers are not walked; only a bare "next header is
    TCP" IPv6 packet is understood.
  - Retransmission handling in reassembly is a simple seq-ordered
    de-overlap, not a full TCP stack.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Constants from docs/protocol-map.md
# ---------------------------------------------------------------------------
SIGNATURE_DRINASD4 = b"DRINASD4"
SIGNATURE_DRINASD = b"DRINASD"
# longest first -- DRINASD is a byte-prefix of DRINASD4
SIGNATURES = (SIGNATURE_DRINASD4, SIGNATURE_DRINASD)

# The status port. The COMMAND port is 5001 -- pass --port to look at
# that instead, which is where Identify and every other command actually
# goes.
DEFAULT_STATUS_PORT = 5000

ETH_TYPE_IPV4 = 0x0800
ETH_TYPE_IPV6 = 0x86DD
ETH_TYPE_VLAN = 0x8100
ETH_TYPE_VLAN_QINQ = 0x88A8


# ---------------------------------------------------------------------------
# Capture file readers -- classic pcap and pcapng, parsed directly.
# Each returns a list of (timestamp, link_layer_frame_bytes, linktype).
# ---------------------------------------------------------------------------
_CLASSIC_PCAP_MAGICS = {
    b"\xa1\xb2\xc3\xd4": (">", 1e-6),
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6),
    b"\xa1\xb2\x3c\x4d": (">", 1e-9),
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9),
}


def read_pcap_classic(data: bytes) -> list[tuple[float, bytes, int]]:
    endian, timestamp_scale = _CLASSIC_PCAP_MAGICS[data[0:4]]
    (_major, _minor, _timezone, _significant_figures, _snapshot_length,
     linktype) = struct.unpack_from(endian + "HHiIII", data, 4)
    pos = 24
    packets: list[tuple[float, bytes, int]] = []
    while pos + 16 <= len(data):
        (timestamp_seconds, timestamp_fraction, captured_length,
         _original_length) = struct.unpack_from(endian + "IIII", data, pos)
        pos += 16
        if captured_length < 0 or pos + captured_length > len(data):
            break
        frame = data[pos:pos + captured_length]
        pos += captured_length
        packets.append(
            (timestamp_seconds + timestamp_fraction * timestamp_scale,
             frame, linktype))
    return packets


PCAPNG_SECTION_HEADER_BLOCK = 0x0A0D0D0A
PCAPNG_INTERFACE_DESCRIPTION_BLOCK = 0x00000001
PCAPNG_SIMPLE_PACKET_BLOCK = 0x00000003
PCAPNG_ENHANCED_PACKET_BLOCK = 0x00000006


def read_pcapng(data: bytes) -> list[tuple[float, bytes, int]]:
    packets: list[tuple[float, bytes, int]] = []
    endian = ">"  # provisional until the first SHB sets it for real
    interfaces: list[int] = []  # linktype per interface id, in this section
    pos = 0
    while pos + 12 <= len(data):
        # Read the block type with the CURRENT section endianness.
        #
        # It is safe to do this before the first section header block
        # has told us the endianness, because 0x0A0D0D0A is a
        # byte-palindrome -- it reads the same either way, so the SHB
        # below is always recognised.
        #
        # Reading it as ">I" unconditionally was a real bug, and a quiet
        # one. Every other block type is an ordinary little-endian
        # integer in a little-endian capture, so an enhanced packet
        # block (type 6, bytes 06 00 00 00) read big-endian comes out as
        # 0x06000000 and matches nothing. Block LENGTHS were already
        # read with the right endianness, so the walk marched happily
        # through the whole file, recognised not a single packet, and
        # reported "Read 0 link-layer frames" -- which reads exactly
        # like an empty capture rather than a broken parser. Every
        # capture Windows produces is little-endian, so this never
        # worked on a real file; the tests passed because their fixtures
        # were big-endian.
        block_type = struct.unpack_from(endian + "I", data, pos)[0]
        if block_type == PCAPNG_SECTION_HEADER_BLOCK:
            magic = data[pos + 8:pos + 12]
            if magic == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            elif magic == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            else:
                raise ValueError(
                    f"pcapng: bad byte-order magic {magic!r} in section "
                    f"header block")
            interfaces = []
        block_total_length = struct.unpack_from(endian + "I", data, pos + 4)[0]
        if block_total_length < 12 or pos + block_total_length > len(data):
            break  # truncated/corrupt block -- stop rather than misread
        body = data[pos + 8:pos + block_total_length - 4]

        if block_type == PCAPNG_INTERFACE_DESCRIPTION_BLOCK and len(body) >= 8:
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            interfaces.append(linktype)
        elif block_type == PCAPNG_ENHANCED_PACKET_BLOCK and len(body) >= 20:
            interface_id = struct.unpack_from(endian + "I", body, 0)[0]
            captured_length = struct.unpack_from(endian + "I", body, 12)[0]
            frame = body[20:20 + captured_length]
            linktype = (interfaces[interface_id]
                        if interface_id < len(interfaces) else 1)
            packets.append((0.0, frame, linktype))
        elif block_type == PCAPNG_SIMPLE_PACKET_BLOCK and len(body) >= 4:
            frame = body[4:]
            linktype = interfaces[0] if interfaces else 1
            packets.append((0.0, frame, linktype))
        # Other block types (name resolution, interface stats, comments,
        # obsolete "packet block") are skipped -- not needed for
        # framing.

        pos += block_total_length
    return packets


def read_capture(path: str) -> list[tuple[float, bytes, int]]:
    with open(path, "rb") as capture_file:
        data = capture_file.read()
    if len(data) < 4:
        raise ValueError(f"{path}: too small to be a capture file")
    if data[0:4] in _CLASSIC_PCAP_MAGICS:
        return read_pcap_classic(data)
    if struct.unpack_from(">I", data, 0)[0] == PCAPNG_SECTION_HEADER_BLOCK:
        return read_pcapng(data)
    raise ValueError(
        f"{path}: not recognised as classic pcap or pcapng "
        f"(first bytes: {data[0:4]!r})")


# ---------------------------------------------------------------------------
# Link layer / IP / TCP parsing -- just enough to get to a TCP payload.
# ---------------------------------------------------------------------------
def strip_link_layer(frame: bytes, linktype: int) -> Optional[bytes]:
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return None
        offset = 12
        if offset + 2 > len(frame):
            return None
        ethertype = struct.unpack_from(">H", frame, offset)[0]
        while ethertype in (ETH_TYPE_VLAN, ETH_TYPE_VLAN_QINQ):
            offset += 4
            if offset + 2 > len(frame):
                return None
            ethertype = struct.unpack_from(">H", frame, offset)[0]
        if ethertype in (ETH_TYPE_IPV4, ETH_TYPE_IPV6):
            return frame[offset + 2:]
        return None
    if linktype == 101:  # LINKTYPE_RAW -- IP packet, no link header at all
        return frame
    if linktype == 113:  # LINKTYPE_LINUX_SLL
        if len(frame) < 16:
            return None
        protocol = struct.unpack_from(">H", frame, 14)[0]
        if protocol in (ETH_TYPE_IPV4, ETH_TYPE_IPV6):
            return frame[16:]
        return None
    # Unknown linktype: best-effort guess between raw-IP and Ethernet.
    if frame and (frame[0] >> 4) in (4, 6):
        return frame
    if len(frame) >= 14:
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        if ethertype in (ETH_TYPE_IPV4, ETH_TYPE_IPV6):
            return frame[14:]
    return None


def parse_ip(packet: bytes):
    """Returns (protocol, source_ip, destination_ip, payload) or
    None."""
    if not packet:
        return None
    version = packet[0] >> 4
    if version == 4:
        if len(packet) < 20:
            return None
        header_length = (packet[0] & 0x0F) * 4
        if header_length < 20 or len(packet) < header_length:
            return None
        total_length = struct.unpack_from(">H", packet, 2)[0]
        protocol = packet[9]
        source_ip = socket.inet_ntop(socket.AF_INET, packet[12:16])
        destination_ip = socket.inet_ntop(socket.AF_INET, packet[16:20])
        end = total_length if 0 < total_length <= len(packet) else len(packet)
        return protocol, source_ip, destination_ip, packet[header_length:end]
    if version == 6:
        if len(packet) < 40:
            return None
        payload_length = struct.unpack_from(">H", packet, 4)[0]
        next_header = packet[6]
        source_ip = socket.inet_ntop(socket.AF_INET6, packet[8:24])
        destination_ip = socket.inet_ntop(socket.AF_INET6, packet[24:40])
        end = (min(40 + payload_length, len(packet)) if payload_length
               else len(packet))
        return next_header, source_ip, destination_ip, packet[40:end]
    return None


def parse_tcp(segment: bytes):
    """Returns (source_port, destination_port, sequence_number, flags,
    data) or None."""
    if len(segment) < 20:
        return None
    source_port, destination_port = struct.unpack_from(">HH", segment, 0)
    sequence_number = struct.unpack_from(">I", segment, 4)[0]
    data_offset = (segment[12] >> 4) * 4
    flags = segment[13]
    if data_offset < 20:
        data_offset = 20
    if data_offset > len(segment):
        data_offset = len(segment)
    return (source_port, destination_port, sequence_number, flags,
            segment[data_offset:])


# ---------------------------------------------------------------------------
# TCP stream reassembly, filtered to port 5000.
# ---------------------------------------------------------------------------
def collect_streams(packets: list[tuple[float, bytes, int]],
                    port: int = DEFAULT_STATUS_PORT) -> dict:
    """Group TCP payload segments for `port` by connection and
    direction.

    Returns {stream_key: {"to_server": [(sequence_number, bytes), ...],
                           "to_client": [(sequence_number, bytes), ...],
                           "client": (ip, port), "server": (ip, port)}}
    """
    streams: dict = {}
    for _ts, frame, linktype in packets:
        ip_packet = strip_link_layer(frame, linktype)
        if ip_packet is None:
            continue
        parsed = parse_ip(ip_packet)
        if parsed is None:
            continue
        protocol, source_ip, destination_ip, payload = parsed
        if protocol != 6:  # TCP only
            continue
        tcp = parse_tcp(payload)
        if tcp is None:
            continue
        source_port, destination_port, sequence_number, _flags, data = tcp
        if source_port != port and destination_port != port:
            continue
        if not data:
            continue  # pure ACK/SYN, nothing to reassemble
        key = frozenset({(source_ip, source_port),
                         (destination_ip, destination_port)})
        stream = streams.setdefault(
            key, {"to_server": [], "to_client": [], "client": None,
                  "server": None})
        if destination_port == port:
            stream["to_server"].append((sequence_number, data))
            stream["client"], stream["server"] = (
                (source_ip, source_port), (destination_ip, destination_port))
        else:
            stream["to_client"].append((sequence_number, data))
            stream["server"], stream["client"] = (
                (source_ip, source_port), (destination_ip, destination_port))
    return streams


def reassemble(segments: list[tuple[int, bytes]]) -> bytes:
    """Sort TCP segments by sequence number and stitch them together,
    dropping retransmitted/overlapping bytes. Not a full TCP stack -- no
    wraparound handling, good enough for one short capture."""
    ordered = sorted((segment for segment in segments if segment[1]),
                     key=lambda segment: segment[0])
    stitched = bytearray()
    next_sequence = None
    for sequence_number, data in ordered:
        if next_sequence is None:
            stitched += data
            next_sequence = sequence_number + len(data)
            continue
        if sequence_number >= next_sequence:
            stitched += data
            next_sequence = sequence_number + len(data)
        else:
            overlap = next_sequence - sequence_number
            if len(data) > overlap:
                stitched += data[overlap:]
                next_sequence = sequence_number + len(data)
            # else: fully-contained retransmission, skip
    return bytes(stitched)


# ---------------------------------------------------------------------------
# Signature search
# ---------------------------------------------------------------------------
def find_signature_occurrences(stream_bytes: bytes) -> list[tuple[int, bytes]]:
    """Every offset where DRINASD or DRINASD4 appears, longest match
    wins when both match the same offset (DRINASD is a prefix of
    DRINASD4)."""
    signature_hits: dict[int, bytes] = {}
    for signature in SIGNATURES:
        start = 0
        while True:
            index = stream_bytes.find(signature, start)
            if index == -1:
                break
            if (index not in signature_hits
                    or len(signature) > len(signature_hits[index])):
                signature_hits[index] = signature
            start = index + 1
    return sorted(signature_hits.items())


def _looks_like_xml_start(payload: bytes) -> bool:
    if payload[:5] == b"<?xml":
        return True
    return len(payload) > 1 and payload[0:1] == b"<" and payload[1:2].isalpha()


# ---------------------------------------------------------------------------
# Framing inference
# ---------------------------------------------------------------------------
# Candidate padding between the end of the signature (or start of frame,
# if no signature) and the start of the length field. 0 is the likely
# case; the others are kept in case there's a reserved/version byte in
# between.
_PAD_CANDIDATES = (0, 1, 2, 4)
_LENGTH_SIZE_CANDIDATES = (4, 2)
_ENDIAN_CANDIDATES = (">", "<")


def _score_login_candidate(stream_bytes: bytes, signature_length: int,
                           pad: int, length_size: int, endian: str,
                           includes_header: bool) -> Optional[dict]:
    field_start = signature_length + pad
    field_end = field_start + length_size
    if field_end > len(stream_bytes):
        return None
    length_value = int.from_bytes(stream_bytes[field_start:field_end],
                                  "big" if endian == ">" else "little")
    header_total = field_end  # bytes from frame start through the length field
    payload_length = ((length_value - header_total) if includes_header
                      else length_value)
    if payload_length < 0:
        return None
    frame_end = field_end + payload_length
    if frame_end > len(stream_bytes):
        return None
    payload = stream_bytes[field_end:frame_end]
    if not _looks_like_xml_start(payload):
        return None  # only entertain candidates whose payload looks like XML

    score = 10 if payload[:5] == b"<?xml" else 6
    reasons = ["payload begins with " +
               ("an XML declaration" if payload[:5] == b"<?xml"
                else "an XML element")]
    remainder = stream_bytes[frame_end:]
    if not remainder:
        score += 2
        reasons.append("consumes exactly to the end of the buffer")
    elif any(remainder.startswith(signature) for signature in SIGNATURES):
        score += 5
        reasons.append("next bytes are another signature occurrence")
    else:
        score += 1

    return {
        "pad": pad, "length_size": length_size, "endian": endian,
        "includes_header": includes_header, "length_value": length_value,
        "field_start": field_start, "field_end": field_end,
        "frame_end": frame_end, "payload": payload,
        "score": score, "reasons": reasons,
    }


def infer_login_frame(stream_bytes: bytes) -> Optional[dict]:
    """Find the first signature occurrence and work out the header shape
    (padding/size/endianness/length-meaning) that makes the bytes right
    after it delimit a single XML payload."""
    signature_hits = find_signature_occurrences(stream_bytes)
    if not signature_hits:
        return None
    offset, signature = signature_hits[0]
    bytes_from_signature = stream_bytes[offset:]
    best = None
    for pad in _PAD_CANDIDATES:
        for length_size in _LENGTH_SIZE_CANDIDATES:
            for endian in _ENDIAN_CANDIDATES:
                for includes_header in (False, True):
                    candidate = _score_login_candidate(
                        bytes_from_signature, len(signature), pad,
                        length_size, endian, includes_header)
                    if candidate and (best is None
                                      or candidate["score"] > best["score"]):
                        best = candidate
    if best is None:
        return None
    best["signature"] = signature
    best["signature_offset"] = offset
    best["frame_end_absolute"] = offset + best["frame_end"]
    return best


def infer_command_frames(stream_bytes: bytes) -> Optional[dict]:
    """Walk the rest of a stream (after the login frame) as a repeating
    sequence of frames, trying both "signature repeats every frame" and
    "no signature, just length + XML" hypotheses. Returns the hypothesis
    that consumes the buffer exactly and cleanly, covering the most
    frames."""
    if not stream_bytes:
        return None
    best = None
    for frame_signature in (None,) + SIGNATURES:
        for pad in _PAD_CANDIDATES:
            for length_size in _LENGTH_SIZE_CANDIDATES:
                for endian in _ENDIAN_CANDIDATES:
                    for includes_header in (False, True):
                        result = _walk_frames(
                            stream_bytes, frame_signature, pad, length_size,
                            endian, includes_header)
                        if result is None:
                            continue
                        frames, consumed = result
                        if consumed != len(stream_bytes) or not frames:
                            # only accept configs that account for every
                            # byte
                            continue
                        score = len(frames) * 10
                        hypothesis = {
                            "signature": frame_signature, "pad": pad,
                            "length_size": length_size,
                            "endian": endian,
                            "includes_header": includes_header,
                            "frames": frames, "score": score,
                        }
                        if best is None or score > best["score"]:
                            best = hypothesis
    return best


def _walk_frames(stream_bytes: bytes, frame_signature: Optional[bytes],
                 pad: int, length_size: int,
                 endian: str, includes_header: bool):
    frames = []
    pos = 0
    while pos < len(stream_bytes):
        frame_start = pos
        cursor = pos
        if frame_signature is not None:
            if (stream_bytes[cursor:cursor + len(frame_signature)]
                    != frame_signature):
                return None
            cursor += len(frame_signature)
        field_start = cursor + pad
        field_end = field_start + length_size
        if field_end > len(stream_bytes):
            return None
        length_value = int.from_bytes(stream_bytes[field_start:field_end],
                                      "big" if endian == ">" else "little")
        header_total = field_end - frame_start
        payload_length = ((length_value - header_total) if includes_header
                          else length_value)
        if payload_length < 0:
            return None
        frame_end = field_end + payload_length
        if frame_end > len(stream_bytes):
            return None
        payload = stream_bytes[field_end:frame_end]
        if not _looks_like_xml_start(payload):
            return None
        frames.append({"start": frame_start, "field_start": field_start,
                       "length_value": length_value, "payload": payload})
        pos = frame_end
    return frames, pos


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _preview(payload: bytes, max_chars: int = 300) -> str:
    return payload[:max_chars].decode("utf-8", "replace")


def _format_login_layout(login: dict) -> str:
    signature = login["signature"]
    lines = [
        f"  offset 0{'':<10}: signature   {len(signature)} bytes  "
        f"{signature!r}",
        f"  offset {len(signature)+login['pad']}{'':<8}: length      "
        f"{login['length_size']} bytes  "
        f"{'big' if login['endian'] == '>' else 'little'}-endian uint  "
        f"(GUESS: {'includes header length' if login['includes_header'] else 'payload length only'})",  # noqa: E501
        f"  offset {login['field_end']}{'':<8}: payload     "
        f"{login['frame_end']-login['field_end']} bytes  XML",
    ]
    return "\n".join(lines)


def _format_command_layout(command_framing: dict) -> str:
    signature = command_framing["signature"]
    signature_length = len(signature) if signature else 0
    lines = []
    if signature:
        lines.append(f"  offset 0{'':<10}: signature   {signature_length} "
                     f"bytes  {signature!r}  "
                     f"(GUESS: repeats on every command frame)")
    else:
        lines.append("  (GUESS: no signature on command frames -- "
                     "only the login carries one)")
    lines.append(
        f"  offset {signature_length+command_framing['pad']}{'':<8}: "
        f"length      "
        f"{command_framing['length_size']} bytes  "
        f"{'big' if command_framing['endian'] == '>' else 'little'}"
        f"-endian uint  "
        f"(GUESS: {'includes header length' if command_framing['includes_header'] else 'payload length only'})")  # noqa: E501
    lines.append(f"  offset "
                 f"{signature_length+command_framing['pad']+command_framing['length_size']}"  # noqa: E501
                 f"{'':<8}: payload     XML")
    return "\n".join(lines)


def analyse_direction(label: str, stream_bytes: bytes, report: dict,
                      max_preview: int) -> None:
    print(f"\n--- {label}: {len(stream_bytes)} bytes reassembled ---")
    if not stream_bytes:
        print("  (nothing)")
        return

    signature_hits = find_signature_occurrences(stream_bytes)
    print(f"  signature occurrences: {len(signature_hits)}"
          + (f" (first at offset {signature_hits[0][0]}, "
             f"{signature_hits[0][1]!r})"
             if signature_hits else ""))

    direction_report: dict = {"bytes": len(stream_bytes),
                              "signature_hits": signature_hits}

    login = infer_login_frame(stream_bytes)
    if login is None:
        print("  could not identify a login frame (no signature found, or no "
              "header shape produced an XML-looking payload).")
        direction_report["login_frame"] = None
    else:
        print(f"  INFERRED login-frame header shape (score {login['score']}, "
              f"reasons: {'; '.join(login['reasons'])}):")
        print(_format_login_layout(login))
        print(f"  first decoded XML payload ({label}, login frame):")
        print("    " + _preview(login["payload"], max_preview).replace(
            "\n", "\n    "))
        direction_report["login_frame"] = {
            "signature": login["signature"].decode("ascii"),
            "signature_offset": login["signature_offset"],
            "pad": login["pad"], "length_size": login["length_size"],
            "endian": login["endian"],
            "includes_header": login["includes_header"],
            "length_value": login["length_value"],
            "payload_preview": _preview(login["payload"], max_preview),
        }

        remainder = stream_bytes[login["frame_end_absolute"]:]
        command_framing = infer_command_frames(remainder)
        if command_framing is None:
            if remainder:
                print(f"  {len(remainder)} bytes remain after the login "
                      "frame; no consistent command-frame framing was found "
                      "for them.")
            direction_report["command_frames"] = None
        else:
            print(f"\n  INFERRED command-frame header shape "
                  f"({len(command_framing['frames'])} frame(s) recovered):")
            print(_format_command_layout(command_framing))
            for i, frame in enumerate(command_framing["frames"][:3]):
                print(f"  first decoded XML payload ({label}, command "
                      f"frame {i}):")
                print("    " + _preview(frame["payload"], max_preview).replace(
                    "\n", "\n    "))
            direction_report["command_frames"] = {
                "signature": (command_framing["signature"].decode("ascii")
                              if command_framing["signature"] else None),
                "pad": command_framing["pad"],
                "length_size": command_framing["length_size"],
                "endian": command_framing["endian"],
                "includes_header": command_framing["includes_header"],
                "frame_count": len(command_framing["frames"]),
                "payload_previews": [
                    _preview(frame["payload"], max_preview)
                    for frame in command_framing["frames"][:5]],
            }

    report.setdefault("directions", {})[label] = direction_report


def print_proposed_header(report: dict, port: int) -> None:
    print("\n" + "=" * 72)
    print("PROPOSED NasdHeader layout -- INFERRED from this capture, not "
          "FIRMWARE")
    print("or CONFIRMED per docs/protocol-map.md's confidence marks. Treat "
          "as a")
    print("lead: verify against additional captures before hard-coding "
          "offsets.")
    print("=" * 72)
    found_login = False
    for label, direction_report in report.get("directions", {}).items():
        login = direction_report.get("login_frame")
        if not login:
            continue
        found_login = True
        print(f"\nLogin frame (first seen in direction: {label}):")
        signature = login["signature"].encode("ascii")
        pad, length_size, endian = (login["pad"], login["length_size"],
                                    login["endian"])
        print(f"  offset 0            : signature   {len(signature)} bytes  "
              f"b{login['signature']!r}")
        print(f"  offset {len(signature)+pad:<3}         : length      "
              f"{length_size} bytes  "
              f"{'big' if endian == '>' else 'little'}-endian uint  "
              f"(GUESS: {'includes header' if login['includes_header'] else 'payload length only'})")  # noqa: E501
        print(f"  offset {len(signature)+pad+length_size:<3}         : "
              f"payload     XML, variable length")
        command_framing = direction_report.get("command_frames")
        if command_framing:
            print(f"\nCommand frame(s) after login (direction: {label}):")
            if command_framing["signature"]:
                command_signature = command_framing["signature"].encode(
                    "ascii")
                print(f"  offset 0            : signature   "
                      f"{len(command_signature)} "
                      f"bytes  b{command_framing['signature']!r}  "
                      f"(GUESS: signature repeats every frame)")
                signature_length = len(command_signature)
            else:
                print("  (GUESS: no signature -- only the login frame "
                      "carries one)")
                signature_length = 0
            print(f"  offset {signature_length+command_framing['pad']:<3}"
                  f"         : length      "
                  f"{command_framing['length_size']} bytes  "
                  f"{'big' if command_framing['endian'] == '>' else 'little'}"
                  f"-endian uint  "
                  f"(GUESS: {'includes header' if command_framing['includes_header'] else 'payload length only'})")  # noqa: E501
            print(f"  offset "
                  f"{signature_length+command_framing['pad']+command_framing['length_size']:<3}"  # noqa: E501
                  f"         : payload     XML, variable length")
    if not found_login:
        print("\nNo signature/framing could be inferred from this capture "
              "-- either")
        print(f"the filter matched no port-{port} traffic, or none of the "
              "header shapes")
        print("tried produced an XML-looking payload. See "
              "docs/protocol-map.md and")
        print("fall back to a manual 'Follow TCP Stream' in Wireshark.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture",
                        help="path to a .pcap or .pcapng capture file")
    parser.add_argument("-o", "--out", help="write a JSON report to this path")
    parser.add_argument("--port", type=int, default=DEFAULT_STATUS_PORT,
                        help="TCP port to dissect: 5000 = status greeting, "
                        "5001 = commands (default: %(default)s)")
    parser.add_argument("--max-preview", type=int, default=400,
                        help="max characters of each XML payload to print "
                             "(default 400)")
    args = parser.parse_args(argv)

    try:
        packets = read_capture(args.capture)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Read {len(packets)} link-layer frames from {args.capture}")
    streams = collect_streams(packets, args.port)
    print(f"Found {len(streams)} TCP stream(s) touching port {args.port}")

    report: dict = {"capture": args.capture, "frame_count": len(packets),
                    "stream_count": len(streams), "streams": []}

    if not streams:
        print(f"\nNothing on port {args.port} in this capture -- check the "
              "Wireshark filter used when saving it, or that Dashboard "
              "actually connected during the recording.")
        print_proposed_header(report, args.port)
    else:
        for key, stream in streams.items():
            client = stream["client"]
            server = stream["server"]
            print(f"\n=== stream {client} <-> {server} ===")
            stream_report = {
                "client": list(client) if client else None,
                "server": list(server) if server else None,
                "directions": {},
            }
            to_server = reassemble(stream["to_server"])
            to_client = reassemble(stream["to_client"])
            analyse_direction(f"{client}->{server} (client->server)",
                              to_server, stream_report, args.max_preview)
            analyse_direction(f"{server}->{client} (server->client)",
                              to_client, stream_report, args.max_preview)
            report["streams"].append(stream_report)
            print_proposed_header(stream_report, args.port)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as report_file:
            json.dump(report, report_file, indent=2, default=str)
        print(f"\nWrote {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
