"""
Byte framing for the nasd protocol.

Two different things live in this file, and they have very different
confidence levels -- do not conflate them:

1. A generic length-prefixed frame (encode_frame / decode_frame /
   FrameAssembler). protocol-map.md's "Connection model" section quotes
   the daemon's own error strings as evidence that commands are
   length-framed:

       NetTMCmds(thread %p/from %s): can't get a complete header.
       Size %d, Received %d.

   i.e. *something* declares a size before the payload arrives. A 4-byte
   length prefix ahead of a payload is the simplest thing that produces
   that exact error shape, so it's used here as the working assumption
   for the command/response path -- but the exact width, byte order, and
   whether the length includes itself are still `GUESS`, pending a
   capture.

2. NasdHeader -- the connection/login handshake header, as guessed
   before a capture existed. **CORRECTION, 2026-07-25:** the real header
   and login shape were captured and confirmed against live hardware --
   see docs/protocol-map.md, "THE COMMAND PROTOCOL -- SOLVED". None of
   the CANDIDATE constructors below turned out to match it: the
   confirmed shape is a 16-byte header (`DRINETTM` signature + 1-byte
   type + 1-byte version + 2-byte flags + big-endian u32 length)
   followed by a 220-byte login payload that is just the device's serial
   (ESAID) twice, NUL-padded. That confirmed framing is implemented
   directly in `config_cmd.py` (`_build_frame`, `_login_payload`), not
   through this class. The CANDIDATE constructors here are left as-is --
   superseded, not deleted -- so `client.NasdClient`'s existing tests
   keep exercising the same code; don't add a new candidate pretending
   to be the confirmed answer, and don't route new work through this
   class expecting it to reach real hardware.

The frame-size assumption in part 1 (a bare 4-byte length prefix, no
signature) is also superseded for the command channel specifically --
the confirmed frame there always carries the `DRINETTM` signature and
type/version bytes ahead of the length (see above). Whether a bare
4-byte-prefix frame like this is used anywhere else in the protocol is
untested and still a `GUESS`.
"""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. generic length-prefixed frame (command / response path)
# ---------------------------------------------------------------------------


class FramingError(Exception):
    """Raised when a buffer does not (yet) hold one complete frame.

    An exception rather than a None return so a caller cannot mistake
    "not complete yet" for an empty payload: b"" is a valid frame.
    FrameAssembler catches it as its signal to wait for more bytes.
    """


class FrameTooLargeError(FramingError):
    """A length prefix declares more than MAX_FRAME_BYTES.

    A FramingError so existing handlers still catch it, but its own
    type, because it is not "not complete yet": no number of further
    reads completes it, and FrameAssembler must report it rather than
    wait.
    """


# The most a frame's payload may declare: the same 4 MiB cap the
# confirmed command channel applies (config_cmd.MAX_PAYLOAD_BYTES),
# repeated here rather than imported so this speculative module keeps
# depending on nothing else in the package. Until 2026-11-02 there was
# no cap, and a prefix of 0xFFFFFFFF kept FrameAssembler buffering
# without bound for a frame that would never arrive.
MAX_FRAME_BYTES = 4 * 1024 * 1024

_LENGTH_STRUCTS = {
    "big": struct.Struct(">I"),
    "little": struct.Struct("<I"),
}


def encode_frame(payload: bytes, *, byteorder: str = "big") -> bytes:
    """4-byte length prefix (payload length only, prefix not included) +
    payload. `byteorder` is a GUESS -- see module docstring.

    The byte order is a keyword so both guesses can be tried against a
    capture without changing any caller; anything but "big" or "little"
    is a ValueError rather than a silent default."""
    if byteorder not in _LENGTH_STRUCTS:
        raise ValueError(
            f"byteorder must be 'big' or 'little', got {byteorder!r}"
        )
    return _LENGTH_STRUCTS[byteorder].pack(len(payload)) + payload


def decode_frame(buffered_bytes: bytes, *,
                 byteorder: str = "big") -> Tuple[bytes, bytes]:
    """
    Decode one frame from the front of `buffered_bytes`.
    Returns (payload, remainder).

    Raises FramingError if `buffered_bytes` does not yet contain a
    complete frame -- the caller should read more bytes from the socket
    and retry (this is exactly the "Size %d, Received %d" situation the
    daemon logs) -- and FrameTooLargeError as soon as the length prefix
    declares more than MAX_FRAME_BYTES, which no retry can complete.
    """
    length_struct = _LENGTH_STRUCTS.get(byteorder)
    if length_struct is None:
        raise ValueError(
            f"byteorder must be 'big' or 'little', got {byteorder!r}"
        )
    if len(buffered_bytes) < length_struct.size:
        raise FramingError(
            f"need {length_struct.size} bytes "
            f"for the length prefix, have {len(buffered_bytes)}"
        )
    (payload_length,) = length_struct.unpack(
        buffered_bytes[:length_struct.size])
    if payload_length > MAX_FRAME_BYTES:
        raise FrameTooLargeError(
            f"frame declares {payload_length} bytes, more than the "
            f"{MAX_FRAME_BYTES}-byte cap")
    frame_end_offset = length_struct.size + payload_length
    if len(buffered_bytes) < frame_end_offset:
        raise FramingError(
            f"incomplete frame: need {payload_length} bytes, "
            f"have {len(buffered_bytes) - length_struct.size}"
        )
    return (buffered_bytes[length_struct.size:frame_end_offset],
            buffered_bytes[frame_end_offset:])


class FrameAssembler:
    """
    Buffers bytes arriving off a socket and yields complete frames as
    they become available. This is the streaming-friendly wrapper around
    encode_frame/decode_frame -- a real socket delivers partial reads,
    and this is where that gets absorbed.
    """

    def __init__(self, byteorder: str = "big"):
        """Start with an empty buffer; `byteorder` is decode_frame's
        guess, fixed for the life of the connection."""
        self._buffer = b""
        self._byteorder = byteorder

    def feed(self, received_bytes: bytes) -> List[bytes]:
        """
        Add bytes from one read and return every frame now complete,
        oldest first (possibly none).

        A partial frame stays buffered for the next call, because a
        socket read ends wherever the network happened to split the
        data, not on a frame boundary. A frame declaring more than
        MAX_FRAME_BYTES raises FrameTooLargeError instead of buffering.
        """
        self._buffer += received_bytes
        frames: List[bytes] = []
        while True:
            try:
                payload, remainder = decode_frame(
                    self._buffer, byteorder=self._byteorder
                )
            except FrameTooLargeError:
                # Not "wait for more": no further read completes it.
                raise
            except FramingError:
                break
            frames.append(payload)
            self._buffer = remainder
        return frames

    def pending_bytes(self) -> int:
        """How many bytes are buffered towards a frame not yet
        complete."""
        return len(self._buffer)


# ---------------------------------------------------------------------------
# 2. NasdHeader -- THE UNKNOWN.
#    Everything below is a CANDIDATE, not fact.
# ---------------------------------------------------------------------------


class NasdHeader:
    """
    The connection/login handshake header. THIS IS THE ONE UNRESOLVED
    PIECE of the whole client library -- see docs/protocol-map.md "Still
    open".

    Confirmed by firmware string evidence (protocol-map.md "Connection
    model" + "Signatures"):

      - A fixed-size header precedes the login packet and carries a
        signature ("got bad signature" is a distinct failure from "did
        not receive complete header", implying the header is read, then
        validated, as two separate steps).
      - Two signature strings exist side by side in the firmware:
        "DRINASD" and "DRINASD4". `GUESS`: DRINASD4 is current, DRINASD
        is older/legacy.
      - The login packet itself is a *separate* subsequent read ("did
        not receive complete login packet"), so header != login packet.

    NOT confirmed, at all:
      - Byte order, header length, where a length field sits
        (before/after the signature, or absent and implied by a fixed
        size instead).
      - Whether there's anything else in the header (version, esaID,
        flags).
      - The login packet's contents (protocol-map.md: PAM-backed auth,
        UCrypto for stored passwords, but "sent in clear over TCP 5000"
        is itself only a `GUESS`).

    TODO(packet-capture): capture a real Dashboard handshake (see
    tools/nasd_probe.py or docs/archive/phase-1-capture-playbook.md),
    confirm which candidate below (if any) matches, delete the others,
    and rename the survivor to drop the "candidate_" prefix. Until that
    happens, do not add a candidate_d/e/... without also adding the
    evidence for it above.
    """

    SIGNATURE_V4 = b"DRINASD4"
    # padded to 8 bytes to match SIGNATURE_V4's width -- GUESS
    SIGNATURE_LEGACY = b"DRINASD\x00"
    # Candidates A and B are both an 8-byte signature and a 4-byte
    # length, in one order or the other.
    _CANDIDATE_HEADER_BYTES = 12

    # -- candidate encoders
    # -------------------------------------------------- Each takes the
    # login/command payload length and returns header bytes to prepend.
    # None of these have been checked against a real Drobo.

    @classmethod
    def candidate_a_signature_then_length(
        cls, payload_length: int, *, signature: Optional[bytes] = None
    ) -> bytes:
        """CANDIDATE A: [8-byte signature]['DRINASD4' by default][4-byte
        big-endian payload length]. UNCONFIRMED."""
        chosen_signature = (signature if signature is not None
                            else cls.SIGNATURE_V4)
        return chosen_signature + struct.pack(">I", payload_length)

    @classmethod
    def candidate_b_length_then_signature(
        cls, payload_length: int, *, signature: Optional[bytes] = None
    ) -> bytes:
        """CANDIDATE B: [4-byte little-endian payload length][8-byte
        signature]. UNCONFIRMED."""
        chosen_signature = (signature if signature is not None
                            else cls.SIGNATURE_V4)
        return struct.pack("<I", payload_length) + chosen_signature

    @classmethod
    def candidate_c_legacy_signature(cls, payload_length: int) -> bytes:
        """CANDIDATE C: same shape as A, but with the older 'DRINASD'
        signature instead of 'DRINASD4'. UNCONFIRMED."""
        return cls.candidate_a_signature_then_length(
            payload_length, signature=cls.SIGNATURE_LEGACY
        )

    # -- matching decoders
    # ---------------------------------------------------- Paired 1:1
    # with the encoders above, for testing internal consistency only
    # (encode-then-decode round trips). This proves nothing about what a
    # real Drobo would send -- see the class docstring.

    @classmethod
    def parse_candidate_a(cls, header_bytes: bytes) -> Tuple[bytes, int]:
        """Inverse of candidate_a_signature_then_length.
        Returns (signature, payload_length).
        """
        if len(header_bytes) < cls._CANDIDATE_HEADER_BYTES:
            raise FramingError(
                "candidate A header is 12 bytes (8 signature + 4 length)"
            )
        signature, length_bytes = header_bytes[:8], header_bytes[8:12]
        (payload_length,) = struct.unpack(">I", length_bytes)
        return signature, payload_length

    @classmethod
    def parse_candidate_b(cls, header_bytes: bytes) -> Tuple[bytes, int]:
        """Inverse of candidate_b_length_then_signature.
        Returns (signature, payload_length).
        """
        if len(header_bytes) < cls._CANDIDATE_HEADER_BYTES:
            raise FramingError(
                "candidate B header is 12 bytes (4 length + 8 signature)"
            )
        length_bytes, signature = header_bytes[:4], header_bytes[4:12]
        (payload_length,) = struct.unpack("<I", length_bytes)
        return signature, payload_length

    @classmethod
    def parse_candidate_c(cls, header_bytes: bytes) -> Tuple[bytes, int]:
        """Inverse of candidate_c_legacy_signature."""
        return cls.parse_candidate_a(header_bytes)

    # Registry so client.py (or a future capture-driven picker) can
    # iterate candidates instead of hard-coding one. Order is not
    # significance.
    CANDIDATES: dict = {}  # populated below, after the classmethods exist


NasdHeader.CANDIDATES = {
    "a_signature_then_length": (
        NasdHeader.candidate_a_signature_then_length,
        NasdHeader.parse_candidate_a,
    ),
    "b_length_then_signature": (
        NasdHeader.candidate_b_length_then_signature,
        NasdHeader.parse_candidate_b,
    ),
    "c_legacy_signature": (
        NasdHeader.candidate_c_legacy_signature,
        NasdHeader.parse_candidate_c,
    ),
}
