"""
Offline tests for firmware.py -- what the running version means.

    py test_firmware.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import firmware  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label +
          (f"  {failure_detail}" if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


print("\n== the release, separated from the build ==")
# The device sends release-build. Two units on the SAME firmware carry
# different build tails, so comparing the whole string would make them look
# like different releases.
check("a real device string parses to its release",
      firmware.parse_version("4.3.1-8.126.117497") == (4, 3, 1))
check(
    "the build tail is ignored, not compared",
    firmware.parse_version("4.3.1-8.126.117497")
    == firmware.parse_version("4.3.1"),
)
check("a two-part version fills the missing component",
      firmware.parse_version("4.3") == (4, 3, 0))
check("a one-part version too", firmware.parse_version("4") == (4, 0, 0))
check("an empty string parses to nothing", firmware.parse_version("") is None)
check("a non-numeric string parses to nothing",
      firmware.parse_version("unknown") is None)
check("None is tolerated", firmware.parse_version(None) is None)

print("\n== the two builds Drobo withdrew ==")
for version in ("4.3.0", "4.3.1", "4.3.1-8.126.117497"):
    assessment = firmware.assess(version)
    check(f"{version} is flagged as withdrawn", assessment.risk ==
          firmware.RISK_WITHDRAWN, assessment.risk)
    check(f"{version} is worth showing", assessment.notable)

assessment = firmware.assess("4.3.1-8.126.117497")
check("the headline names the release, not the build",
      "4.3.1" in assessment.headline and "117497" not in assessment.headline,
      assessment.headline)
check("it says who withdrew it", "Drobo" in assessment.detail,
      assessment.detail)
check("it names the last-known-good version",
      firmware.LAST_KNOWN_GOOD in assessment.detail, assessment.detail)

# The line this module must not cross. Downgrading means writing an unsigned
# image to hardware with no recovery tool -- the one action that can destroy the
# device. Informing about the firmware is useful; nudging toward a flash is not,
# and DO_NOT_SEND refuses every flashing command anyway.
lowered_detail = assessment.detail.lower()
check("it never tells you to downgrade", "downgrade" not in lowered_detail,
      assessment.detail)
check(
    "it never tells you to update or reflash",
    not any(
        phrase in lowered_detail
        for phrase in ("you should update", "reflash", "install 4.2.2")
    ),
    assessment.detail,
)
check("it says outright that this software won't do it",
      "will\n" not in assessment.detail and "not do it" in lowered_detail,
      assessment.detail)
check("it admits the fault was never published",
      "never published" in lowered_detail, assessment.detail)
check(
    "it does not invent symptoms it cannot know",
    not any(
        phrase in lowered_detail
        for phrase in ("data loss", "corrupt", "crash", "fails to")
    ),
    assessment.detail,
)
check("it ends on advice true regardless of firmware",
      "backups" in lowered_detail, assessment.detail)

print("\n== everything else stays quiet ==")
# A dashboard that comments on healthy firmware teaches people to ignore it.
for version in ("4.2.2", "4.2.1", "4.1.0", "1.2.7", "5.0.0", "4.4.0"):
    assessment = firmware.assess(version)
    check(f"{version} says nothing", assessment.risk ==
          firmware.RISK_NONE and not assessment.notable, assessment.risk)
    check(f"{version} carries no text",
          assessment.headline == "" and assessment.detail == "")

print("\n== an unreadable version is unknown, not assumed safe ==")
for version in ("", "unknown", None):
    assessment = firmware.assess(version)
    check(f"{version!r} is UNKNOWN", assessment.risk == firmware.RISK_UNKNOWN,
          assessment.risk)
    # Unknown must not be NOTABLE either -- a device that hasn't answered yet
    # would otherwise raise a firmware warning on every fresh agent start.
    check(f"{version!r} is not shown to the user", not assessment.notable)

print("\n== near-misses are not swept in ==")
# 4.3.2 does not exist; if one ever turned up it would not be a build Drobo
# pulled, because Drobo was gone. Matching "4.3.x" as a family would have
# claimed otherwise.
check("4.3.2 is not treated as withdrawn",
      firmware.assess("4.3.2").risk == firmware.RISK_NONE)
check("4.30.1 is not confused with 4.3.1",
      firmware.assess("4.30.1").risk == firmware.RISK_NONE)
check("3.3.1 is not confused with 4.3.1",
      firmware.assess("3.3.1").risk == firmware.RISK_NONE)

print("\n== the dict a front-end receives ==")
assessment_dict = firmware.assess("4.3.1-8.126.117497").to_dict()
check("carries the full version as reported",
      assessment_dict["version"] == "4.3.1-8.126.117497")
check("carries the release separately", assessment_dict["release"] == "4.3.1",
      assessment_dict["release"])
check("carries notable, so a UI can test one field",
      assessment_dict["notable"] is True)
check("carries last_known_good for context",
      assessment_dict["last_known_good"] == "4.2.2")
healthy_assessment_dict = firmware.assess("4.2.2").to_dict()
check("a healthy version's dict is not notable",
      healthy_assessment_dict["notable"] is False)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
