"""
Reading the Drobo's status.

Confirmed against a live Drobo 5N (firmware 4.3.1) on 2026-07-24: the
moment a client connects to nasd on TCP 5000, the device pushes one
framed message, unprompted and with no login required, carrying its
complete status as an <ESATMUpdate> XML document. That greeting is
everything the dashboard needs, so the driver polls simply by
reconnecting and reading it.

Frame layout (confirmed on the wire):

    offset  bytes  meaning
    0       8      signature "DRINASD\\0"
    8       1      major version (observed 0x01)
    9       1      minor version / message type (observed 0x01)
    10      2      flags / reserved (observed 0x0000)
    12      4      big-endian uint32: payload length in bytes
    16      N      UTF-8 XML payload, NUL-terminated
                   (the NUL is inside N)

See docs/protocol-map.md for the field-by-field mapping.
"""

from __future__ import annotations

import re
import socket
import struct
import time
from xml.etree import ElementTree

from .models import (
    STATE_EMPTY,
    STATE_OK,
    STATE_WARNING,
    Capacity,
    DeviceInfo,
    DriveSlot,
    PackHealth,
    Snapshot,
    Volume,
)

SIGNATURE = b"DRINASD\x00"
HEADER_BYTES = 16
DEFAULT_PORT = 5000
# a greeting is ~7 KB; this is a sane ceiling
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

# The most one recv call asks for. Only a cap on each call: a frame
# longer than this is read in several calls, never cut short.
_RECEIVE_CHUNK_BYTES = 65536

# mDiskState -> our state. 16 = healthy, confirmed across five good
# drives. 0 with an empty slot means no disk. Any other non-zero value
# is unknown, so it maps to WARNING rather than being silently treated
# as OK.
_STATE_BY_DISK_STATE_CODE = {16: STATE_OK, 0: STATE_EMPTY}
_EMPTY_SLOT_STATUS = 128  # mStatus for a bay with no disk


class FrameError(Exception):
    """
    The bytes on the wire were not a well-formed DRINASD frame.

    Raised for a wrong signature, an implausible length, a reply that
    broke off part-way, or one that ran out of time. Callers treat it
    as "whatever answered is not (or not yet) a Drobo we can read":
    discovery moves on to the next candidate, and the poll path reports
    the device unreachable and goes looking again, the same answer it
    gives an OSError.
    """


def _receive_exactly(
    sock: socket.socket, byte_count: int, deadline: float | None = None
) -> bytes:
    """
    Read exactly byte_count bytes, or as many as arrive before the
    socket gives up.

    `deadline` is a time.monotonic() instant that bounds the WHOLE read,
    and it matters because sock.settimeout() does not. A socket timeout
    is per recv call, so a peer that sends one byte just inside every
    timeout window keeps this loop alive for as long as it likes -- and
    with MAX_PAYLOAD_BYTES at 8 MB it can declare a length that makes
    "as long as it likes" mean hours. Anything on the LAN can answer on
    port 5000 and claim to be a Drobo (see the trust note at the top of
    discovery.py), and the agent runs this loop while holding the
    process-wide discovery lock, so one such peer used to be enough to
    wedge the device picker until the agent was restarted.

    Left at None -- the default -- the read is unbounded exactly as it
    has always been, so every caller that passes no deadline is
    unaffected.
    """
    received = b""
    # The shrinking timeout below belongs to THIS read. Left in place it
    # would silently become the budget for whatever the caller does next
    # on the same socket. fetch_greeting closes the socket straight
    # after, so nothing here depends on the restore today -- but
    # config_cmd's copy of this read does (it reads the login reply and
    # then sends the command frame on the same socket), and the two
    # copies are deliberately kept the same read. Hand the socket back
    # as it was found.
    original_timeout = sock.gettimeout() if deadline is not None else None
    try:
        while len(received) < byte_count:
            if deadline is not None:
                _shrink_timeout_to_deadline(sock, deadline, len(received),
                                            byte_count)
            chunk = sock.recv(
                min(_RECEIVE_CHUNK_BYTES, byte_count - len(received)))
            if not chunk:
                break
            received += chunk
    finally:
        if deadline is not None:
            _restore_socket_timeout(sock, original_timeout)
    return received


def _shrink_timeout_to_deadline(sock: socket.socket, deadline: float,
                                received_bytes: int,
                                byte_count: int) -> None:
    """
    Give the next recv only what is left of the read's budget, or raise
    FrameError once the budget is spent.

    Shrinking the per-recv wait as the budget runs down is what makes
    `deadline` a bound at all: without it the very last recv could
    still overshoot by a full socket timeout. The message is the one
    config_cmd raises for the same stall, word for word.
    """
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        raise FrameError(
            f"timed out reading frame: got {received_bytes} "
            f"of {byte_count} bytes")
    sock.settimeout(remaining_seconds)


def _restore_socket_timeout(sock: socket.socket,
                            original_timeout: float | None) -> None:
    """
    Hand the socket back with the timeout it had before the read.

    An OSError here means the socket is already unusable -- the peer
    went away and it was closed underneath the read. That is the read's
    own failure to report; restoring a timeout on it must not replace
    that error with a confusing one from the cleanup.
    """
    try:
        sock.settimeout(original_timeout)
    except OSError:
        pass


def read_frame(sock: socket.socket, deadline: float | None = None) -> bytes:
    """
    Read one DRINASD frame and return its raw XML payload (NUL
    included).

    `deadline` (a time.monotonic() instant) bounds header and payload
    together; see _receive_exactly for why a socket timeout alone is not
    a bound. Keyword-optional so existing callers keep working
    unchanged.
    """
    header = _receive_exactly(sock, HEADER_BYTES, deadline)
    if len(header) < HEADER_BYTES:
        raise FrameError(
            f"short header: got {len(header)} of {HEADER_BYTES} bytes"
        )
    if header[:8] != SIGNATURE:
        raise FrameError(f"bad signature: {header[:8]!r}")
    payload_length = struct.unpack(">I", header[12:16])[0]
    if payload_length > MAX_PAYLOAD_BYTES:
        raise FrameError(f"implausible payload length {payload_length}")
    payload = _receive_exactly(sock, payload_length, deadline)
    if len(payload) < payload_length:
        raise FrameError(
            f"short payload: got {len(payload)} of {payload_length} bytes"
        )
    return payload


def fetch_greeting(
    host: str, port: int = DEFAULT_PORT, timeout: float = 8.0
) -> bytes:
    """
    Connect and read the unprompted status greeting. Read-only.

    No login is needed because nasd pushes the greeting by itself the
    moment a client connects on port 5000; nothing is sent, so nothing
    on the device can change.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        # The clock starts once the connection is up, so `timeout` is
        # the budget for the connect AND, separately, the budget for the
        # read. Anchoring it before create_connection would quietly
        # shorten the read on a slow-to-answer device, which is the
        # opposite of the point: this is here to stop a hostile peer
        # stalling, not to make a real Drobo on a busy network harder to
        # find. A ~7 KB greeting inside 3-8 seconds is not a demanding
        # ask of hardware that is actually there.
        return read_frame(sock, deadline=time.monotonic() + timeout)
    finally:
        sock.close()


def _child_int(element, tag: str, default: int = 0) -> int:
    """
    The integer in the child element `tag`, or `default` (0) when it is
    absent, empty or not a number.

    0 is the right default for the fields this is used on -- counts and
    sizes, where "not reported" and "none" come to the same thing.
    Opaque status codes, where 0 would be a claim, use
    _child_int_or_none instead.
    """
    child = element.find(tag)
    if child is None or not child.text:
        return default
    try:
        return int(child.text)
    except ValueError:
        return default


def _child_text(element, tag: str, default: str = "") -> str:
    """
    The text of the child element `tag`, or `default` ("") when it is
    absent or empty, so callers never have to tell a missing element
    from an empty one -- the greeting uses both for "not set".
    """
    child = element.find(tag)
    return child.text if child is not None and child.text else default


def _parse_volumes(root) -> list[Volume]:
    """
    The volumes, read out of the greeting's `mLUNUpdates`.

    Each volume lives in its own numbered container (`<n0>`, `<n1>`,
    ...), exactly like the drive bays do. Costs no extra command -- the
    device volunteers this alongside everything else.

    Deliberately forgiving: a container missing a field yields None for
    it rather than 0, because a volume that reports no partition count
    is not a volume with zero partitions. Same rule as the pack status
    codes.
    """
    lun_updates = root.find("mLUNUpdates")
    if lun_updates is None:
        return []

    volumes: list[Volume] = []
    for volume_container in lun_updates:
        # Only numbered containers; anything else is not a volume.
        if not re.fullmatch(r"n\d+", volume_container.tag or ""):
            continue
        volumes.append(_volume_from_container(volume_container))
    volumes.sort(key=lambda volume: volume.lun)
    return volumes


def _volume_from_container(volume_container) -> Volume:
    """
    One Volume from one numbered `mLUNUpdates` container.

    The partition fields and the share state stay None when absent (see
    _parse_volumes); the number and the sizes fall back to 0, since a
    Volume always carries those.
    """
    return Volume(
        lun=_child_int_or_none(volume_container, "mLUN") or 0,
        unique_id=_child_text(volume_container, "mUniqueLUNID"),
        # Drobo leaves the name empty on a single-volume pack; the front
        # end decides what to call it rather than us inventing one here.
        name=_child_text(volume_container, "mLUNName"),
        max_size_bytes=_child_int_or_none(
            volume_container, "mMaximumLUNSize") or 0,
        used_bytes=_child_int_or_none(
            volume_container, "mUsedCapacityOS") or 0,
        partition_count=_child_int_or_none(
            volume_container, "mPartitionCount"),
        partition_type=_child_int_or_none(
            volume_container, "mPartitionType"),
        partition_format=_child_int_or_none(
            volume_container, "mPartitionFormat"),
        share_state=_child_int_or_none(volume_container, "mShareState"),
    )


def _child_int_or_none(element, tag: str) -> int | None:
    """
    Like _child_int, but absent means None rather than 0.

    This matters for the opaque pack-status codes. Their healthy
    baseline includes non-zero values (dnas_status=6,
    device_status=98304), so a missing field defaulting to 0 would look
    like a *deviation* and fire a false alarm claiming the code
    "changed" -- when in truth we simply never received it. None is the
    honest answer, and monitor._evaluate_alerts skips it.
    """
    child = element.find(tag)
    if child is None or not child.text:
        return None
    try:
        return int(child.text)
    except ValueError:
        return None


def _threshold_fraction(element, tag: str) -> float | None:
    """
    mRedThreshold/mYellowThreshold are the Drobo's own capacity alert
    levels, reported as hundredths of a percent (9500 == 95.00%).
    Observed on one healthy 5N: red=9500 (95%), yellow=8500 (85%) -- the
    exact values this project already used as hardcoded config defaults,
    so preferring the device's own numbers when present (see
    monitor._evaluate_alerts) is a faithful improvement, not a guess.
    """
    child = element.find(tag)
    if child is None or not child.text:
        return None
    try:
        return int(child.text) / 10000.0
    except ValueError:
        return None


# How many bays each model physically has. A real Drobo 5N reports
# mSlotCountExp = 6 even though it has five bays -- the sixth is a
# firmware placeholder that is always empty. Confirmed against a live 5N
# on firmware 4.3.1-8.126.117497 (the private capture of the status
# greeting from 2026-07-24; the sanitised fixture
# sdk/tests/fixtures/esatm-sample.xml keeps the same slot n5: state=0,
# capacity=0, no make).
_BAY_COUNT_BY_MODEL = {
    "Drobo 5N": 5,
}


def _trim_phantom_slots(drives: list[DriveSlot], model: str) -> None:
    """
    Drop placeholder slots the firmware reports beyond the real
    bay count.

    Deliberately conservative: only *trailing* and only *empty* slots
    are removed. If the device ever reports a disk in a slot we didn't
    expect, it stays visible -- over-reporting a bay is a cosmetic bug,
    hiding a real drive would be a serious one.
    """
    bay_count = _BAY_COUNT_BY_MODEL.get(model)
    if not bay_count:
        return
    while len(drives) > bay_count and not drives[-1].present:
        drives.pop()


def _parse_greeting_xml(payload: bytes) -> ElementTree.Element:
    """
    Strip the frame's trailing NUL (the length includes it) and parse
    the XML.
    """
    return ElementTree.fromstring(payload.rstrip(b"\x00").rstrip())


def parse_identity(payload: bytes) -> dict:
    """
    Pull just the identity fields out of a greeting, without building a
    full Snapshot.

    mESAID is the permanent hardware serial -- the best thing to pin an
    "expected device" to, since it survives IP changes, renames, and
    even a repack (unlike DNASDiskPackId, which identifies the disk pack
    and changes if the drives are moved to different hardware).
    Discovery uses this to confirm "this is definitely (or definitely
    not) my Drobo" before trusting anything else the device says -- see
    nasd/discovery.py.
    """
    root = _parse_greeting_xml(payload)
    return {
        "esa_id": _child_text(root, "mESAID"),
        "disk_pack_id": _child_text(root, "DNASDiskPackId"),
        "name": _child_text(root, "mDroboName") or _child_text(root, "mName"),
        "model": _child_text(root, "mModel"),
        "firmware": _child_text(root, "mVersion"),
    }


def parse_esatm(payload: bytes) -> Snapshot:
    """
    Turn an <ESATMUpdate> payload into a Snapshot.

    The model is read from the device (mModel) rather than assumed, so
    the same parser serves any Drobo that sends this greeting. The
    phantom sixth slot a 5N reports is trimmed here, at the one place
    every front end reads from, so no bay list ever shows a bay the
    chassis does not have.
    """
    # The payload is NUL-terminated and the length includes the NUL, so
    # strip trailing NULs/whitespace before the XML parser sees it.
    root = _parse_greeting_xml(payload)

    device = _parse_device_info(root)
    drives, raw_capacity_bytes = _parse_drives(root)
    _trim_phantom_slots(drives, device.model)

    return Snapshot(
        reachable=True,
        source="drobo5n",
        device=device,
        capacity=_parse_capacity(root, raw_capacity_bytes),
        drives=drives,
        volumes=_parse_volumes(root),
        # How many volumes this pack could hold. 16 on a 5N.
        max_volumes=_child_int_or_none(root, "mMaxLUNs"),
        pack_health=_parse_pack_health(root),
    )


def _parse_device_info(root) -> DeviceInfo:
    """The chassis identity: name, model, hardware serial, firmware."""
    return DeviceInfo(
        name=_child_text(root, "mDroboName") or _child_text(root, "mName"),
        # The model comes from the device itself (mModel) rather than
        # being hardcoded here -- _BAY_COUNT_BY_MODEL above still keys
        # off the model string, so an unrecognized/missing mModel just
        # means _trim_phantom_slots is a no-op (conservative: never
        # hides a bay we don't have a count for).
        model=_child_text(root, "mModel"),
        # Top-level mESAID is the permanent hardware serial. NOTE:
        # per-slot mSerial (below) is each *disk's* serial -- a
        # different thing, don't confuse them.
        serial=_child_text(root, "mESAID"),
        firmware=_child_text(root, "mVersion"),
    )


def _parse_drives(root) -> tuple[list[DriveSlot], int]:
    """
    Every bay the greeting reports, sorted by bay number, and the raw
    capacity of the disks in them added up.

    The raw total is the sum of the present disks' mPhysicalCapacity,
    read in the same pass as the bays so the two cannot disagree; an
    empty bay adds nothing.
    """
    drives: list[DriveSlot] = []
    raw_capacity_bytes = 0
    for slot in root.iter():
        if slot.find("mSlotNumber") is None:
            continue
        drive = _parse_drive_slot(slot)
        if drive.present:
            raw_capacity_bytes += drive.capacity_bytes
        drives.append(drive)
    drives.sort(key=lambda drive: drive.bay)
    return drives, raw_capacity_bytes


def _parse_drive_slot(slot) -> DriveSlot:
    """
    One bay. Either an mStatus of 128 or no reported capacity marks it
    empty, so a disk is recorded only when the slot says it is
    populated AND reports a size.
    """
    # device is 0-indexed; UI is 1-indexed
    bay = _child_int(slot, "mSlotNumber") + 1
    slot_status = _child_int(slot, "mStatus")
    physical_capacity_bytes = _child_int(slot, "mPhysicalCapacity")
    if slot_status == _EMPTY_SLOT_STATUS or physical_capacity_bytes <= 0:
        return DriveSlot(bay=bay, present=False, state=STATE_EMPTY)
    disk_state_code = _child_int(slot, "mDiskState")
    state = _STATE_BY_DISK_STATE_CODE.get(disk_state_code, STATE_WARNING)
    drive_temperature_c = _child_int(slot, "mTemperature")
    return DriveSlot(
        bay=bay,
        present=True,
        state=state,
        model=(_child_text(slot, "mMake") or "disk").strip(),
        serial=_child_text(slot, "mSerial"),
        capacity_bytes=physical_capacity_bytes,
        # The 5N greeting reports 0 for these drives -- treat 0 as "not
        # reported" rather than a real and alarming 0 C.
        temperature_c=(float(drive_temperature_c)
                       if drive_temperature_c > 0 else None),
        note=("" if state == STATE_OK
              else f"disk state code {disk_state_code}"),
        # Surfaced; not previously parsed. mErrorCount is the one
        # unambiguous per-drive signal here -- above zero is bad.
        error_count=_child_int(slot, "mErrorCount"),
        ssd_life_remaining=_child_int(slot, "SSDLifeRemaining"),
        firmware_rev=_child_text(slot, "mDiskFwRev"),
        rotational_speed=_child_int(slot, "RotationalSpeed"),
        managed_capacity_bytes=_child_int(slot, "mManagedCapacity"),
    )


def _parse_capacity(root, raw_capacity_bytes: int) -> Capacity:
    """
    The pack's capacity figures. Raw is the sum of the disks (see
    _parse_drives); the rest are the device's own protected figures.
    """
    return Capacity(
        raw_bytes=raw_capacity_bytes,
        usable_bytes=_child_int(root, "mTotalCapacityProtected"),
        used_bytes=_child_int(root, "mUsedCapacityProtected"),
        free_bytes=_child_int(root, "mFreeCapacityProtected"),
    )


def _parse_pack_health(root) -> PackHealth:
    """
    Pack/device-level health values the greeting already sends. See
    PackHealth's docstring for the healthy-baseline values and the
    discipline of not inventing meanings for the opaque status codes.
    """
    return PackHealth(
        # Opaque codes: absent must be None, not 0 --
        # see _child_int_or_none.
        disk_pack_status=_child_int_or_none(root, "mDiskPackStatus"),
        dnas_status=_child_int_or_none(root, "DNASStatus"),
        device_status=_child_int_or_none(root, "mStatus"),
        status_ex=_child_int_or_none(root, "mStatusEx"),
        real_time_integrity_checking=_child_int_or_none(
            root, "mRealTimeIntegrityChecking"),
        # Counts are genuinely counts: absent sensibly means zero, and
        # the alert rules compare with > 0, so 0 is both safe and
        # truthful here.
        relayout_count=_child_int(root, "mRelayoutCount"),
        double_degraded_count=_child_int(root, "mDoubleDegradedCnt"),
        red_threshold_fraction=_threshold_fraction(root, "mRedThreshold"),
        yellow_threshold_fraction=_threshold_fraction(
            root, "mYellowThreshold"),
        total_capacity_unprotected_bytes=_child_int(
            root, "mTotalCapacityUnprotected"),
        use_unprotected_capacity=_child_int(
            root, "mUseUnprotectedCapacity") != 0,
    )


def read_snapshot(
    host: str, port: int = DEFAULT_PORT, timeout: float = 8.0
) -> Snapshot:
    """
    Connect to a real Drobo 5N and return a parsed Snapshot. Read-only.

    The greeting is the device's whole status, so a poll is just a
    fresh connection: there is no session to keep alive between polls
    and nothing to log in to.
    """
    return parse_esatm(fetch_greeting(host, port, timeout))
