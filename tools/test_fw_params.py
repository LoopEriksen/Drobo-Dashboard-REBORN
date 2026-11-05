#!/usr/bin/env python3
"""
Offline tests for fw_params.py, and for fwtool.py's rootfs sub-command.

fwtool.py is the unpacker that feeds fw_params.py, so its one
sub-command that writes a tree is checked here too.

The firmware itself is gitignored (vendor/**), so this suite cannot
assume the real nasd binary is present. It tests the logic against a
synthetic blob built to mimic nasd's actual .rodata layout, and -- when
the real binary IS available locally -- additionally runs the tool's own
--validate check against it.

    py test_fw_params.py
"""
import argparse
import atexit
import contextlib
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fw_params  # noqa: E402
import fwtool  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + (f"  {failure_detail}"
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


def build_rodata(*parts: str) -> bytes:
    """NUL-separated strings, the way a compiler lays .rodata out."""
    return b"\x00".join(p.encode() for p in parts) + b"\x00"


print("\n== strings are found with their offsets ==")
rodata_bytes = build_rodata("hello", "ESADevice::GetPerformance", "Iops",
                            "TierIOps")
offset_by_string = dict((text, offset) for offset, text
                        in fw_params.extract_strings(rodata_bytes))
check("every string is extracted",
      {"hello", "Iops", "TierIOps"} <= set(offset_by_string),
      sorted(offset_by_string))
check("offsets increase with position",
      offset_by_string["Iops"] > offset_by_string["ESADevice::GetPerformance"])
check("runs shorter than 3 chars are skipped",
      all(len(text) >= 3
          for _, text in fw_params.extract_strings(
              build_rodata("ab", "abcd"))))

print("\n== the two kinds of anchor, and why the difference matters ==")
# The bare command names live in ONE contiguous table -- that is how
# this project recovered all 103 commands. A name's neighbours there are
# just other names, never parameters. Anchoring on them was this tool's
# first mistake.
name_table = build_rodata("eCmdSetConfig", "eCmdGetConfig", "eCmdSetDimming",
                          "eCmdGetDimming", "eCmdUploadDiags")
strings = fw_params.extract_strings(name_table)
neighbours = fw_params.candidates_near(
    strings,
    dict((text, offset) for offset, text in strings)["eCmdSetDimming"])
check("a command name's neighbours yield no parameters", neighbours == [],
      neighbours)

# The useful anchor is the HANDLER's own debug string.
handler = build_rodata("ESADevice::GetPerformance - failed", "Iops",
                       "ReadThroughout",
                       "WriteThroughout", "TierIOps")
markers = fw_params.find_markers(
    fw_params.extract_strings(handler), "eCmdGetPerformance")
check("a handler string is matched via the stripped command name",
      len(markers) == 1, markers)
check("-- and it is the handler, not a bare name",
      markers and "::" in markers[0][1], markers)

# "::" is required so an ordinary log line
# mentioning the verb doesn't anchor.
prose = build_rodata("Now going to GetPerformance for the user", "Iops")
check("a plain log line is not mistaken for a handler",
      fw_params.find_markers(
          fw_params.extract_strings(prose), "eCmdGetPerformance") == [],
      fw_params.find_markers(
          fw_params.extract_strings(prose), "eCmdGetPerformance"))

print("\n== candidate filtering ==")
noisy = build_rodata("ESADevice::GetPerformance", "Iops", "ERROR", "Failed",
                     "Params", "Result", "eCmdGetConfig", "lower",
                     "With Space", "TierIOps")
strings = fw_params.extract_strings(noisy)
anchor = dict((text, offset)
              for offset, text in strings)["ESADevice::GetPerformance"]
candidates = fw_params.candidates_near(strings, anchor)
check("real tag names survive", {"Iops", "TierIOps"} <= set(candidates),
      candidates)
check("log-level noise is dropped",
      "ERROR" not in candidates and "Failed" not in candidates, candidates)
# Params/Result are envelope structure, not parameters -- listing them
# would send somebody chasing a tag that is already known and already
# sent.
check("envelope tags are dropped",
      "Params" not in candidates and "Result" not in candidates, candidates)
check("command names are dropped", "eCmdGetConfig" not in candidates,
      candidates)
check("lowercase words are dropped", "lower" not in candidates, candidates)
check("strings with spaces are dropped", "With Space" not in candidates,
      candidates)

print("\n== the window bounds the search ==")
far = build_rodata("ESADevice::GetPerformance",
                   *["Padding" + str(i) for i in range(120)], "FarAwayTag")
strings = fw_params.extract_strings(far)
anchor = dict((text, offset)
              for offset, text in strings)["ESADevice::GetPerformance"]
check("a tag beyond the window is not attributed",
      "FarAwayTag" not in fw_params.candidates_near(
          strings, anchor, window=50))
check("-- but is found with a wide enough window",
      "FarAwayTag" in fw_params.candidates_near(strings, anchor, window=5000))

print("\n== analyse_command() ranks by how often a tag appears"
      " near the command ==")
multi = build_rodata("ESADevice::GetPerformance", "Iops", "Once",
                     "ESASession::GetPerformance", "Iops")
result = fw_params.analyse_command(multi, "eCmdGetPerformance", window=200)
check("a tag near several markers outranks one seen once",
      result["candidates"] and result["candidates"][0] == "Iops",
      result["candidates"])
check("the command id is carried", result["id"] == 62, result["id"])

print("\n== fwtool rootfs keeps its output inside -o, even when empty ==")
# fwtool.py is the firmware unpacker that feeds this tool (it writes
# vendor/extracted/rootfs/), so its one sub-command that writes a tree
# is checked here. 64 zero bytes are not JFFS2: nothing is extracted.
fwtool_scratch = tempfile.mkdtemp(prefix="fwtool-rootfs-")
atexit.register(shutil.rmtree, fwtool_scratch, ignore_errors=True)
empty_image_path = os.path.join(fwtool_scratch, "empty.bin")
with open(empty_image_path, "wb") as empty_image:
    empty_image.write(bytes(64))
rootfs_out = os.path.join(fwtool_scratch, "extracted", "rootfs")
try:
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        rootfs_outcome = fwtool.rebuild_rootfs(argparse.Namespace(
            file=empty_image_path, out=rootfs_out))
except OSError as exc:
    rootfs_outcome = exc
check("an image with nothing to extract is not an error",
      rootfs_outcome == 0, repr(rootfs_outcome))
check("the listing is written inside the -o folder",
      os.path.isfile(os.path.join(rootfs_out, "rootfs-listing.txt")),
      os.listdir(os.path.dirname(rootfs_out))
      if os.path.isdir(os.path.dirname(rootfs_out)) else "no folder")
check("-- and nothing is written beside or above it",
      sorted(os.listdir(fwtool_scratch)) == ["empty.bin", "extracted"]
      and os.listdir(os.path.join(fwtool_scratch, "extracted"))
      == ["rootfs"],
      sorted(os.listdir(fwtool_scratch)))

mistyped_out = os.path.join(fwtool_scratch, "mistyped", "rootfs")
try:
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        mistyped_outcome = fwtool.rebuild_rootfs(argparse.Namespace(
            file=os.path.join(fwtool_scratch, "no-such-image.bin"),
            out=mistyped_out))
except OSError as exc:
    mistyped_outcome = exc
check("a mistyped image path makes no output folder",
      isinstance(mistyped_outcome, FileNotFoundError)
      and not os.path.exists(os.path.join(fwtool_scratch, "mistyped")),
      repr(mistyped_outcome))

print("\n== validation against the real binary, when it is present ==")
# vendor/** is gitignored, so this is conditional by necessity. When the
# firmware IS on the machine, the tool must reproduce field names this
# project confirmed independently ON THE WIRE long before this tool
# existed. If it cannot, nothing else it prints is worth reading -- the
# same discipline that caught the pcapng dissector agreeing with its own
# fixtures while returning zero frames against every real capture.
real_nasd_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "vendor", "extracted", "rootfs", "sbin",
                              "nasd")
if os.path.exists(real_nasd_path):
    nasd_bytes = open(real_nasd_path, "rb").read()
    for anchor, expected, found_tags in fw_params.validate(nasd_bytes):
        missing = expected - found_tags
        check(f"{anchor} rediscovers what we measured on the wire",
              not missing, f"missing {sorted(missing)}")
else:
    print("  SKIP    firmware not present locally (vendor/** is gitignored) --"
          " run  py tools/fw_params.py <nasd> --validate  where it is")

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
