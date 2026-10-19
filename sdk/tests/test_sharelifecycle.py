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
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import config_cmd as cc
from drobo_nasd import sharewrite as sw
from drobo_nasd.shareedit import ShareEditError

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def _pp_share(name):
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


def _vol_share(name, volume):
    """A share in the compact shape our own create writes -- the plausible
    read-back of one, used as derivation input for <VolumeName>."""
    return ("\n\t\t\t<Share><ShareName>" + name + "</ShareName>"
            "<VolumeName>" + volume + "</VolumeName>"
            "<TimeMachineEnabled>0</TimeMachineEnabled>"
            "<ShareMaxTMSizeGB></ShareMaxTMSizeGB><ShareUsers/></Share>")


def _pp_config(shares_pp, basis="21"):
    return ("<DRIShareConfig>"
            "\n\t\t<ConfigVersionBasis>" + basis + "</ConfigVersionBasis>"
            "\n\t\t<UserList/>"
            "\n\t\t<Shares>" + shares_pp + "\n\t\t</Shares>"
            "\n\t</DRIShareConfig>")


def _read_reply(shareconfig_xml):
    """A read reply in the shape the real 5N sends: DRIShareConfig nested under
    a <DRINASConfig> wrapper inside ResultDetails (measured 2026-08-10)."""
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig>{shareconfig_xml}"
            f"</DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _accept():
    return (f"<TMCmd><CmdID>31</CmdID><Result>{cc.RESULT_ACCEPTED}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


def _roundtrip(shareconfig_xml):
    """What the surgical pipeline sends for this document when nothing is
    edited: the read reserialized by ElementTree."""
    return ET.tostring(ET.fromstring(shareconfig_xml), encoding="unicode")


def _sent_inner(raw_tmcmd):
    """The inner DRIShareConfig document a captured write actually carried."""
    m = re.search(r"<Params>(.*)</Params>", raw_tmcmd, re.S)
    inner = html.unescape(m.group(1))
    assert inner.startswith("<DRINASConfig>") and inner.endswith("</DRINASConfig>")
    return inner[len("<DRINASConfig>"):-len("</DRINASConfig>")]


class FakeDrobo:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(4)
        threading.Thread(target=self._run, daemon=True).start()

    def _frame(self, conn):
        head = conn.recv(16)
        if len(head) < 16:
            return None
        ln = struct.unpack(">I", head[12:16])[0]
        buf = b""
        while len(buf) < ln:
            c = conn.recv(ln - len(buf))
            if not c:
                break
            buf += c
        return buf

    def _run(self):
        for reply in self.replies:
            try:
                conn, _ = self.srv.accept()
                conn.settimeout(5)
                self._frame(conn)  # login
                conn.sendall(cc._frame(cc.TYPE_LOGIN_OK, b""))
                payload = self._frame(conn)  # command
                self.sent.append((payload or b"").rstrip(b"\x00").decode("utf-8", "replace"))
                conn.sendall(cc._frame(cc.TYPE_RESULT, reply))
                conn.close()
            except OSError:
                break
        self.srv.close()


ESA = "drb000000SAMPLE"

# The capture's world: Alpha and Beta stored pretty-printed, no volume named
# anywhere, and the throwaway share's node in the exact captured compact shape.
CAPTURED_NODE = ("<Share><ShareName>ZZTESTZZ</ShareName>"
                 "<VolumeName>Volume1</VolumeName>"
                 "<TimeMachineEnabled>0</TimeMachineEnabled>"
                 "<ShareMaxTMSizeGB></ShareMaxTMSizeGB>"
                 "<ShareUsers/></Share>")
ORIG = _pp_config(_pp_share("Alpha") + _pp_share("Beta"))
WITH_ZZ = ORIG.replace("</Shares>", CAPTURED_NODE + "</Shares>")

# ---------------------------------------------------------------------------
print("== the new node is byte-exact against the capture ==")

check("substituting ZZTESTZZ/Volume1 reproduces the captured node exactly",
      sw._NEW_SHARE_NODE.format(name="ZZTESTZZ", volume="Volume1") == CAPTURED_NODE)
check("the node has no ShareState", "ShareState" not in CAPTURED_NODE)
check("ShareMaxTMSizeGB is an empty OPEN-CLOSE pair, not self-closing",
      "<ShareMaxTMSizeGB></ShareMaxTMSizeGB>" in CAPTURED_NODE)

print("\n== create: the node is appended and every other byte is untouched ==")

dev = FakeDrobo([_read_reply(ORIG), _accept(), _read_reply(WITH_ZZ)])
res = sw.create_share("127.0.0.1", ESA, "ZZTESTZZ", volume="Volume1",
                      port=dev.port, timeout=5)
check("the create is accepted and verified", res.get("verified") is True, res)
check("read, write, then verify -- three connections", len(dev.sent) == 3, len(dev.sent))

sent = _sent_inner(dev.sent[1])
expected = _roundtrip(ORIG).replace("</Shares>", CAPTURED_NODE + "</Shares>")
check("the sent document is the read reserialized plus ONLY the new node",
      sent == expected, sent)
check("the node sits immediately before </Shares>",
      CAPTURED_NODE + "</Shares>" in sent)
check("the node appears exactly once", sent.count(CAPTURED_NODE) == 1)
check("ConfigVersionBasis is echoed, not invented",
      "<ConfigVersionBasis>21</ConfigVersionBasis>" in sent)

# verify=False stops after the accepted write: two connections, no verdict.
dev = FakeDrobo([_read_reply(ORIG), _accept()])
res = sw.create_share("127.0.0.1", ESA, "ZZTESTZZ", volume="Volume1",
                      port=dev.port, timeout=5, verify=False)
check("verify=False sends but does not read back",
      len(dev.sent) == 2 and "verified" not in res, res)

print("\n== create: name validation (deliberately narrower than the device) ==")

for bad in ["", " leading", "trailing ", "-lead", "_lead", "a" * 28, "Bad<Name",
            "Bad&Name", "Tab\tName", "Café", "slash/name", "dot.name"]:
    try:
        sw.create_share("127.0.0.1", ESA, bad, volume="V", port=1, timeout=1)
        check(f"bad name {bad!r} is refused before any network", False)
    except ShareEditError as exc:
        check(f"bad name {bad!r} is refused before any network",
              "invalid share name" in str(exc), str(exc))
for good in ["A", "a" * 27, "My Share_2-b", "0numeric", "x-", "x _-9"]:
    check(f"good name {good!r} passes the pattern",
          sw._NAME_PATTERN.match(good) is not None)

# A duplicate -- even one differing only by case -- is refused after the read,
# before any write.
dev = FakeDrobo([_read_reply(ORIG), _accept()])
try:
    sw.create_share("127.0.0.1", ESA, "ALPHA", volume="Volume1", port=dev.port, timeout=5)
    check("a case-folding duplicate is refused", False)
except ShareEditError as exc:
    check("a case-folding duplicate is refused", "already exists" in str(exc), str(exc))
check("...and only the read happened, no write", len(dev.sent) == 1, dev.sent)

print("\n== create: volume derivation from the device's own data ==")

# Exactly one volume named anywhere in the read -> used without being passed.
ONE_VOL = _pp_config(_pp_share("Alpha") + _vol_share("Made", "VolX"))
after_one = ONE_VOL.replace("</Shares>",
                            sw._NEW_SHARE_NODE.format(name="Fresh", volume="VolX") + "</Shares>")
dev = FakeDrobo([_read_reply(ONE_VOL), _accept(), _read_reply(after_one)])
res = sw.create_share("127.0.0.1", ESA, "Fresh", port=dev.port, timeout=5)
check("a single on-device volume is derived and used",
      "<VolumeName>VolX</VolumeName><TimeMachineEnabled>0</TimeMachineEnabled>"
      in _sent_inner(dev.sent[1]).split("<ShareName>Fresh</ShareName>", 1)[-1],
      _sent_inner(dev.sent[1]))
check("...and the create verifies", res.get("verified") is True, res)

# No volume named anywhere (the captured unit's own shape) -> refuse to guess.
dev = FakeDrobo([_read_reply(ORIG), _accept()])
try:
    sw.create_share("127.0.0.1", ESA, "Fresh", port=dev.port, timeout=5)
    check("zero volume candidates without volume= is refused", False)
except ShareEditError as exc:
    check("zero volume candidates without volume= is refused",
          "names no volume" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

# Two different volumes named -> ambiguous, refuse; explicit volume= resolves it.
TWO_VOL = _pp_config(_vol_share("A", "Vol1") + _vol_share("B", "Vol2"))
dev = FakeDrobo([_read_reply(TWO_VOL), _accept()])
try:
    sw.create_share("127.0.0.1", ESA, "Fresh", port=dev.port, timeout=5)
    check("two volume candidates without volume= is refused", False)
except ShareEditError as exc:
    check("two volume candidates without volume= is refused",
          "ambiguous" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

after_two = TWO_VOL.replace("</Shares>",
                            sw._NEW_SHARE_NODE.format(name="Fresh", volume="Vol2") + "</Shares>")
dev = FakeDrobo([_read_reply(TWO_VOL), _accept(), _read_reply(after_two)])
res = sw.create_share("127.0.0.1", ESA, "Fresh", volume="Vol2", port=dev.port, timeout=5)
check("an explicit volume= resolves the ambiguity", res.get("verified") is True, res)

# A volume needing XML escaping was never observed on the wire -> refused early.
try:
    sw.create_share("127.0.0.1", ESA, "Fresh", volume="V<ol", port=1, timeout=1)
    check("an XML-special volume name is refused", False)
except ShareEditError as exc:
    check("an XML-special volume name is refused", "XML-special" in str(exc), str(exc))

print("\n== create: a Shares container shape no capture has shown is refused ==")

EMPTY = ("<DRIShareConfig>\n\t\t<ConfigVersionBasis>3</ConfigVersionBasis>"
         "\n\t\t<UserList/>\n\t\t<Shares/>\n\t</DRIShareConfig>")
dev = FakeDrobo([_read_reply(EMPTY), _accept()])
try:
    sw.create_share("127.0.0.1", ESA, "First", volume="Volume1", port=dev.port, timeout=5)
    check("an empty <Shares/> container is refused, not guessed at", False)
except ShareEditError as exc:
    check("an empty <Shares/> container is refused, not guessed at",
          "exactly one </Shares>" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

print("\n== delete: the node is removed, the confirm appended, nothing else moves ==")

dev = FakeDrobo([_read_reply(WITH_ZZ), _accept(), _read_reply(ORIG)])
res = sw.delete_share("127.0.0.1", ESA, "ZZTESTZZ", port=dev.port, timeout=5)
check("the delete is accepted and verified", res.get("verified") is True, res)

sent = _sent_inner(dev.sent[1])
expected = _roundtrip(ORIG).replace(
    "</DRIShareConfig>",
    "<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>")
check("the sent document is the read minus ONLY the target node, plus the confirm",
      sent == expected, sent)
check("the deleted share is absent from the wire", "ZZTESTZZ" not in sent)
check("the confirm sits after </Shares>, right before </DRIShareConfig>",
      sent.endswith("<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>"))
check("the confirm appears exactly once",
      sent.count("<ShareDeleteConfirm>") == 1)
check("both surviving shares are still on the wire",
      "<ShareName>Alpha</ShareName>" in sent and "<ShareName>Beta</ShareName>" in sent)

# Deleting a MIDDLE pretty-printed share leaves the others byte-identical too.
THREE = _pp_config(_pp_share("Alpha") + _pp_share("Beta") + _pp_share("Gamma"))
TWO_LEFT = _pp_config(_pp_share("Alpha") + _pp_share("Gamma"))
dev = FakeDrobo([_read_reply(THREE), _accept(), _read_reply(TWO_LEFT)])
res = sw.delete_share("127.0.0.1", ESA, "Beta", port=dev.port, timeout=5)
sent = _sent_inner(dev.sent[1])
expected = _roundtrip(TWO_LEFT).replace(
    "</DRIShareConfig>",
    "<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>")
check("removing a middle share leaves every other byte identical",
      sent == expected, sent)

print("\n== delete: resolution is exact-case first, then a UNIQUE fold ==")

# A fold that lands on exactly one share resolves.
dev = FakeDrobo([_read_reply(WITH_ZZ), _accept(), _read_reply(ORIG)])
res = sw.delete_share("127.0.0.1", ESA, "zztestzz", port=dev.port, timeout=5)
check("a unique case-insensitive fold resolves and verifies",
      res.get("verified") is True and "ZZTESTZZ" not in _sent_inner(dev.sent[1]), res)

# Two folds and no exact match -> refuse, send nothing.
CASE = _pp_config(_pp_share("Data") + _pp_share("data"))
dev = FakeDrobo([_read_reply(CASE), _accept()])
try:
    sw.delete_share("127.0.0.1", ESA, "DATA", port=dev.port, timeout=5)
    check("an ambiguous fold with no exact match is refused", False)
except ShareEditError as exc:
    check("an ambiguous fold with no exact match is refused",
          "Ambiguous" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

# An unknown share -> refuse after the read, before any write.
dev = FakeDrobo([_read_reply(ORIG), _accept()])
try:
    sw.delete_share("127.0.0.1", ESA, "Nope", port=dev.port, timeout=5)
    check("an unknown share is refused", False)
except ShareEditError as exc:
    check("an unknown share is refused", "no share named" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

try:
    sw.delete_share("127.0.0.1", ESA, "  ", port=1, timeout=1)
    check("an empty delete name is refused before connecting", False)
except ShareEditError as exc:
    check("an empty delete name is refused before connecting",
          "no share name" in str(exc), str(exc))

# A read that ALREADY carries a ShareDeleteConfirm is a shape no capture
# explains -> refuse rather than append a second.
ODD = WITH_ZZ.replace("</DRIShareConfig>",
                      "<ShareDeleteConfirm>Y</ShareDeleteConfirm></DRIShareConfig>")
dev = FakeDrobo([_read_reply(ODD), _accept()])
try:
    sw.delete_share("127.0.0.1", ESA, "ZZTESTZZ", port=dev.port, timeout=5)
    check("a pre-existing ShareDeleteConfirm in the read is refused", False)
except ShareEditError as exc:
    check("a pre-existing ShareDeleteConfirm in the read is refused",
          "already carries" in str(exc), str(exc))
check("...nothing written", len(dev.sent) == 1, dev.sent)

print("\n== accepted-but-unverified and accepted-but-unapplied are named apart ==")

# Write accepted, verify connection refused -> ShareWriteOutcomeUnknownError, both ways.
dev = FakeDrobo([_read_reply(ORIG), _accept()])
try:
    sw.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1", port=dev.port, timeout=3)
    check("create verify transport failure raises ShareWriteOutcomeUnknownError", False)
except sw.ShareWriteOutcomeUnknownError as exc:
    check("create verify transport failure raises ShareWriteOutcomeUnknownError",
          "ACCEPTED" in str(exc) and "do NOT simply retry" in str(exc), str(exc))
except ShareEditError as exc:
    check("create verify transport failure raises ShareWriteOutcomeUnknownError "
          "(got plain ShareEditError)", False, str(exc))

dev = FakeDrobo([_read_reply(WITH_ZZ), _accept()])
try:
    sw.delete_share("127.0.0.1", ESA, "ZZTESTZZ", port=dev.port, timeout=3)
    check("delete verify transport failure raises ShareWriteOutcomeUnknownError", False)
except sw.ShareWriteOutcomeUnknownError as exc:
    check("delete verify transport failure raises ShareWriteOutcomeUnknownError",
          "ACCEPTED" in str(exc) and "do NOT simply retry" in str(exc), str(exc))
except ShareEditError as exc:
    check("delete verify transport failure raises ShareWriteOutcomeUnknownError "
          "(got plain ShareEditError)", False, str(exc))

# Device accepts but the read-back does not show the change -> received, not applied.
dev = FakeDrobo([_read_reply(ORIG), _accept(), _read_reply(ORIG)])
try:
    sw.create_share("127.0.0.1", ESA, "Fresh", volume="Volume1", port=dev.port, timeout=5)
    check("a create the read-back does not show raises", False)
except ShareEditError as exc:
    check("a create the read-back does not show raises",
          "Received, not applied" in str(exc), str(exc))

dev = FakeDrobo([_read_reply(WITH_ZZ), _accept(), _read_reply(WITH_ZZ)])
try:
    sw.delete_share("127.0.0.1", ESA, "ZZTESTZZ", port=dev.port, timeout=5)
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
dev = FakeDrobo([_read_reply(ORIG), _accept()])
try:
    sw.create_share("127.0.0.1", ESA, "Amp", volume="Photos & Video",
                    port=dev.port, timeout=5, verify=False)
    check("a volume name with & is refused", False, "no exception")
except ShareEditError as exc:
    check("a volume name with & is refused", "XML-special" in str(exc), str(exc))
check("nothing was written for it",
      not any("<ShareName>Amp<" in s for s in dev.sent), len(dev.sent))

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
