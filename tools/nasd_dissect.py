#!/usr/bin/env python3
"""
nasd_dissect.py - reconstruct nasd's request framing
from a packet capture.

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
import itertools
import json
import socket
import struct
import sys
from typing import Iterator, Optional

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

# The capture-file link types this reader understands by number (the
# tcpdump LINKTYPE_* registry). Anything else gets a best-effort guess.
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101  # an IP packet with no link header at all
LINKTYPE_LINUX_SLL = 113

# The IP protocol number of TCP, the only transport this tool follows.
IP_PROTOCOL_TCP = 6


# ---------------------------------------------------------------------------
# Capture file readers -- classic pcap and pcapng, parsed directly.
# Each returns a list of (timestamp, link_layer_frame_bytes, linktype).
# ---------------------------------------------------------------------------
# Magic -> (struct byte order, seconds per timestamp tick). The second
# pair of magics marks a nanosecond-resolution capture.
_CLASSIC_PCAP_MAGICS = {
    b"\xa1\xb2\xc3\xd4": (">", 1e-6),
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6),
    b"\xa1\xb2\x3c\x4d": (">", 1e-9),
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9),
}


def read_pcap_classic(
        capture_bytes: bytes) -> list[tuple[float, bytes, int]]:
    """Read every record of a classic pcap file.

    Parsed by hand so the tool needs nothing beyond the standard
    library. The magic decides both the byte order and whether the
    fractional timestamp counts micro- or nanoseconds, so the scale
    comes from the magic rather than being assumed.
    """
    endian, seconds_per_timestamp_tick = (
        _CLASSIC_PCAP_MAGICS[capture_bytes[0:4]])
    (_major, _minor, _timezone, _significant_figures, _snapshot_length,
     linktype) = struct.unpack_from(endian + "HHiIII", capture_bytes, 4)
    offset = 24
    packets: list[tuple[float, bytes, int]] = []
    while offset + 16 <= len(capture_bytes):
        (timestamp_seconds, timestamp_fraction, captured_length,
         _original_length) = struct.unpack_from(
             endian + "IIII", capture_bytes, offset)
        offset += 16
        if (captured_length < 0
                or offset + captured_length > len(capture_bytes)):
            break
        frame = capture_bytes[offset:offset + captured_length]
        offset += captured_length
        packets.append(
            (timestamp_seconds
             + timestamp_fraction * seconds_per_timestamp_tick,
             frame, linktype))
    return packets


PCAPNG_SECTION_HEADER_BLOCK = 0x0A0D0D0A
PCAPNG_INTERFACE_DESCRIPTION_BLOCK = 0x00000001
PCAPNG_SIMPLE_PACKET_BLOCK = 0x00000003
PCAPNG_ENHANCED_PACKET_BLOCK = 0x00000006


def _section_endianness(magic: bytes) -> str:
    """The struct byte order a section header block's magic announces.

    Raises ValueError for anything else, because a section whose byte
    order is unknown cannot be read at all.
    """
    if magic == b"\x1a\x2b\x3c\x4d":
        return ">"
    if magic == b"\x4d\x3c\x2b\x1a":
        return "<"
    raise ValueError(
        f"pcapng: bad byte-order magic {magic!r} "
        f"in section header block")


def _read_pcapng_block(block_type: int, block_body: bytes, endian: str,
                       interface_linktypes: list[int],
                       packets: list[tuple[float, bytes, int]]) -> None:
    """Take what this tool needs from one pcapng block body.

    An interface description adds its link type to
    `interface_linktypes`; a packet block adds its frame to `packets`.
    Other block types (name resolution, interface stats, comments, the
    obsolete "packet block") are skipped, because framing does not need
    them. Timestamps are not decoded here: nothing downstream reads
    them, so every packet carries 0.0.
    """
    if (block_type == PCAPNG_INTERFACE_DESCRIPTION_BLOCK
            and len(block_body) >= 8):
        linktype = struct.unpack_from(endian + "H", block_body, 0)[0]
        interface_linktypes.append(linktype)
    elif (block_type == PCAPNG_ENHANCED_PACKET_BLOCK
          and len(block_body) >= 20):
        interface_id = struct.unpack_from(endian + "I", block_body, 0)[0]
        captured_length = struct.unpack_from(
            endian + "I", block_body, 12)[0]
        frame = block_body[20:20 + captured_length]
        linktype = (interface_linktypes[interface_id]
                    if interface_id < len(interface_linktypes)
                    else LINKTYPE_ETHERNET)
        packets.append((0.0, frame, linktype))
    elif block_type == PCAPNG_SIMPLE_PACKET_BLOCK and len(block_body) >= 4:
        frame = block_body[4:]
        linktype = (interface_linktypes[0] if interface_linktypes
                    else LINKTYPE_ETHERNET)
        packets.append((0.0, frame, linktype))


def read_pcapng(capture_bytes: bytes) -> list[tuple[float, bytes, int]]:
    """Read every packet of a pcapng file.

    pcapng records its byte order per section, so each block type is
    read with the CURRENT section's endianness.

    It is safe to do this before the first section header block has
    told us the endianness, because 0x0A0D0D0A is a byte-palindrome --
    it reads the same either way, so the section header block is always
    recognised.

    Reading it as ">I" unconditionally was a real bug, and a quiet one.
    Every other block type is an ordinary little-endian integer in a
    little-endian capture, so an enhanced packet block (type 6, bytes
    06 00 00 00) read big-endian comes out as 0x06000000 and matches
    nothing. Block LENGTHS were already read with the right endianness,
    so the walk marched happily through the whole file, recognised not
    a single packet, and reported "Read 0 link-layer frames" -- which
    reads exactly like an empty capture rather than a broken parser.
    Every capture Windows produces is little-endian, so this never
    worked on a real file; the tests passed because their fixtures were
    big-endian.
    """
    packets: list[tuple[float, bytes, int]] = []
    endian = ">"  # provisional until the first SHB sets it for real
    # linktype per interface id, in this section
    interface_linktypes: list[int] = []
    block_offset = 0
    while block_offset + 12 <= len(capture_bytes):
        block_type = struct.unpack_from(
            endian + "I", capture_bytes, block_offset)[0]
        if block_type == PCAPNG_SECTION_HEADER_BLOCK:
            endian = _section_endianness(
                capture_bytes[block_offset + 8:block_offset + 12])
            interface_linktypes = []
        block_length_bytes = struct.unpack_from(
            endian + "I", capture_bytes, block_offset + 4)[0]
        if (block_length_bytes < 12
                or block_offset + block_length_bytes > len(capture_bytes)):
            break  # truncated/corrupt block -- stop rather than misread
        block_body = capture_bytes[
            block_offset + 8:block_offset + block_length_bytes - 4]
        _read_pcapng_block(block_type, block_body, endian,
                           interface_linktypes, packets)
        block_offset += block_length_bytes
    return packets


def read_capture(path: str) -> list[tuple[float, bytes, int]]:
    """Read a capture file of either format.

    The format is told from the file's magic rather than its extension,
    because captures get renamed and saved under the wrong suffix.
    """
    with open(path, "rb") as capture_file:
        capture_bytes = capture_file.read()
    if len(capture_bytes) < 4:
        raise ValueError(f"{path}: too small to be a capture file")
    if capture_bytes[0:4] in _CLASSIC_PCAP_MAGICS:
        return read_pcap_classic(capture_bytes)
    if (struct.unpack_from(">I", capture_bytes, 0)[0]
            == PCAPNG_SECTION_HEADER_BLOCK):
        return read_pcapng(capture_bytes)
    raise ValueError(
        f"{path}: not recognised as classic pcap or pcapng "
        f"(first bytes: {capture_bytes[0:4]!r})")


# ---------------------------------------------------------------------------
# Link layer / IP / TCP parsing -- just enough to get to a TCP payload.
# ---------------------------------------------------------------------------
def _strip_ethernet(frame: bytes) -> Optional[bytes]:
    """The IP packet inside an Ethernet frame, skipping any VLAN tags
    (802.1Q and QinQ), or None when it carries something other than
    IP."""
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


def _strip_linux_sll(frame: bytes) -> Optional[bytes]:
    """The IP packet inside a Linux "cooked" (SLL) frame, whose 16-byte
    header ends with the protocol type."""
    if len(frame) < 16:
        return None
    protocol = struct.unpack_from(">H", frame, 14)[0]
    if protocol in (ETH_TYPE_IPV4, ETH_TYPE_IPV6):
        return frame[16:]
    return None


def _guess_link_layer(frame: bytes) -> Optional[bytes]:
    """Best-effort for a link type this reader does not know: a frame
    that starts with an IP version nibble is taken as raw IP, otherwise
    as untagged Ethernet."""
    if frame and (frame[0] >> 4) in (4, 6):
        return frame
    if len(frame) >= 14:
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        if ethertype in (ETH_TYPE_IPV4, ETH_TYPE_IPV6):
            return frame[14:]
    return None


def strip_link_layer(frame: bytes, linktype: int) -> Optional[bytes]:
    """The IP packet inside one captured frame, or None.

    Windows (npcap) captures are Ethernet, Linux "any" captures are
    SLL, and some tools write raw IP, so each framing is handled; an
    unknown link type falls back to a guess rather than dropping the
    capture.
    """
    if linktype == LINKTYPE_ETHERNET:
        return _strip_ethernet(frame)
    if linktype == LINKTYPE_RAW:
        return frame
    if linktype == LINKTYPE_LINUX_SLL:
        return _strip_linux_sll(frame)
    return _guess_link_layer(frame)


def parse_ip(packet: bytes):
    """Returns (protocol, source_ip, destination_ip, payload)
    or None.

    IPv6 extension headers are deliberately not walked: a Drobo on a
    LAN sends bare IPv6, so only "next header is the transport" packets
    are understood, and the next-header value is returned as the
    protocol.
    """
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
    data) or None.

    The data offset is clamped to 20 bytes at least and to the segment
    at most, so a corrupt header yields a short or empty payload rather
    than an exception or bytes from before the payload.
    """
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
def _tcp_segment_on_port(frame: bytes, linktype: int, port: int):
    """(source_ip, source_port, destination_ip, destination_port,
    sequence_number, data) for a TCP segment to or from `port` that
    carries data, or None for anything else (non-IP, non-TCP, another
    port, or a bare ACK/SYN with nothing to reassemble)."""
    ip_packet = strip_link_layer(frame, linktype)
    if ip_packet is None:
        return None
    ip_fields = parse_ip(ip_packet)
    if ip_fields is None:
        return None
    protocol, source_ip, destination_ip, payload = ip_fields
    if protocol != IP_PROTOCOL_TCP:
        return None
    tcp_fields = parse_tcp(payload)
    if tcp_fields is None:
        return None
    source_port, destination_port, sequence_number, _flags, data = (
        tcp_fields)
    if source_port != port and destination_port != port:
        return None
    if not data:
        return None  # pure ACK/SYN, nothing to reassemble
    return (source_ip, source_port, destination_ip, destination_port,
            sequence_number, data)


def collect_streams(packets: list[tuple[float, bytes, int]],
                    port: int = DEFAULT_STATUS_PORT) -> dict:
    """Group TCP payload segments for `port` by connection
    and direction.

    A connection is keyed by its two endpoints regardless of which one
    sent the segment, and the side that owns `port` is taken as the
    server, so the direction is right even when the capture starts
    after the handshake.

    Returns {stream_key: {"to_server": [(sequence_number, bytes), ...],
                           "to_client": [(sequence_number, bytes), ...],
                           "client": (ip, port), "server": (ip, port)}}
    """
    streams: dict = {}
    for _timestamp_seconds, frame, linktype in packets:
        segment = _tcp_segment_on_port(frame, linktype, port)
        if segment is None:
            continue
        (source_ip, source_port, destination_ip, destination_port,
         sequence_number, data) = segment
        connection_key = frozenset({(source_ip, source_port),
                                    (destination_ip, destination_port)})
        stream = streams.setdefault(
            connection_key, {"to_server": [], "to_client": [],
                             "client": None, "server": None})
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
        if next_sequence is None or sequence_number >= next_sequence:
            stitched += data
            next_sequence = sequence_number + len(data)
            continue
        overlap = next_sequence - sequence_number
        if len(data) <= overlap:
            continue  # fully-contained retransmission, skip
        stitched += data[overlap:]
        next_sequence = sequence_number + len(data)
    return bytes(stitched)


# ---------------------------------------------------------------------------
# Signature search
# ---------------------------------------------------------------------------
def _offsets_of(stream_bytes: bytes, signature: bytes) -> Iterator[int]:
    """Every offset where `signature` starts, overlapping matches
    included."""
    start = 0
    while True:
        index = stream_bytes.find(signature, start)
        if index == -1:
            return
        yield index
        start = index + 1


def find_signature_occurrences(stream_bytes: bytes) -> list[tuple[int, bytes]]:
    """Every offset where DRINASD or DRINASD4 appears, longest match
    wins when both match the same offset (DRINASD is a prefix of
    DRINASD4)."""
    signature_hits: dict[int, bytes] = {}
    matches = ((index, signature) for signature in SIGNATURES
               for index in _offsets_of(stream_bytes, signature))
    for index, signature in matches:
        if (index not in signature_hits
                or len(signature) > len(signature_hits[index])):
            signature_hits[index] = signature
    return sorted(signature_hits.items())


def _looks_like_xml_start(payload: bytes) -> bool:
    """Whether `payload` opens like an XML document.

    This is the only evidence the inference has that a header shape is
    right, so it is kept strict: an XML declaration, or "<" followed by
    a letter.
    """
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
_PAD_BYTE_CANDIDATES = (0, 1, 2, 4)
_LENGTH_FIELD_BYTE_CANDIDATES = (4, 2)
_ENDIAN_CANDIDATES = (">", "<")


def _header_shape_candidates() -> Iterator[tuple[int, int, str, bool]]:
    """Every (pad_bytes, length_field_bytes, endian, includes_header)
    shape to try, always in the same order.

    The order matters: the callers keep the first of several equally
    good candidates, so changing it would change which shape a tie
    reports.
    """
    return itertools.product(_PAD_BYTE_CANDIDATES,
                             _LENGTH_FIELD_BYTE_CANDIDATES,
                             _ENDIAN_CANDIDATES, (False, True))


def _score_login_payload(payload: bytes,
                         remainder: bytes) -> tuple[int, list[str]]:
    """How convincing a candidate login frame is, and why.

    An XML declaration beats a bare element, and a frame that ends
    exactly where the buffer or the next signature begins beats one
    that leaves stray bytes behind.
    """
    score = 10 if payload[:5] == b"<?xml" else 6
    reasons = ["payload begins with " +
               ("an XML declaration" if payload[:5] == b"<?xml"
                else "an XML element")]
    if not remainder:
        score += 2
        reasons.append("consumes exactly to the end of the buffer")
    elif any(remainder.startswith(signature) for signature in SIGNATURES):
        score += 5
        reasons.append("next bytes are another signature occurrence")
    else:
        score += 1
    return score, reasons


def _score_login_candidate(stream_bytes: bytes, signature_length: int,
                           pad_bytes: int, length_field_bytes: int,
                           endian: str,
                           includes_header: bool) -> Optional[dict]:
    """Read `stream_bytes` (which starts at a signature) as one frame
    of the given header shape, or return None when that shape does not
    delimit an XML-looking payload."""
    field_start = signature_length + pad_bytes
    field_end = field_start + length_field_bytes
    if field_end > len(stream_bytes):
        return None
    length_value = int.from_bytes(stream_bytes[field_start:field_end],
                                  "big" if endian == ">" else "little")
    # bytes from frame start through the length field
    header_length_bytes = field_end
    payload_length = ((length_value - header_length_bytes)
                      if includes_header else length_value)
    if payload_length < 0:
        return None
    frame_end = field_end + payload_length
    if frame_end > len(stream_bytes):
        return None
    payload = stream_bytes[field_end:frame_end]
    if not _looks_like_xml_start(payload):
        return None  # only entertain candidates whose payload looks like XML

    score, reasons = _score_login_payload(payload, stream_bytes[frame_end:])
    return {
        "pad": pad_bytes, "length_size": length_field_bytes,
        "endian": endian,
        "includes_header": includes_header, "length_value": length_value,
        "field_start": field_start, "field_end": field_end,
        "frame_end": frame_end, "payload": payload,
        "score": score, "reasons": reasons,
    }


def infer_login_frame(stream_bytes: bytes) -> Optional[dict]:
    """Find the first signature occurrence and work out the header shape
    (padding/size/endianness/length-meaning) that makes the bytes right
    after it delimit a single XML payload.

    The shape is inferred from the bytes rather than taken from
    docs/protocol-map.md, so a capture can test that document's
    hypothesis instead of assuming it.
    """
    signature_hits = find_signature_occurrences(stream_bytes)
    if not signature_hits:
        return None
    offset, signature = signature_hits[0]
    bytes_from_signature = stream_bytes[offset:]
    best_candidate = None
    for shape in _header_shape_candidates():
        candidate = _score_login_candidate(
            bytes_from_signature, len(signature), *shape)
        if candidate and (best_candidate is None
                          or candidate["score"] > best_candidate["score"]):
            best_candidate = candidate
    if best_candidate is None:
        return None
    best_candidate["signature"] = signature
    best_candidate["signature_offset"] = offset
    best_candidate["frame_end_absolute"] = (
        offset + best_candidate["frame_end"])
    return best_candidate


def _command_frame_hypothesis(stream_bytes: bytes,
                              frame_signature: Optional[bytes],
                              pad_bytes: int, length_field_bytes: int,
                              endian: str,
                              includes_header: bool) -> Optional[dict]:
    """The hypothesis that every frame in `stream_bytes` has this
    shape, scored by the number of frames, or None unless it accounts
    for every byte."""
    walk_result = _walk_frames(
        stream_bytes, frame_signature, pad_bytes, length_field_bytes,
        endian, includes_header)
    if walk_result is None:
        return None
    frames, consumed = walk_result
    if consumed != len(stream_bytes) or not frames:
        # only accept configs that account
        # for every byte
        return None
    score = len(frames) * 10
    return {
        "signature": frame_signature, "pad": pad_bytes,
        "length_size": length_field_bytes,
        "endian": endian,
        "includes_header": includes_header,
        "frames": frames, "score": score,
    }


def infer_command_frames(stream_bytes: bytes) -> Optional[dict]:
    """Walk the rest of a stream (after the login frame) as a repeating
    sequence of frames, trying both "signature repeats every frame" and
    "no signature, just length + XML" hypotheses. Returns the hypothesis
    that consumes the buffer exactly and cleanly, covering the most
    frames."""
    if not stream_bytes:
        return None
    best_hypothesis = None
    # The signature varies slowest, then the header shape, so a tie
    # keeps the same hypothesis it always has.
    for frame_signature, shape in itertools.product(
            (None,) + SIGNATURES, _header_shape_candidates()):
        hypothesis = _command_frame_hypothesis(
            stream_bytes, frame_signature, *shape)
        if hypothesis is None:
            continue
        if (best_hypothesis is None
                or hypothesis["score"] > best_hypothesis["score"]):
            best_hypothesis = hypothesis
    return best_hypothesis


def _frame_at(stream_bytes: bytes, frame_start: int,
              frame_signature: Optional[bytes], pad_bytes: int,
              length_field_bytes: int, endian: str,
              includes_header: bool) -> Optional[tuple[dict, int]]:
    """The frame that starts at `frame_start` under this header shape,
    and the offset just past it, or None when the bytes there do not
    fit the shape."""
    cursor = frame_start
    if frame_signature is not None:
        if (stream_bytes[cursor:cursor + len(frame_signature)]
                != frame_signature):
            return None
        cursor += len(frame_signature)
    field_start = cursor + pad_bytes
    field_end = field_start + length_field_bytes
    if field_end > len(stream_bytes):
        return None
    length_value = int.from_bytes(stream_bytes[field_start:field_end],
                                  "big" if endian == ">" else "little")
    header_length_bytes = field_end - frame_start
    payload_length = ((length_value - header_length_bytes)
                      if includes_header else length_value)
    if payload_length < 0:
        return None
    frame_end = field_end + payload_length
    if frame_end > len(stream_bytes):
        return None
    payload = stream_bytes[field_end:frame_end]
    if not _looks_like_xml_start(payload):
        return None
    frame = {"start": frame_start, "field_start": field_start,
             "length_value": length_value, "payload": payload}
    return frame, frame_end


def _walk_frames(stream_bytes: bytes, frame_signature: Optional[bytes],
                 pad_bytes: int, length_field_bytes: int,
                 endian: str, includes_header: bool):
    """Read `stream_bytes` as back-to-back frames of one header shape.

    Returns (frames, bytes consumed), or None as soon as one frame does
    not fit, because a shape that is wrong anywhere is wrong.
    """
    frames = []
    frame_offset = 0
    while frame_offset < len(stream_bytes):
        frame_and_end = _frame_at(
            stream_bytes, frame_offset, frame_signature, pad_bytes,
            length_field_bytes, endian, includes_header)
        if frame_and_end is None:
            return None
        frame, frame_offset = frame_and_end
        frames.append(frame)
    return frames, frame_offset


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _preview(payload: bytes, max_bytes: int = 300) -> str:
    """The start of a payload as text for the report.

    It slices bytes before decoding, so the limit counts bytes, and an
    invalid sequence shows as U+FFFD rather than stopping the report.
    """
    return payload[:max_bytes].decode("utf-8", "replace")


def _format_login_layout(login: dict) -> str:
    """The inferred login-frame header as aligned offset lines, each
    marked as a guess where the capture cannot prove it."""
    signature = login["signature"]
    length_basis = ("includes header length" if login["includes_header"]
                    else "payload length only")
    lines = [
        f"  offset 0{'':<10}: signature   {len(signature)} bytes  "
        f"{signature!r}",
        f"  offset {len(signature)+login['pad']}{'':<8}: length      "
        f"{login['length_size']} bytes  "
        f"{'big' if login['endian'] == '>' else 'little'}-endian uint  "
        f"(GUESS: {length_basis})",
        f"  offset {login['field_end']}{'':<8}: payload     "
        f"{login['frame_end']-login['field_end']} bytes  XML",
    ]
    return "\n".join(lines)


def _format_command_layout(command_framing: dict) -> str:
    """The inferred command-frame header as aligned offset lines, each
    marked as a guess where the capture cannot prove it."""
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
    length_basis = ("includes header length"
                    if command_framing["includes_header"]
                    else "payload length only")
    payload_offset = (signature_length + command_framing["pad"]
                      + command_framing["length_size"])
    lines.append(
        f"  offset {signature_length+command_framing['pad']}{'':<8}: "
        f"length      {command_framing['length_size']} bytes  "
        f"{'big' if command_framing['endian'] == '>' else 'little'}"
        f"-endian uint  "
        f"(GUESS: {length_basis})")
    lines.append(f"  offset {payload_offset}{'':<8}: payload     XML")
    return "\n".join(lines)


def _report_login_frame(direction_label: str, login: dict,
                        max_preview_bytes: int) -> dict:
    """Print the inferred login frame and return its JSON report
    entry."""
    print(f"  INFERRED login-frame header shape (score {login['score']}, "
          f"reasons: {'; '.join(login['reasons'])}):")
    print(_format_login_layout(login))
    print(f"  first decoded XML payload ({direction_label}, login frame):")
    print("    " + _preview(login["payload"], max_preview_bytes).replace(
        "\n", "\n    "))
    return {
        "signature": login["signature"].decode("ascii"),
        "signature_offset": login["signature_offset"],
        "pad": login["pad"], "length_size": login["length_size"],
        "endian": login["endian"],
        "includes_header": login["includes_header"],
        "length_value": login["length_value"],
        "payload_preview": _preview(login["payload"], max_preview_bytes),
    }


def _report_command_frames(direction_label: str, remainder: bytes,
                           max_preview_bytes: int) -> Optional[dict]:
    """Infer the framing of the bytes after the login frame, print it,
    and return its JSON report entry (None when no framing fits)."""
    command_framing = infer_command_frames(remainder)
    if command_framing is None:
        if remainder:
            print(f"  {len(remainder)} bytes remain after the login "
                  "frame; no consistent command-frame framing "
                  "was found for them.")
        return None
    print(f"\n  INFERRED command-frame header shape "
          f"({len(command_framing['frames'])} frame(s) recovered):")
    print(_format_command_layout(command_framing))
    for frame_index, frame in enumerate(command_framing["frames"][:3]):
        print(f"  first decoded XML payload ({direction_label}, "
              f"command frame {frame_index}):")
        print("    " + _preview(frame["payload"], max_preview_bytes).replace(
            "\n", "\n    "))
    return {
        "signature": (command_framing["signature"].decode("ascii")
                      if command_framing["signature"] else None),
        "pad": command_framing["pad"],
        "length_size": command_framing["length_size"],
        "endian": command_framing["endian"],
        "includes_header": command_framing["includes_header"],
        "frame_count": len(command_framing["frames"]),
        "payload_previews": [
            _preview(frame["payload"], max_preview_bytes)
            for frame in command_framing["frames"][:5]],
    }


def analyse_direction(direction_label: str, stream_bytes: bytes,
                      stream_report: dict, max_preview_bytes: int) -> None:
    """Infer and print the framing of one direction of a stream, and
    file what was found under `stream_report["directions"]`.

    The login frame is inferred first because only it is known to
    carry a signature; the bytes after it are then tried as command
    frames.
    """
    print(f"\n--- {direction_label}: {len(stream_bytes)} bytes "
          f"reassembled ---")
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
        direction_report["login_frame"] = _report_login_frame(
            direction_label, login, max_preview_bytes)
        direction_report["command_frames"] = _report_command_frames(
            direction_label, stream_bytes[login["frame_end_absolute"]:],
            max_preview_bytes)

    stream_report.setdefault("directions", {})[direction_label] = (
        direction_report)


def _print_login_header(direction_label: str, login: dict) -> None:
    """The proposed login-frame header, from one direction's report."""
    print(f"\nLogin frame (first seen in direction: {direction_label}):")
    signature = login["signature"].encode("ascii")
    pad_bytes, length_field_bytes, endian = (
        login["pad"], login["length_size"], login["endian"])
    print(f"  offset 0            : signature   {len(signature)} bytes  "
          f"b{login['signature']!r}")
    login_length_basis = ("includes header"
                          if login["includes_header"]
                          else "payload length only")
    print(f"  offset {len(signature)+pad_bytes:<3}         : length      "
          f"{length_field_bytes} bytes  "
          f"{'big' if endian == '>' else 'little'}-endian uint  "
          f"(GUESS: {login_length_basis})")
    print(f"  offset {len(signature)+pad_bytes+length_field_bytes:<3}"
          f"         : "
          f"payload     XML, variable length")


def _print_command_header(direction_label: str,
                          command_framing: dict) -> None:
    """The proposed command-frame header, from one direction's
    report."""
    print(f"\nCommand frame(s) after login (direction: {direction_label}):")
    if command_framing["signature"]:
        command_signature = command_framing["signature"].encode(
            "ascii")
        print(f"  offset 0            : signature   "
              f"{len(command_signature)} "
              f"bytes  b{command_framing['signature']!r}  "
              f"(GUESS: signature repeats every frame)")
        signature_length = len(command_signature)
    else:
        print("  (GUESS: no signature -- "
              "only the login frame carries one)")
        signature_length = 0
    command_length_basis = ("includes header"
                            if command_framing["includes_header"]
                            else "payload length only")
    payload_offset = (signature_length + command_framing["pad"]
                      + command_framing["length_size"])
    print(f"  offset {signature_length+command_framing['pad']:<3}"
          f"         : length      "
          f"{command_framing['length_size']} bytes  "
          f"{'big' if command_framing['endian'] == '>' else 'little'}"
          f"-endian uint  "
          f"(GUESS: {command_length_basis})")
    print(f"  offset {payload_offset:<3}"
          f"         : payload     XML, variable length")


def print_proposed_header(report: dict, port: int) -> None:
    """Print the header layout this capture suggests.

    Everything here is labelled INFERRED and GUESS because it comes
    from one capture's evidence, not from the firmware or a confirmed
    measurement, and docs/protocol-map.md keeps those apart.
    """
    print("\n" + "=" * 72)
    print("PROPOSED NasdHeader layout -- "
          "INFERRED from this capture, not FIRMWARE")
    print("or CONFIRMED per docs/protocol-map.md's confidence marks. "
          "Treat as a")
    print("lead: verify against additional captures "
          "before hard-coding offsets.")
    print("=" * 72)
    found_login = False
    for direction_label, direction_report in report.get(
            "directions", {}).items():
        login = direction_report.get("login_frame")
        if not login:
            continue
        found_login = True
        _print_login_header(direction_label, login)
        command_framing = direction_report.get("command_frames")
        if command_framing:
            _print_command_header(direction_label, command_framing)
    if not found_login:
        print("\nNo signature/framing could be inferred "
              "from this capture -- either")
        print(f"the filter matched no port-{port} traffic, or none of the "
              "header shapes")
        print("tried produced an XML-looking payload. See "
              "docs/protocol-map.md and")
        print("fall back to a manual 'Follow TCP Stream' in Wireshark.")


def _build_parser() -> argparse.ArgumentParser:
    """The command line; the module docstring is its description."""
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
                        help="max characters of each XML payload "
                             "to print (default 400)")
    return parser


def _analyse_stream(stream: dict, port: int,
                    max_preview_bytes: int) -> dict:
    """Reassemble and analyse both directions of one connection, print
    the header they suggest, and return the stream's report."""
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
                      to_server, stream_report, max_preview_bytes)
    analyse_direction(f"{server}->{client} (server->client)",
                      to_client, stream_report, max_preview_bytes)
    print_proposed_header(stream_report, port)
    return stream_report


def main(argv=None) -> int:
    """Dissect one capture: print the inferred framing of every stream
    on --port, and write the JSON report when -o is given.

    Returns 1 when the capture cannot be read, otherwise 0, even when
    nothing could be inferred, because "no framing found" is a result.
    """
    args = _build_parser().parse_args(argv)

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
        for stream in streams.values():
            report["streams"].append(
                _analyse_stream(stream, args.port, args.max_preview))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as report_file:
            json.dump(report, report_file, indent=2, default=str)
        print(f"\nWrote {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
