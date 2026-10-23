"""
Offline test suite for the nasd protocol client library
(drobo_nasd/). No hardware needed, nothing touches a socket to a real
Drobo -- this only exercises commands.py, envelope.py, and framing.py.

    py sdk/tests/test_nasd.py

Run this after changing anything under drobo_nasd/.
"""
import os
import socket
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import client, commands, envelope, esatm, framing  # noqa: E402
from drobo_nasd.models import DriveSlot  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + label
        + (
            "  " + str(failure_detail)
            if failure_detail and not condition
            else ""
        )
    )
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------------
print("\n== commands.py: enum completeness ==")
# ---------------------------------------------------------------------------

check(
    "exactly 103 commands",
    len(commands.COMMANDS) == 103,
    len(commands.COMMANDS),
)
check(
    "ids are exactly 0..102, each used once",
    sorted(commands.COMMANDS.values()) == list(range(103)),
)

expected_command_ids = {
    "eCmdSetAdmin": 0,
    "eCmdGetAdmin": 1,
    "eCmdGetVersion": 7,
    "eCmdDeviceLogin": 10,
    "eCmdRunESACommand": 51,
    "eCmdSendTMCommand": 52,
    "eCmdGetSysInfo": 61,
    "eCmdReadHostBuffer": 70,
    "eCmdWriteHostBuffer": 71,
    "eCmdDoPoll": 75,
    "eCmdGetAllDevicesStatusXML": 85,
    "eCmdRunCommand": 89,
    "eCmdGetNASUsernamePassword": 102,
}
for name, expected in expected_command_ids.items():
    check(
        f"spot id {name} == {expected}",
        commands.COMMANDS.get(name) == expected,
        commands.COMMANDS.get(name),
    )

check(
    "ID_TO_NAME is the exact inverse",
    all(
        commands.ID_TO_NAME[command_id] == command_name
        for command_name, command_id in commands.COMMANDS.items()
    ),
)

print("\n== commands.py: DO-NOT-SEND group ==")
expected_escape_hatches = {
    "eCmdRunESACommand",
    "eCmdRunCommand",
    "eCmdSendTMCommand",
    "eCmdRouter",
    "eCmdReadHostBuffer",
    "eCmdWriteHostBuffer",
}
check("the six escape hatches are exactly as documented",
      commands.ESCAPE_HATCHES == frozenset(expected_escape_hatches))
check("DO_NOT_SEND is both groups combined",
      commands.DO_NOT_SEND == commands.ESCAPE_HATCHES | commands.DESTRUCTIVE)
check("the two groups don't overlap",
      commands.ESCAPE_HATCHES.isdisjoint(commands.DESTRUCTIVE))

# The whole point of the destructive group. Before 2026-07-26 the guard
# held only the escape hatches, so none of these were refused -- on a
# project whose first rule is that it never writes to the pack and never
# flashes firmware.
print("\n== SAFE_WRITES: the one narrow exception to read-only ==")
# Opened 2026-07-27. This is the boundary that keeps "we don't write to
# the hardware" true, so it is tested harder than anything else in this
# file.
check(
    "eCmdIdentify is an approved write", commands.is_safe_write("eCmdIdentify")
)
check(
    "...and is therefore NOT refused",
    not commands.is_dangerous("eCmdIdentify"),
)
check(
    "the approved-write set is exactly one command so far",
    commands.SAFE_WRITES == frozenset({"eCmdIdentify"}),
    sorted(commands.SAFE_WRITES),
)
check("refused and approved sets cannot overlap",
      commands.DO_NOT_SEND.isdisjoint(commands.SAFE_WRITES),
      sorted(commands.DO_NOT_SEND & commands.SAFE_WRITES))
check("every approved write is a real command",
      all(n in commands.COMMANDS for n in commands.SAFE_WRITES))

# The things that must NOT have crept in on Identify's coat-tails. Each
# of these fails at least one of the four tests in commands.SAFE_WRITES,
# and a future change that quietly adds one should fail here loudly.
for name in (
    "eCmdSetDimming",
    "eCmdRename",
    "eCmdSetTime",
    "eCmdToggleNightMode",
    "eCmdStandby",
    "eCmdRestart",
    "eCmdShutdown",
    "eCmdFormatDevice",
    "eCmdInstallFirmware",
    "eCmdDroboAppsAction",
    "eCmdUpdateUserGroupList",
):
    check(f"{name} is NOT an approved write", not commands.is_safe_write(name))
    check(f"{name} is still refused", commands.is_dangerous(name))

print("\n== the commands that must never reach the hardware ==")
for name in (
    "eCmdFormatDevice",
    "eCmdFormatLUN",
    "eCmdInstallFirmware",
    "eCmdRevertFirmware",
    "eCmdReset",
    "eCmdRepair",
    "eCmdForceRepair",
    "eCmdShutdown",
    "eCmdRestart",
    "eCmdSetConfig",
    "eCmdSetAdmin",
):
    check(f"{name} is refused", commands.is_dangerous(name))

# Two traps where the name lies about what the command does.
check("eCmdForceSingleInitiator is refused (reads like a query, is a write)",
      commands.is_dangerous("eCmdForceSingleInitiator"))
check("eCmdGetNextUniqueLUNID is refused (starts with Get, but ALLOCATES)",
      commands.is_dangerous("eCmdGetNextUniqueLUNID"))

# ...without the guard becoming so broad it blocks the reads we depend
# on.
print("\n== ordinary reads are still allowed ==")
for name in ("eCmdGetConfig", "eCmdGetSysInfo", "eCmdGetSharesConfig",
             "eCmdGetFirmwareInfo", "eCmdGetVersion", "eCmdGetAdminConfig"):
    check(f"{name} is NOT refused", not commands.is_dangerous(name))

check("every destructive name is a real command (no typos)",
      all(n in commands.COMMANDS or n in commands.NAMES_WITHOUT_CONFIRMED_ID
          for n in commands.DESTRUCTIVE),
      sorted(n for n in commands.DESTRUCTIVE
             if n not in commands.COMMANDS
             and n not in commands.NAMES_WITHOUT_CONFIRMED_ID))
check(
    "eCmdRunESACommand is dangerous",
    commands.is_dangerous("eCmdRunESACommand"),
)
check("eCmdRunESACommand is dangerous by id (51)", commands.is_dangerous(51))
check("eCmdFormatDevice is dangerous by id (33)", commands.is_dangerous(33))
check(
    "eCmdGetVersion is NOT dangerous",
    not commands.is_dangerous("eCmdGetVersion"),
)
check(
    "eCmdRouter has no numeric id (absent from the recovered 103-entry table)",
    "eCmdRouter" not in commands.COMMANDS
    and "eCmdRouter" in commands.NAMES_WITHOUT_CONFIRMED_ID,
)
check(
    "eCmdRouter is still flagged dangerous by name",
    commands.is_dangerous("eCmdRouter"),
)


# ---------------------------------------------------------------------------
print("\n== envelope.py: build/parse round trip ==")
# ---------------------------------------------------------------------------

# a spread of commands: no params, flat params, nested params
envelope_cases = [
    ("eCmdGetVersion", None),
    ("eCmdGetAllDevicesStatusXML", {"Verbose": 1}),
    # Note: "Name" is deliberately non-numeric text -- envelope.py's
    # wire format has no type tags, so a digit-only *string* like "1" is
    # indistinguishable from the int 1 after a round trip (both come
    # back as int). Use non-numeric strings and ints here to keep the
    # round-trip comparison meaningful instead of exercising that
    # documented ambiguity.
    (
        "eCmdGetSysInfo",
        {"Name": "unit-test", "Count": 5, "Nested": {"A": "alpha", "B": 2}},
    ),
]
for name, params in envelope_cases:
    xml_text = envelope.build_command("ESA-1234", name, params)
    check(
        f"{name}: build() produced the exact XML declaration",
        xml_text.startswith('<?xml version="1.0"?>\n'),
    )
    check(
        f"{name}: build() produced a DRINETTM root", "<DRINETTM>" in xml_text
    )

    parsed_envelope = envelope.parse(xml_text)
    check(f"{name}: round-trip esa_id", parsed_envelope.esa_id == "ESA-1234",
          parsed_envelope.esa_id)
    check(
        f"{name}: round-trip command_name",
        parsed_envelope.command_name == name,
        parsed_envelope.command_name,
    )
    check(
        f"{name}: round-trip command_id",
        parsed_envelope.command_id == commands.COMMANDS[name],
        parsed_envelope.command_id,
    )
    check(
        f"{name}: round-trip params",
        parsed_envelope.params == (params or {}),
        parsed_envelope.params,
    )

# building by numeric id instead of name should resolve the same way
xml_by_id = envelope.build_command(
    "ESA-1", commands.COMMANDS["eCmdGetVersion"], None
)
envelope_by_id = envelope.parse(xml_by_id)
check(
    "build by numeric id resolves command_name",
    envelope_by_id.command_name == "eCmdGetVersion",
)

# unknown command name must be rejected, not silently accepted
try:
    envelope.build_command("ESA-1", "eCmdTotallyMadeUp", None)
    check("unknown command name raises KeyError", False)
except KeyError:
    check("unknown command name raises KeyError", True)

print("\n== envelope.py: response build/parse (Error / Result) ==")
response_xml = envelope.build_response(
    "ESA-9", "eCmdGetVersion", error=0, result="4.2.1"
)
response_envelope = envelope.parse(response_xml)
check(
    "response error round-trips as int 0",
    response_envelope.error == 0,
    response_envelope.error,
)
check(
    "response result round-trips",
    response_envelope.result == "4.2.1",
    response_envelope.result,
)

error_response_xml = envelope.build_response(
    "ESA-9", "eCmdGetVersion", error=1, result_details={"Reason": "denied"}
)
error_response_envelope = envelope.parse(error_response_xml)
check(
    "response error round-trips as int 1",
    error_response_envelope.error == 1,
    error_response_envelope.error,
)
check(
    "response result_details round-trips",
    error_response_envelope.result_details == {"Reason": "denied"},
    error_response_envelope.result_details,
)

print("\n== envelope.py: malformed input is rejected ==")
try:
    envelope.parse(
        "<NotDRINETTM><ESAID>1</ESAID><Command>1</Command></NotDRINETTM>"
    )
    check("wrong root tag raises EnvelopeError", False)
except envelope.EnvelopeError:
    check("wrong root tag raises EnvelopeError", True)

try:
    envelope.parse("<DRINETTM><ESAID>1</ESAID></DRINETTM>")
    check("missing <Command> raises EnvelopeError", False)
except envelope.EnvelopeError:
    check("missing <Command> raises EnvelopeError", True)


# ---------------------------------------------------------------------------
print("\n== framing.py: length-prefixed frame round trip ==")
# ---------------------------------------------------------------------------

for byteorder in ("big", "little"):
    payload = f"hello nasd ({byteorder})".encode("utf-8")
    frame = framing.encode_frame(payload, byteorder=byteorder)
    decoded, remainder = framing.decode_frame(frame, byteorder=byteorder)
    check(
        f"encode/decode round trip ({byteorder})", decoded == payload, decoded
    )
    check(f"no remainder after exact frame ({byteorder})", remainder == b"")

# empty payload is a legitimate frame
empty_frame = framing.encode_frame(b"")
decoded_empty, _ = framing.decode_frame(empty_frame)
check("empty payload round-trips", decoded_empty == b"")

# incomplete buffer must raise, not silently return garbage
try:
    framing.decode_frame(b"\x00\x00")
    check("short-of-length-prefix buffer raises FramingError", False)
except framing.FramingError:
    check("short-of-length-prefix buffer raises FramingError", True)

full_frame = framing.encode_frame(b"0123456789")
try:
    framing.decode_frame(full_frame[:-3])
    check("truncated payload raises FramingError", False)
except framing.FramingError:
    check("truncated payload raises FramingError", True)

# two frames back to back decode in sequence
first_frame = framing.encode_frame(b"first")
second_frame = framing.encode_frame(b"second")
first_payload, remainder_after_first = framing.decode_frame(first_frame
                                                            + second_frame)
second_payload, remainder_after_second = framing.decode_frame(
    remainder_after_first)
check("first of two concatenated frames", first_payload == b"first",
      first_payload)
check("second of two concatenated frames", second_payload == b"second",
      second_payload)
check("nothing left after both frames", remainder_after_second == b"")

print("\n== framing.py: FrameAssembler handles fragmented delivery ==")
assembler = framing.FrameAssembler()
whole_frame = framing.encode_frame(b"fragmented-payload")
# split the frame into three arbitrary chunks, as a real socket.recv()
# would
chunk_points = [3, 9]
chunks = [
    whole_frame[: chunk_points[0]],
    whole_frame[chunk_points[0]:chunk_points[1]],
    whole_frame[chunk_points[1]:],
]
collected = []
for chunk in chunks:
    collected.extend(assembler.feed(chunk))
check(
    "fragmented frame reassembled into exactly one payload",
    collected == [b"fragmented-payload"],
    collected,
)
check(
    "assembler has no leftover bytes buffered",
    assembler.pending_bytes() == 0,
    assembler.pending_bytes(),
)

three_frames = (
    framing.encode_frame(b"one")
    + framing.encode_frame(b"two")
    + framing.encode_frame(b"three")
)
multi_frame_assembler = framing.FrameAssembler()
multi_frame_payloads = multi_frame_assembler.feed(three_frames)
check(
    "assembler yields multiple frames fed at once",
    multi_frame_payloads == [b"one", b"two", b"three"],
    multi_frame_payloads,
)


print(
    "\n== framing.py: NasdHeader candidates are internally self-consistent =="
)
# This does NOT prove any candidate matches a real Drobo -- see
# NasdHeader's docstring. It only proves each candidate's own
# encode/decode pair agrees with itself, which is what client.py depends
# on structurally.
for candidate_name, (encoder,
                     decoder) in framing.NasdHeader.CANDIDATES.items():
    header_bytes = encoder(42)
    _signature, length = decoder(header_bytes)
    check(
        f"NasdHeader.{candidate_name}: encode/decode agree on length",
        length == 42,
        length,
    )
    check(
        f"NasdHeader.{candidate_name}: header is 12 bytes",
        len(header_bytes) == 12,
        len(header_bytes),
    )

check(
    "NasdHeader.SIGNATURE_V4 matches the firmware string 'DRINASD4'",
    framing.NasdHeader.SIGNATURE_V4 == b"DRINASD4",
)


# ---------------------------------------------------------------------------
print("\n== client.py: state machine guards (no socket touched) ==")
# ---------------------------------------------------------------------------

nasd_client = client.NasdClient("192.0.2.1",
                                esa_id="ESA-1")  # TEST-NET-1, never dialled
check("client starts in state NEW",
      nasd_client.state == client.ConnectionState.NEW, nasd_client.state)

try:
    nasd_client.login()
    check("login() before connect() raises NotConnectedError", False)
except client.NotConnectedError:
    check("login() before connect() raises NotConnectedError", True)

try:
    nasd_client.send_command("eCmdGetVersion")
    check("send_command() before login() raises NotConnectedError", False)
except client.NotConnectedError:
    check("send_command() before login() raises NotConnectedError", True)

try:
    client.NasdClient.discover()
    check("discover() is explicitly not implemented here", False)
except NotImplementedError:
    check("discover() is explicitly not implemented here", True)

# Fake being logged in with a real (but unconnected-to-anything-real)
# socket object, so this genuinely proves the DO_NOT_SEND guard fires
# before any send-side code runs -- not just before a None-socket check
# would have.

nasd_client.state = client.ConnectionState.LOGGED_IN
nasd_client._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    nasd_client.send_command("eCmdRunESACommand")
    check("send_command() refuses DO_NOT_SEND commands", False)
except client.DangerousCommandError:
    check("send_command() refuses DO_NOT_SEND commands", True)
finally:
    nasd_client._sock.close()


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# esatm.py -- parsing a REAL <ESATMUpdate> captured from a live Drobo
# 5N. Fixture: sdk/tests/fixtures/esatm-sample.xml, firmware
# 4.3.1-8.126.117497. These lock in behaviour confirmed against actual
# hardware, so a future refactor can't silently regress it.
# ---------------------------------------------------------------------------
print("\n== esatm.py: real Drobo 5N greeting ==")

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "esatm-sample.xml")

if not os.path.exists(FIXTURE_PATH):
    check("real-greeting fixture present", False, FIXTURE_PATH)
else:
    greeting_payload = open(FIXTURE_PATH, "rb").read()
    snapshot = esatm.parse_esatm(greeting_payload)

    check("payload is NUL-terminated (protocol detail)",
          greeting_payload.rstrip().endswith(b"\x00"))
    check("parses despite the trailing NUL", snapshot.reachable is True)
    check(
        "device name read",
        snapshot.device.name == "TestDrobo",
        snapshot.device.name,
    )
    check("firmware read", snapshot.device.firmware.startswith("4.3.1"),
          snapshot.device.firmware)

    # The device reports mSlotCountExp=6, but a 5N has five physical
    # bays.
    check("phantom 6th bay trimmed", len(snapshot.drives) == 5,
          len(snapshot.drives))
    check("bays numbered 1..5",
          [drive.bay for drive in snapshot.drives] == [1, 2, 3, 4, 5],
          [drive.bay for drive in snapshot.drives])
    check("all five bays populated",
          all(drive.present for drive in snapshot.drives),
          [(drive.bay, drive.present) for drive in snapshot.drives])
    check("every drive healthy",
          all(drive.state == "ok" for drive in snapshot.drives),
          [(drive.bay, drive.state) for drive in snapshot.drives])
    check("drive models parsed",
          all("WD" in drive.model for drive in snapshot.drives),
          [drive.model for drive in snapshot.drives])
    check(
        "serials parsed",
        all(drive.serial.startswith("WD-") for drive in snapshot.drives),
        [drive.serial for drive in snapshot.drives],
    )

    # mTemperature is 0 on this hardware. It must read as "unknown",
    # never as a literal 0 C, or the alert rules would treat a healthy
    # array as freezing.
    check("temperature reported as unknown, not 0 C",
          all(drive.temperature_c is None for drive in snapshot.drives),
          [drive.temperature_c for drive in snapshot.drives])

    check("capacity usable > used",
          snapshot.capacity.usable_bytes > snapshot.capacity.used_bytes)
    check(
        "raw capacity is the sum of the disks",
        snapshot.capacity.raw_bytes
        == sum(drive.capacity_bytes for drive in snapshot.drives),
        (
            snapshot.capacity.raw_bytes,
            sum(drive.capacity_bytes for drive in snapshot.drives),
        ),
    )
    check("array reads healthy overall", snapshot.overall_state == "ok",
          snapshot.overall_state)

# Safety: the trim must never hide a slot that actually holds a disk.
_six_populated_slots = [
    DriveSlot(bay=i + 1, present=True, state="ok", capacity_bytes=1)
    for i in range(6)
]
esatm._trim_phantom_slots(_six_populated_slots, "Drobo 5N")
check("trim never hides a populated slot", len(_six_populated_slots) == 6,
      len(_six_populated_slots))

_unknown_model_slots = [
    DriveSlot(bay=1, present=True, state="ok", capacity_bytes=1),
    DriveSlot(bay=2, present=False, state="empty")]
esatm._trim_phantom_slots(_unknown_model_slots, "Unknown Model")
check("trim is a no-op for unknown models", len(_unknown_model_slots) == 2,
      len(_unknown_model_slots))

_five_populated_plus_phantom_slots = [
    DriveSlot(bay=i + 1, present=True, state="ok", capacity_bytes=1)
    for i in range(5)
]
_five_populated_plus_phantom_slots.append(
    DriveSlot(bay=6, present=False, state="empty"))
esatm._trim_phantom_slots(_five_populated_plus_phantom_slots, "Drobo 5N")
check("trailing empty phantom slot is removed",
      len(_five_populated_plus_phantom_slots) == 5,
      len(_five_populated_plus_phantom_slots))

print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
if failures:
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
