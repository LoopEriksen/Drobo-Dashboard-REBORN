"""
Tests for building a modified share configuration.

    py test_shareedit.py

These matter more than most. The only way to change a share on a Drobo
is eCmdSetConfig, which replaces the ENTIRE configuration document -- so
a bug that drops the network block does not produce a wrong share list,
it produces a Drobo that is no longer on the network, on hardware with
no vendor recovery tool left.

So the tests below spend most of their effort on what must NOT happen.

Entirely offline. This module cannot open a socket by construction.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import shareedit  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# Shaped exactly like the live device's reply, including the quirk that
# one user arrives as a dict and several as a list.
DEVICE_CONFIG = {
    "DRINASConfig": {
        "DRINasNetworkConfig": {
            "NasName": "TestDrobo",
            "IPConfig": {"IP": "10.0.0.5", "Gateway": "10.0.0.1"},
            "MACAddress": "02:00:00:00:00:01",
        },
        "DRIShareConfig": {
            "ConfigVersionBasis": "14",
            "Shares": {"Share": [
                {"ShareName": "Documents", "ShareState": "0",
                 "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                              "ShareUserAccess": "1"}}},
                {"ShareName": "Photo Library", "ShareState": "0",
                 "ShareUsers": {"ShareUser": [
                     {"ShareUsername": "Everyone", "ShareUserAccess": "1"},
                     {"ShareUsername": "guest", "ShareUserAccess": "0"}]}},
                {"ShareName": "Private", "ShareState": "0",
                 "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                              "ShareUserAccess": "0"}}},
            ]},
        },
    }
}

print("== reading the document ==")
check("every share is found",
      shareedit.share_names(DEVICE_CONFIG) == ["Documents", "Photo Library",
                                               "Private"],
      shareedit.share_names(DEVICE_CONFIG))
check(
    "a share name with a space survives",
    "Photo Library" in shareedit.share_names(DEVICE_CONFIG),
)

print("\n== the edit itself ==")
config_before_edit = copy.deepcopy(DEVICE_CONFIG)
private_granted_config = shareedit.set_user_access(DEVICE_CONFIG, "Private",
                                                   "Everyone", "1")
private_share = [s for s in (private_granted_config["DRINASConfig"]
                             ["DRIShareConfig"]["Shares"]["Share"])
                 if s["ShareName"] == "Private"][0]
check("the access code is changed",
      private_share["ShareUsers"]["ShareUser"]["ShareUserAccess"] == "1",
      private_share)
check("the input document is NOT mutated", DEVICE_CONFIG == config_before_edit)
check(
    "a single user stays a dict, as the device sends it",
    isinstance(private_share["ShareUsers"]["ShareUser"], dict),
    type(private_share["ShareUsers"]["ShareUser"]),
)

documents_with_new_user_config = shareedit.set_user_access(DEVICE_CONFIG,
                                                           "Documents",
                                                           "jordan", "1")
documents_share = [s for s in (documents_with_new_user_config["DRINASConfig"]
                               ["DRIShareConfig"]["Shares"]["Share"])
                   if s["ShareName"] == "Documents"][0]
check("a new user is added",
      isinstance(documents_share["ShareUsers"]["ShareUser"], list)
      and len(documents_share["ShareUsers"]["ShareUser"]) == 2,
      documents_share)
check("...and several users become a list, as the device sends them",
      isinstance(documents_share["ShareUsers"]["ShareUser"], list))

documents_revoked_config = shareedit.revoke_user(DEVICE_CONFIG, "Documents",
                                                 "Everyone")
revoked_documents_share = [s for s in (documents_revoked_config["DRINASConfig"]
                                       ["DRIShareConfig"]["Shares"]["Share"])
                           if s["ShareName"] == "Documents"][0]
check(
    "revoking sets code 0",
    revoked_documents_share["ShareUsers"]["ShareUser"]["ShareUserAccess"]
    == "0",
)
# Deliberate: the row stays. We cannot interpret access codes, so we
# could not faithfully restore a deleted one.
check("revoking does NOT delete the user row",
      revoked_documents_share["ShareUsers"]["ShareUser"]["ShareUsername"]
      == "Everyone", revoked_documents_share)

print("\n== what must never happen ==")
check("nothing outside DRIShareConfig moved",
      private_granted_config["DRINASConfig"]["DRINasNetworkConfig"]
      == DEVICE_CONFIG["DRINASConfig"]["DRINasNetworkConfig"])
check("no share disappeared",
      shareedit.share_names(private_granted_config)
      == shareedit.share_names(DEVICE_CONFIG))

# The central refusal. Code 2's meaning is still unconfirmed (only 0 and
# 1 are known), and it is not on this device, so it is not something to
# send.
try:
    shareedit.set_user_access(DEVICE_CONFIG, "Documents", "Everyone", "2")
    check("an unconfirmed, unseen access code is refused", False)
except shareedit.ShareEditError as exc:
    check("an unconfirmed, unseen access code is refused",
          "neither confirmed nor already on" in str(exc), str(exc))

try:
    shareedit.set_user_access(DEVICE_CONFIG, "Documents", "Everyone", "7")
    check("an invented access code is refused", False)
except shareedit.ShareEditError:
    check("an invented access code is refused", True)

check("code 0 is always allowed -- it is the one we are surest of",
      shareedit.set_user_access(DEVICE_CONFIG, "Documents", "Everyone", "0")
      is not None)

# The capability that actually unlocks a locked share: code 1
# (read/write) is CONFIRMED, so it may be set even on a device whose
# shares are ALL zero and thus have no non-zero code to copy -- the case
# this capability exists for.
ALL_ZERO_CONFIG = {
    "DRINASConfig": {
        "DRIShareConfig": {
            "ConfigVersionBasis": "9",
            "Shares": {
                "Share": {
                    "ShareName": "Locked",
                    "ShareUsers": {
                        "ShareUser": {
                            "ShareUsername": "Everyone",
                            "ShareUserAccess": "0",
                        }
                    },
                }
            },
        }
    }
}
unlocked_config = shareedit.set_user_access(ALL_ZERO_CONFIG, "Locked",
                                            "Everyone", "1")
share_user = (shareedit._share_block(unlocked_config)
              ["Shares"]["Share"]["ShareUsers"]["ShareUser"])
check("a confirmed code (1) can be set on an all-zero device",
      share_user["ShareUserAccess"] == "1", share_user)
check("...and setting it changed nothing outside DRIShareConfig",
      shareedit.share_names(unlocked_config)
      == shareedit.share_names(ALL_ZERO_CONFIG))

try:
    shareedit.set_user_access(DEVICE_CONFIG, "Nope", "Everyone", "0")
    check("editing a share that isn't there is refused", False)
except shareedit.ShareEditError as exc:
    check(
        "editing a share that isn't there is refused",
        "no share named" in str(exc),
    )

try:
    shareedit._share_block({"something": "else"})
    check("a document with no share block is refused", False)
except shareedit.ShareEditError:
    check("a document with no share block is refused", True)

# Prove the guards actually fire, rather than trusting that they would.
tampered_config = copy.deepcopy(DEVICE_CONFIG)
tampered_config["DRINASConfig"]["DRINasNetworkConfig"]["IPConfig"]["IP"] = (
    "10.0.0.99")
try:
    shareedit._assert_only_shares_changed(DEVICE_CONFIG, tampered_config)
    check("a change to the network block IS caught", False)
except shareedit.ShareEditError as exc:
    check(
        "a change to the network block IS caught",
        "outside DRIShareConfig" in str(exc),
    )

share_dropped_config = copy.deepcopy(DEVICE_CONFIG)
share_dropped_config["DRINASConfig"]["DRIShareConfig"]["Shares"]["Share"].pop()
try:
    shareedit._assert_no_share_lost(DEVICE_CONFIG, share_dropped_config)
    check("a vanished share IS caught", False)
except shareedit.ShareEditError as exc:
    check(
        "a vanished share IS caught",
        "would remove share" in str(exc),
        str(exc),
    )

print("\n== the confirmation a person reads before agreeing ==")
change_lines = shareedit.describe_change(
    DEVICE_CONFIG,
    shareedit.set_user_access(DEVICE_CONFIG, "Private", "Everyone", "1"),
)
check("the change is described in one line", len(change_lines) == 1,
      change_lines)
check("it names the user and the share",
      "Everyone" in change_lines[0] and "Private" in change_lines[0],
      change_lines)
check("it reports the raw code without claiming to know what it means",
      "code 1" in change_lines[0], change_lines)

revoke_change_lines = shareedit.describe_change(
    DEVICE_CONFIG,
    shareedit.revoke_user(DEVICE_CONFIG, "Documents", "Everyone"),
)
check("losing access is described as losing access",
      "loses access" in revoke_change_lines[0], revoke_change_lines)
check("an unchanged document describes nothing",
      shareedit.describe_change(DEVICE_CONFIG, copy.deepcopy(DEVICE_CONFIG))
      == [])

print("\n== this module cannot reach the network, by construction ==")
shareedit_source = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "drobo_nasd", "shareedit.py"),
    encoding="utf-8").read()
for forbidden in (
    "import socket",
    "config_cmd",
    "sendall",
    "create_connection",
):
    check(f"no {forbidden!r} anywhere in the module",
          forbidden not in shareedit_source)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
