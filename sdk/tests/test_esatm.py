"""
Offline tests for the real Drobo 5N status path (nasd/esatm.py).

Uses a sanitized capture of an actual <ESATMUpdate> greeting (fake serials, fake
name, synthetic drive models, firmware revisions and capacities) so the parser
is regression-tested without hardware and without any real device identity in
the repo. No network is touched.

    py test_esatm.py
"""
import dataclasses
import os
import re
import socket
import struct
import sys
import time
from xml.etree import ElementTree

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd.models import STATE_OK  # noqa: E402
from drobo_nasd import esatm  # noqa: E402

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "esatm-sample.xml")

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"  {failure_detail}" if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


GREETING_PAYLOAD = open(FIXTURE_PATH, "rb").read()

print("\n== parse a real (sanitized) ESATMUpdate greeting ==")
snapshot = esatm.parse_esatm(GREETING_PAYLOAD)
snapshot_dict = snapshot.to_dict()
check("reachable", snapshot_dict["reachable"])
check("overall state ok", snapshot_dict["overall_state"] == STATE_OK,
      snapshot_dict["overall_state"])
check("device name from mDroboName", snapshot_dict["device"]["name"] == "TestDrobo", snapshot_dict["device"]["name"])
check("firmware parsed", snapshot_dict["device"]["firmware"].startswith("4.3.1"), snapshot_dict["device"]["firmware"])
check("model is Drobo 5N", snapshot_dict["device"]["model"] == "Drobo 5N")

present_drives = [drive for drive in snapshot_dict["drives"]
                  if drive["present"]]
check("five drives present", len(present_drives) == 5, len(present_drives))
# A 5N reports mSlotCountExp = 6, but it only has five physical bays: the sixth
# is a firmware placeholder that is always empty. parse_esatm() trims it, so a
# fully-populated 5N shows five bays and no empty one. (This assertion used to
# expect the phantom bay back when we surfaced it.)
check("no phantom sixth bay", sum(1 for drive in snapshot_dict["drives"] if not drive["present"]) == 0,
      [drive["bay"] for drive in snapshot_dict["drives"]
       if not drive["present"]])
check("exactly five bays reported", len(snapshot_dict["drives"]) == 5,
      len(snapshot_dict["drives"]))
check("all present drives ok",
      all(drive["state"] == STATE_OK for drive in present_drives))
check("drive capacity ~4TB", all(3.9e12 < drive["capacity_bytes"] < 4.1e12 for drive in present_drives))
# Per-slot mSerial is the DISK's serial, which is a different field from the
# device's mESAID. Confusing the two was a real bug once, so assert they are
# distinct per drive and disk-shaped, not that they equal the device serial.
check("each disk has its own distinct serial",
      len({drive["serial"] for drive in present_drives})
      == len(present_drives),
      [drive["serial"] for drive in present_drives])
check("disk serials are disk-shaped, not the device serial",
      all(drive["serial"].startswith("WD-") for drive in present_drives),
      [drive["serial"] for drive in present_drives])
check("temperature reported as unknown (greeting sends 0)",
      all(drive["temperature_c"] is None for drive in present_drives))

print("\n== health signals the greeting already sends ==")
check("all healthy drives report zero errors",
      all(drive["error_count"] == 0 for drive in present_drives),
      [drive["error_count"] for drive in present_drives])
check("firmware rev parsed per drive",
      all(drive["firmware_rev"] for drive in present_drives),
      [drive["firmware_rev"] for drive in present_drives])
check("ssd_life_remaining parsed per drive",
      [drive["ssd_life_remaining"] for drive in present_drives] == [100, 100, 100, 100, 100],
      [drive["ssd_life_remaining"] for drive in present_drives])
check("rotational_speed parsed per drive (bay 1 spins down to 0, others 27)",
      [drive["rotational_speed"] for drive in present_drives]
      == [0, 27, 27, 27, 27],
      [drive["rotational_speed"] for drive in present_drives])
check("managed_capacity_bytes parsed per drive (0 on this healthy array)",
      all(drive["managed_capacity_bytes"] == 0 for drive in present_drives),
      [drive["managed_capacity_bytes"] for drive in present_drives])

pack_health = snapshot_dict["pack_health"]
check("disk_pack_status matches the fixture", pack_health["disk_pack_status"] == 0, pack_health["disk_pack_status"])
check("dnas_status matches the fixture", pack_health["dnas_status"] == 6, pack_health["dnas_status"])
check("device_status matches the fixture (mStatus)", pack_health["device_status"] == 98304, pack_health["device_status"])
check("status_ex matches the fixture", pack_health["status_ex"] == 0,
      pack_health["status_ex"])
check("relayout_count zero on a healthy array", pack_health["relayout_count"] == 0, pack_health["relayout_count"])
check("double_degraded_count zero on a healthy array", pack_health["double_degraded_count"] == 0, pack_health["double_degraded_count"])
check("real_time_integrity_checking matches the fixture",
      pack_health["real_time_integrity_checking"] == 0, pack_health["real_time_integrity_checking"])
check("red_threshold_fraction is 0.95 (device's own 95% level)",
      pack_health["red_threshold_fraction"] == 0.95,
      pack_health["red_threshold_fraction"])
check("yellow_threshold_fraction is 0.85 (device's own 85% level)",
      pack_health["yellow_threshold_fraction"] == 0.85,
      pack_health["yellow_threshold_fraction"])
check("total_capacity_unprotected_bytes matches the fixture (0)",
      pack_health["total_capacity_unprotected_bytes"] == 0, pack_health["total_capacity_unprotected_bytes"])
check("use_unprotected_capacity is false on the fixture",
      pack_health["use_unprotected_capacity"] is False,
      pack_health["use_unprotected_capacity"])

capacity = snapshot_dict["capacity"]
check("usable ~11.7 TB", 11.4e12 < capacity["usable_bytes"] < 12.0e12, capacity["usable_bytes"])
check("used < free (array nearly empty)",
      capacity["used_bytes"] < capacity["free_bytes"])
check("raw is sum of the pack (~20 TB)", 19.5e12 < capacity["raw_bytes"] < 20.5e12, capacity["raw_bytes"])
check("used_fraction between 0 and 1", 0 < capacity["used_fraction"] < 1, capacity["used_fraction"])

print("\n== absent pack-status fields must read as unknown, not zero ==")
# Regression: these codes were parsed with a default of 0. Because the healthy
# baseline includes non-zero values (dnas_status=6, device_status=98304), a
# device that omitted a field would have looked like it CHANGED, firing a false
# alarm. Absent must be None so the alert rule skips it.

_greeting_without_pack_status = GREETING_PAYLOAD.rstrip(b"\x00")
for _tag in (b"mDiskPackStatus", b"DNASStatus", b"mStatusEx",
             b"mRealTimeIntegrityChecking"):
    _greeting_without_pack_status = re.sub(b"<" + _tag + b">[^<]*</" + _tag + b">", b"", _greeting_without_pack_status)

_partial_snapshot = esatm.parse_esatm(_greeting_without_pack_status + b"\x00")
_partial_pack_health = _partial_snapshot.pack_health
check("absent disk_pack_status is None, not 0",
      _partial_pack_health.disk_pack_status is None,
      _partial_pack_health.disk_pack_status)
check("absent dnas_status is None, not 0", _partial_pack_health.dnas_status is None, _partial_pack_health.dnas_status)
check("absent status_ex is None, not 0",
      _partial_pack_health.status_ex is None, _partial_pack_health.status_ex)
check("absent real_time_integrity_checking is None",
      _partial_pack_health.real_time_integrity_checking is None, _partial_pack_health.real_time_integrity_checking)
check("a field that IS present still parses",
      _partial_pack_health.device_status == 98304,
      _partial_pack_health.device_status)

# The ALERT consequence of this -- that absent fields raise no false
# "changed from baseline" alarm -- is an agent concern rather than a protocol
# one, so it lives in agent/tests/test_agent.py under "absent pack-status fields
# raise no false alarm". This suite stops at "absent parses as None", which is
# the part the library is responsible for.
#
# Worth knowing: when this moved out during the SDK split, the comment claiming
# it lived there was written before it did, and the check was briefly lost
# altogether. Restored 2026-07-27.

print("\n== frame reading over a real socket ==")
frame = esatm.SIGNATURE + b"\x01\x01\x00\x00" + struct.pack(">I", len(GREETING_PAYLOAD)) + GREETING_PAYLOAD
# Each peer below sends from this thread, before the read, as
# test_config_cmd.py does. They used to send from threads whose lambdas
# looked `writer_socket` up when they RAN, so a sender still going when
# the next case rebound `writer_socket` closed that case's socket
# instead of its own. The biggest frame here is the greeting, about
# 7 KB: a socketpair holds that with nobody reading yet, so the send
# does not block. Not by a wide margin everywhere -- macOS gives an
# AF_UNIX pair only 8 KB by default -- so a much bigger fixture would
# need a reader first.
reader_socket, writer_socket = socket.socketpair()
writer_socket.sendall(frame)
writer_socket.close()
received_payload = esatm.read_frame(reader_socket)
reader_socket.close()
check("read_frame returns the exact payload",
      received_payload == GREETING_PAYLOAD, len(received_payload))

print("\n== a bad signature is rejected ==")
reader_socket, writer_socket = socket.socketpair()
writer_socket.sendall(b"NOTDROBO" + b"\x00" * 8)
writer_socket.close()
try:
    esatm.read_frame(reader_socket)
    check("bad signature raises", False)
except esatm.FrameError as exc:
    # The message too, not just the class: a peer that hangs up before
    # sending raises FrameError as well ("short header"), which is how a
    # socket closed too early could pass this with no signature read.
    check("bad signature raises FrameError", "bad signature" in str(exc),
          exc)
finally:
    reader_socket.close()

print("\n== a stalling peer cannot hold the read open for ever ==")
# The header declares a 4 MB payload that never arrives. Before the deadline
# there was only a PER-RECV timeout, so a peer that went quiet on a socket in
# blocking mode -- or dribbled one byte inside every window -- held the reader
# indefinitely, and the agent runs this while holding its process-wide
# discovery lock. Anything on the LAN can answer on port 5000 and claim to be
# a Drobo, so a peer that behaves badly on purpose is the realistic case.
reader_socket, writer_socket = socket.socketpair()
writer_socket.sendall(esatm.SIGNATURE + b"\x01\x01\x00\x00"
                      + struct.pack(">I", 4 * 1024 * 1024))
_started_at = time.monotonic()
try:
    esatm.read_frame(reader_socket, deadline=time.monotonic() + 0.3)
    check("a stalled read gives up", False)
except (esatm.FrameError, OSError):
    # FrameError once the budget is spent, or the socket timeout that the
    # shrinking deadline puts on the last recv -- which one you get depends on
    # the scheduler, so this asserts that it stopped, not how.
    check("a stalled read gives up", True)
finally:
    _elapsed = time.monotonic() - _started_at
    reader_socket.close()
    writer_socket.close()
check("...and within its budget rather than for ever", _elapsed < 5.0, _elapsed)

# The deadline works by shrinking the socket's own timeout as the budget runs
# down, and that value belongs to the one read. fetch_greeting closes the
# socket straight after, so nothing here would notice it being left behind --
# but config_cmd's copy of this read sends on the socket afterwards, and the
# two copies are deliberately the same read. Checked on both sides so they
# cannot drift apart again.
reader_socket, writer_socket = socket.socketpair()
reader_socket.settimeout(5.0)
writer_socket.sendall(esatm.SIGNATURE + b"\x01\x01\x00\x00" + struct.pack(">I", 4) + b"abcd")
esatm.read_frame(reader_socket, deadline=time.monotonic() + 5.0)
check("a bounded read leaves the socket's timeout as it found it",
      reader_socket.gettimeout() == 5.0, reader_socket.gettimeout())
reader_socket.close()
writer_socket.close()


print("\n== volumes, read from the greeting at no extra cost ==")
# The pack is carved into volumes (Drobo calls them LUNs); each becomes a drive
# letter in Windows. Every volume-MANAGEMENT command in the firmware table
# answers empty on this firmware -- but the device volunteers the STATE of its
# volumes in the ordinary status greeting, which is the half worth having and
# costs no extra connection to a device that tires easily.
check("the real greeting carries one volume", len(snapshot.volumes) == 1, len(snapshot.volumes))
check("the pack could hold sixteen", snapshot.max_volumes == 16,
      snapshot.max_volumes)
_volume = snapshot.volumes[0]
check("volume number read", _volume.lun == 0, _volume.lun)
check("unique id read", _volume.unique_id == "1000", _volume.unique_id)
check("max size read (64 TiB)", _volume.max_size_bytes == 70368744177664, _volume.max_size_bytes)
check("partition count read", _volume.partition_count == 2,
      _volume.partition_count)
check("partition type carried raw", _volume.partition_type == 3,
      _volume.partition_type)
# ...and now also decoded. Scheme 3 is GPT, on two independent grounds:
# drobo-utils (Peter Silva's reverse-engineering of Drobo's SCSI management
# protocol) says so, and MBR cannot address the 64 TiB ceiling this volume
# reports. See models.Volume for the full credit, including what got rejected.
check("partition scheme 3 is decoded as GPT", _volume.partition_scheme == "GPT", _volume.partition_scheme)
check("the decode reaches front-ends via to_dict",
      _volume.to_dict().get("partition_scheme") == "GPT", _volume.to_dict().get("partition_scheme"))
check("the raw number is still carried alongside the word",
      _volume.to_dict()["partition_type"] == 3,
      _volume.to_dict().get("partition_type"))

# The format number stays raw. drobo-utils' format table (NO FORMAT/NTFS/HFS/
# EXT3/FAT32) contains no 64, so that mapping does NOT transfer -- and a
# neighbouring table being right is not evidence for guessing this one.
check("partition format carried raw", _volume.partition_format == 64, _volume.partition_format)

# An unrecognised scheme yields "" rather than "Unknown", so a UI falls back to
# showing the raw number it already has instead of a word that means nothing.
check("an unknown scheme is empty, not 'Unknown'",
      dataclasses.replace(_volume, partition_type=99).partition_scheme == "")
check("an absent scheme is empty too",
      dataclasses.replace(_volume, partition_type=None).partition_scheme == "")
check("scheme 1 is MBR", dataclasses.replace(_volume, partition_type=1).partition_scheme == "MBR")
check("an unnamed volume stays unnamed rather than being invented",
      _volume.name == "", repr(_volume.name))

# Absent must not read as zero -- a volume reporting no partition count is not
# a volume with zero partitions.
_bare_volumes = esatm._parse_volumes(
    ElementTree.fromstring("<r><mLUNUpdates><n0><mLUN>0</mLUN></n0></mLUNUpdates></r>"))
check("a missing field is None, not 0", _bare_volumes[0].partition_count is None, _bare_volumes[0].partition_count)
check("a greeting with no mLUNUpdates gives no volumes",
      esatm._parse_volumes(ElementTree.fromstring("<r/>")) == [])

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
