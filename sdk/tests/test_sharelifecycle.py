"""
Tests for sharewrite.create_share and delete_share -- the 2026-08-25 capture
mirrored, byte for byte.

    py test_sharelifecycle.py

The capture shows the original Dashboard creating a share by resending the
whole DRIShareConfig with ONE compact single-line <Share> node appended before
</Shares>, and deleting one by resending the config with that node absent plus
<ShareDeleteConfirm>Y</ShareDeleteConfirm> appended after </Shares>. So the
tests assert byte-exactness both ways: the new node matches the captured shape
to the byte, and everything that was not the change is sent exactly as the
read-reserialize pipeline (hardware-verified 2026-08-12) would send it
untouched.

Runs against a fake device on loopback. No hardware.
"""
import html
import os
import re
import socket
import struct
import sys
import threading
from xml.etree import ElementTree

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import sharewrite  # noqa: E402
from drobo_nasd.shareedit import ShareEditError  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


def _pretty_printed_share(name):
    """One stored share, pretty-printed with tabs the way the capture shows the
    device's own document (no VolumeName -- stored shares carry none)."""
    return ("\n\t\t\t<Share>"
            "\n\t\t\t\t<ShareName>" + name + "</ShareName>"
            "\n\t\t\t\t<ShareState>0</ShareState>"
            "\n\t\t\t\t<TimeMachineEnabled>0</TimeMachineEnabled>"
            "\n\t\t\t\t<ShareMaxTMSizeGB>0.000000</ShareMaxTMSizeGB>"
            "\n\t\t\t\t<ShareUsers>"
            "\n\t\t\t\t\t<ShareUser>"
            "\n\t\t\t\t\t\t<ShareUsername>Everyone</ShareUsername>"
            "\n\t\t\t\t\t\t<ShareUserAccess>1</ShareUserAccess>"
            "\n\t\t\t\t\t</ShareUser>"
            "\n\t\t\t\t</ShareUsers>"
            "\n\t\t\t</Share>")


def _compact_share_with_volume(name, volume):
    """A share in the compact shape our own create writes -- the plausible
    read-back of one, used as derivation input for <VolumeName>."""
    return ("\n\t\t\t<Share><ShareName>" + name + "</ShareName>"
            "<VolumeName>" + volume + "</VolumeName>"
            "<TimeMachineEnabled>0</TimeMachineEnabled>"
            "<ShareMaxTMSizeGB></ShareMaxTMSizeGB><ShareUsers/></Share>")


def _pretty_printed_share_config(pretty_printed_shares_xml,
                                 version_basis="21"):
    return ("<DRIShareConfig>"
            "\n\t\t<ConfigVersionBasis>" + version_basis
            + "</ConfigVersionBasis>"
            "\n\t\t<UserList/>"
            "\n\t\t<Shares>" + pretty_printed_shares_xml + "\n\t\t</Shares>"
            "\n\t</DRIShareConfig>")


def _read_reply(share_config_xml):
    """A read reply in the shape the real 5N sends: DRIShareConfig nested under
    a <DRINASConfig> wrapper inside ResultDetails (measured 2026-08-10)."""
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig>{share_config_xml}"
            f"</DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _accepted_reply():
    return ("<TMCmd><CmdID>31</CmdID>"
            f"<Result>{config_cmd.RESULT_ACCEPTED}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


def _reserialized(share_config_xml):
    """What the surgical pipeline sends for this document when nothing is
    edited: the read reserialized by ElementTree."""
    return ElementTree.tostring(ElementTree.fromstring(share_config_xml),
                                encoding="unicode")


def _written_share_config(raw_command):
    """The inner DRIShareConfig document a captured write actually carried."""
    params_match = re.search(r"<Params>(.*)</Params>", raw_command, re.S)
    wrapped_share_config = html.unescape(params_match.group(1))
    assert wrapped_share_config.startswith("<DRINASConfig>") and wrapped_share_config.endswith("</DRINASConfig>")
    return wrapped_share_config[len("<DRINASConfig>"):-len("</DRINASConfig>")]


class FakeDrobo:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET,
                                      socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.port = self.server_socket.getsockname()[1]
        self.server_socket.listen(4)
        threading.Thread(target=self._serve_replies, daemon=True).start()

    def _receive_frame(self, connection):
        header = connection.recv(16)
        if len(header) < 16:
            return None
        payload_length = struct.unpack(">I", header[12:16])[0]
        payload = b""
        while len(payload) < payload_length:
            chunk = connection.recv(payload_length - len(payload))
            if not chunk:
                break
            payload += chunk
        return payload

    def _serve_replies(self):
        for reply in self.replies:
            try:
                connection, _ = self.server_socket.accept()
                connection.settimeout(5)
                self._receive_frame(connection)  # login
                connection.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
                payload = self._receive_frame(connection)  # command
                self.sent.append((payload or b"").rstrip(b"\x00").decode("utf-8", "replace"))
                connection.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_RESULT, reply))
                connection.close()
            except OSError:
                break
        self.server_socket.close()


SAMPLE_ESA_ID = "drb000000SAMPLE"

# The capture's world: Alpha and Beta stored pretty-printed, no volume named
# anywhere, and the throwaway share's node in the exact captured compact shape.
CAPTURED_NODE = ("<Share><ShareName>ZZTESTZZ</ShareName>"
                 "<VolumeName>Volume1</VolumeName>"
                 "<TimeMachineEnabled>0</TimeMachineEnabled>"
                 "<ShareMaxTMSizeGB></ShareMaxTMSizeGB>"
                 "<ShareUsers/></Share>")
ORIGINAL_SHARE_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Alpha") + _pretty_printed_share("Beta"))
CONFIG_WITH_TEST_SHARE = ORIGINAL_SHARE_CONFIG.replace("</Shares>",
                                                       CAPTURED_NODE
                                                       + "</Shares>")

# ---------------------------------------------------------------------------
print("== the new node is byte-exact against the capture ==")

check("substituting ZZTESTZZ/Volume1 reproduces the captured node exactly",
      sharewrite._NEW_SHARE_NODE.format(name="ZZTESTZZ", volume="Volume1") == CAPTURED_NODE)
check("the node has no ShareState", "ShareState" not in CAPTURED_NODE)
check("ShareMaxTMSizeGB is an empty OPEN-CLOSE pair, not self-closing",
      "<ShareMaxTMSizeGB></ShareMaxTMSizeGB>" in CAPTURED_NODE)

print("\n== create: the node is appended and every other byte is untouched ==")

fake_drobo = FakeDrobo(
    [_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply(),
     _read_reply(CONFIG_WITH_TEST_SHARE)])
result = sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "ZZTESTZZ",
                                 volume="Volume1",
                                 port=fake_drobo.port, timeout=5)
check("the create is accepted and verified",
      result.get("verified") is True, result)
check("read, write, then verify -- three connections", len(fake_drobo.sent) == 3, len(fake_drobo.sent))

written_document = _written_share_config(fake_drobo.sent[1])
expected_document = _reserialized(ORIGINAL_SHARE_CONFIG).replace("</Shares>",
                                                                 CAPTURED_NODE
                                                                 + "</Shares>")
check("the sent document is the read reserialized plus ONLY the new node",
      written_document == expected_document, written_document)
check("the node sits immediately before </Shares>",
      CAPTURED_NODE + "</Shares>" in written_document)
check("the node appears exactly once",
      written_document.count(CAPTURED_NODE) == 1)
check("ConfigVersionBasis is echoed, not invented",
      "<ConfigVersionBasis>21</ConfigVersionBasis>" in written_document)

# verify=False stops after the accepted write: two connections, no verdict.
fake_drobo = FakeDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply()])
result = sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "ZZTESTZZ",
                                 volume="Volume1",
                                 port=fake_drobo.port, timeout=5, verify=False)
check("verify=False sends but does not read back",
      len(fake_drobo.sent) == 2 and "verified" not in result, result)

print("\n== create: name validation (deliberately narrower than the device) ==")

for bad_name in ["", " leading", "trailing ", "-lead", "_lead", "a" * 28, "Bad<Name",
                 "Bad&Name", "Tab\tName", "Café", "slash/name", "dot.name"]:
    try:
        sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, bad_name,
                                volume="V", port=1, timeout=1)
        check(f"bad name {bad_name!r} is refused before any network", False)
    except ShareEditError as exc:
        check(f"bad name {bad_name!r} is refused before any network",
              "invalid share name" in str(exc), str(exc))
for good_name in ["A", "a" * 27, "My Share_2-b", "0numeric", "x-", "x _-9"]:
    check(f"good name {good_name!r} passes the pattern",
          sharewrite._SHARE_NAME_PATTERN.match(good_name) is not None)

# A duplicate -- even one differing only by case -- is refused after the read,
# before any write.
fake_drobo = FakeDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply()])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "ALPHA", volume="Volume1", port=fake_drobo.port, timeout=5)
    check("a case-folding duplicate is refused", False)
except ShareEditError as exc:
    check("a case-folding duplicate is refused", "already exists" in str(exc), str(exc))
check("...and only the read happened, no write", len(fake_drobo.sent) == 1,
      fake_drobo.sent)

print("\n== create: volume derivation from the device's own data ==")

# Exactly one volume named anywhere in the read -> used without being passed.
ONE_VOLUME_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Alpha")
    + _compact_share_with_volume("Made", "VolX"))
one_volume_after_create = ONE_VOLUME_CONFIG.replace("</Shares>",
                                                    sharewrite._NEW_SHARE_NODE.format(name="Fresh", volume="VolX") + "</Shares>")
fake_drobo = FakeDrobo(
    [_read_reply(ONE_VOLUME_CONFIG), _accepted_reply(),
     _read_reply(one_volume_after_create)])
result = sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                                 port=fake_drobo.port,
                                 timeout=5)
check("a single on-device volume is derived and used",
      "<VolumeName>VolX</VolumeName><TimeMachineEnabled>0</TimeMachineEnabled>"
      in _written_share_config(fake_drobo.sent[1]).split(
          "<ShareName>Fresh</ShareName>", 1)[-1],
      _written_share_config(fake_drobo.sent[1]))
check("...and the create verifies", result.get("verified") is True, result)

# No volume named anywhere (the captured unit's own shape) -> refuse to guess.
fake_drobo = FakeDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply()])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            port=fake_drobo.port,
                            timeout=5)
    check("zero volume candidates without volume= is refused", False)
except ShareEditError as exc:
    check("zero volume candidates without volume= is refused",
          "names no volume" in str(exc), str(exc))
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

# Two different volumes named -> ambiguous, refuse; explicit volume= resolves it.
TWO_VOLUME_CONFIG = _pretty_printed_share_config(
    _compact_share_with_volume("A", "Vol1")
    + _compact_share_with_volume("B", "Vol2"))
fake_drobo = FakeDrobo([_read_reply(TWO_VOLUME_CONFIG), _accepted_reply()])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh",
                            port=fake_drobo.port,
                            timeout=5)
    check("two volume candidates without volume= is refused", False)
except ShareEditError as exc:
    check("two volume candidates without volume= is refused",
          "ambiguous" in str(exc), str(exc))
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

two_volume_after_create = TWO_VOLUME_CONFIG.replace("</Shares>",
                                                    sharewrite._NEW_SHARE_NODE.format(name="Fresh", volume="Vol2") + "</Shares>")
fake_drobo = FakeDrobo(
    [_read_reply(TWO_VOLUME_CONFIG), _accepted_reply(),
     _read_reply(two_volume_after_create)])
result = sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh", volume="Vol2", port=fake_drobo.port, timeout=5)
check("an explicit volume= resolves the ambiguity", result.get("verified") is True, result)

# A volume needing XML escaping was never observed on the wire -> refused early.
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh", volume="V<ol", port=1, timeout=1)
    check("an XML-special volume name is refused", False)
except ShareEditError as exc:
    check("an XML-special volume name is refused", "XML-special" in str(exc), str(exc))

print("\n== create: a Shares container shape no capture has shown is refused ==")

EMPTY_SHARES_CONFIG = (
    "<DRIShareConfig>\n\t\t<ConfigVersionBasis>3</ConfigVersionBasis>"
    "\n\t\t<UserList/>\n\t\t<Shares/>\n\t</DRIShareConfig>")
fake_drobo = FakeDrobo([_read_reply(EMPTY_SHARES_CONFIG), _accepted_reply()])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "First", volume="Volume1", port=fake_drobo.port, timeout=5)
    check("an empty <Shares/> container is refused, not guessed at", False)
except ShareEditError as exc:
    check("an empty <Shares/> container is refused, not guessed at",
          "exactly one </Shares>" in str(exc), str(exc))
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

print("\n== delete: the node is removed, the confirm appended, nothing else moves ==")

fake_drobo = FakeDrobo(
    [_read_reply(CONFIG_WITH_TEST_SHARE), _accepted_reply(),
     _read_reply(ORIGINAL_SHARE_CONFIG)])
result = sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "ZZTESTZZ",
                                 port=fake_drobo.port,
                                 timeout=5)
check("the delete is accepted and verified",
      result.get("verified") is True, result)

written_document = _written_share_config(fake_drobo.sent[1])
expected_document = _reserialized(ORIGINAL_SHARE_CONFIG).replace(
    "</DRIShareConfig>",
    "<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>")
check("the sent document is the read minus ONLY the target node, plus the confirm",
      written_document == expected_document, written_document)
check("the deleted share is absent from the wire",
      "ZZTESTZZ" not in written_document)
check("the confirm sits after </Shares>, right before </DRIShareConfig>",
      written_document.endswith("<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>"))
check("the confirm appears exactly once",
      written_document.count("<ShareDeleteConfirm>") == 1)
check("both surviving shares are still on the wire",
      "<ShareName>Alpha</ShareName>" in written_document and "<ShareName>Beta</ShareName>" in written_document)

# Deleting a MIDDLE pretty-printed share leaves the others byte-identical too.
THREE_SHARE_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Alpha") + _pretty_printed_share("Beta")
    + _pretty_printed_share("Gamma"))
TWO_SHARES_LEFT_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Alpha") + _pretty_printed_share("Gamma"))
fake_drobo = FakeDrobo(
    [_read_reply(THREE_SHARE_CONFIG), _accepted_reply(),
     _read_reply(TWO_SHARES_LEFT_CONFIG)])
result = sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "Beta",
                                 port=fake_drobo.port,
                                 timeout=5)
written_document = _written_share_config(fake_drobo.sent[1])
expected_document = _reserialized(TWO_SHARES_LEFT_CONFIG).replace(
    "</DRIShareConfig>",
    "<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>")
check("removing a middle share leaves every other byte identical",
      written_document == expected_document, written_document)

print("\n== delete: resolution is exact-case first, then a UNIQUE fold ==")

# A fold that lands on exactly one share resolves.
fake_drobo = FakeDrobo(
    [_read_reply(CONFIG_WITH_TEST_SHARE), _accepted_reply(),
     _read_reply(ORIGINAL_SHARE_CONFIG)])
result = sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "zztestzz",
                                 port=fake_drobo.port,
                                 timeout=5)
check("a unique case-insensitive fold resolves and verifies",
      result.get("verified") is True and "ZZTESTZZ" not in _written_share_config(fake_drobo.sent[1]), result)

# Two folds and no exact match -> refuse, send nothing.
CASE_COLLIDING_CONFIG = _pretty_printed_share_config(
    _pretty_printed_share("Data") + _pretty_printed_share("data"))
fake_drobo = FakeDrobo([_read_reply(CASE_COLLIDING_CONFIG), _accepted_reply()])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "DATA",
                            port=fake_drobo.port, timeout=5)
    check("an ambiguous fold with no exact match is refused", False)
except ShareEditError as exc:
    check("an ambiguous fold with no exact match is refused",
          "Ambiguous" in str(exc), str(exc))
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

# An unknown share -> refuse after the read, before any write.
fake_drobo = FakeDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply()])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "Nope",
                            port=fake_drobo.port, timeout=5)
    check("an unknown share is refused", False)
except ShareEditError as exc:
    check("an unknown share is refused", "no share named" in str(exc), str(exc))
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "  ",
                            port=1, timeout=1)
    check("an empty delete name is refused before connecting", False)
except ShareEditError as exc:
    check("an empty delete name is refused before connecting",
          "no share name" in str(exc), str(exc))

# A read that ALREADY carries a ShareDeleteConfirm is a shape no capture
# explains -> refuse rather than append a second.
CONFIG_WITH_DELETE_CONFIRM = CONFIG_WITH_TEST_SHARE.replace(
    "</DRIShareConfig>",
    "<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>")
fake_drobo = FakeDrobo(
    [_read_reply(CONFIG_WITH_DELETE_CONFIRM), _accepted_reply()])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "ZZTESTZZ",
                            port=fake_drobo.port,
                            timeout=5)
    check("a pre-existing ShareDeleteConfirm in the read is refused", False)
except ShareEditError as exc:
    check("a pre-existing ShareDeleteConfirm in the read is refused",
          "already carries" in str(exc), str(exc))
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

print("\n== accepted-but-unverified and accepted-but-unapplied are named apart ==")

# Write accepted, verify connection refused -> ShareWriteOutcomeUnknownError, both ways.
fake_drobo = FakeDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply()])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh", volume="Volume1", port=fake_drobo.port, timeout=3)
    check("create verify transport failure raises ShareWriteOutcomeUnknownError", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    check("create verify transport failure raises ShareWriteOutcomeUnknownError",
          "ACCEPTED" in str(exc) and "do NOT simply retry" in str(exc), str(exc))
except ShareEditError as exc:
    check("create verify transport failure raises ShareWriteOutcomeUnknownError "
          "(got plain ShareEditError)", False, str(exc))

fake_drobo = FakeDrobo(
    [_read_reply(CONFIG_WITH_TEST_SHARE), _accepted_reply()])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "ZZTESTZZ",
                            port=fake_drobo.port,
                            timeout=3)
    check("delete verify transport failure raises ShareWriteOutcomeUnknownError", False)
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    check("delete verify transport failure raises ShareWriteOutcomeUnknownError",
          "ACCEPTED" in str(exc) and "do NOT simply retry" in str(exc), str(exc))
except ShareEditError as exc:
    check("delete verify transport failure raises ShareWriteOutcomeUnknownError "
          "(got plain ShareEditError)", False, str(exc))

# Device accepts but the read-back does not show the change -> received, not applied.
fake_drobo = FakeDrobo(
    [_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply(),
     _read_reply(ORIGINAL_SHARE_CONFIG)])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Fresh", volume="Volume1", port=fake_drobo.port, timeout=5)
    check("a create the read-back does not show raises", False)
except ShareEditError as exc:
    check("a create the read-back does not show raises",
          "Received, not applied" in str(exc), str(exc))

fake_drobo = FakeDrobo(
    [_read_reply(CONFIG_WITH_TEST_SHARE), _accepted_reply(),
     _read_reply(CONFIG_WITH_TEST_SHARE)])
try:
    sharewrite.delete_share("127.0.0.1", SAMPLE_ESA_ID, "ZZTESTZZ",
                            port=fake_drobo.port,
                            timeout=5)
    check("a delete the read-back still shows raises", False)
except ShareEditError as exc:
    check("a delete the read-back still shows raises",
          "Received, not applied" in str(exc), str(exc))

# A volume name carrying an XML metacharacter. The share NAME cannot (the
# pattern refuses it) but the VOLUME is the device's own string, read through
# ElementTree and therefore unescaped. The captured create node carries the
# volume as plain text and an escaped one has never been seen on the wire, so
# the module REFUSES rather than guessing what the device's parser accepts.
# Pinned here (2026-09-01 audit) because the obvious "fix" -- escape it --
# would be a guessed wire format, exactly what this project does not send.
print()
print("== a volume name with & is refused, never escaped and sent ==")
fake_drobo = FakeDrobo([_read_reply(ORIGINAL_SHARE_CONFIG), _accepted_reply()])
try:
    sharewrite.create_share("127.0.0.1", SAMPLE_ESA_ID, "Amp",
                            volume="Photos & Video",
                            port=fake_drobo.port, timeout=5, verify=False)
    check("a volume name with & is refused", False, "no exception")
except ShareEditError as exc:
    check("a volume name with & is refused", "XML-special" in str(exc), str(exc))
check("nothing was written for it",
      not any("<ShareName>Amp<" in s for s in fake_drobo.sent),
      len(fake_drobo.sent))

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
