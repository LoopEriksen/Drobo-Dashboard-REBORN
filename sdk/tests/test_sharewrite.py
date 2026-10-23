"""
Tests for sharewrite -- the send half of share-permission edits, and the
highest-stakes write in the SDK.

    py test_sharewrite.py

The whole point of the surgical design is that changing ONE user's
access on ONE share leaves every other byte the device's own: every
other share, every other user, ConfigVersionBasis, UserList. So the
tests assert exactly that -- not just that the target changed, but that
nothing else did -- because a share silently dropped from the write
would be a share gone from a device that may hold the only copy of
somebody's data.

Runs against a fake device on loopback. No hardware.
"""

import html
import os
import socket
import struct
import sys
import threading

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


def _raises(attempt):
    try:
        attempt()
        return False
    except Exception:
        return True


def _compact_share(name, access_code, user="Everyone"):
    return (
        f"<Share><ShareName>{name}</ShareName><ShareState>0</ShareState>"
        f"<TimeMachineEnabled>0</TimeMachineEnabled>"
        f"<ShareUsers><ShareUser><ShareUsername>{user}</ShareUsername>"
        f"<ShareUserAccess>{access_code}</ShareUserAccess>"
        f"</ShareUser></ShareUsers>"
        f"</Share>"
    )


def _compact_share_config(shares_xml, version_basis="15"):
    """A DRIShareConfig with ConfigVersionBasis and an empty UserList,
    the exact shape the capture confirmed."""
    return (f"<DRIShareConfig><ConfigVersionBasis>{version_basis}"
            "</ConfigVersionBasis>"
            f"<UserList /><Shares>{shares_xml}</Shares></DRIShareConfig>")


def _read_reply(share_config_xml):
    """A read reply in the shape the real 5N sends: DRIShareConfig
    nested under a <DRINASConfig> wrapper inside ResultDetails
    (measured 2026-08-10 -- the wrapper is what get_config_element must
    dig through)."""
    return (
        f"<TMCmd><CmdID>30</CmdID><ResultDetails>"
        f"<DRINASConfig>{share_config_xml}"
        f"</DRINASConfig></ResultDetails></TMCmd>"
    ).encode() + b"\x00"


def _accepted_reply():
    return ("<TMCmd><CmdID>31</CmdID>"
            f"<Result>{config_cmd.RESULT_ACCEPTED}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


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
                self.sent.append(
                    (payload or b"").rstrip(b"\x00").decode("utf-8", "replace")
                )
                connection.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_RESULT, reply))
                connection.close()
            except OSError:
                break
        self.server_socket.close()


SAMPLE_ESA_ID = "drb000000SAMPLE"
TWO_LOCKED_SHARES_CONFIG = _compact_share_config(_compact_share("Locked", "0")
                                                 + _compact_share(
                                                     "Other", "0"))

# ---------------------------------------------------------------------------
print("== unlock a share: set Everyone from 0 to 1, touch nothing else ==")

# read-element, write, verify read-back (Locked now 1)
after_unlock_config = _compact_share_config(_compact_share("Locked", "1")
                                            + _compact_share("Other", "0"))
fake_drobo = FakeDrobo(
    [_read_reply(TWO_LOCKED_SHARES_CONFIG), _accepted_reply(),
     _read_reply(after_unlock_config)])
result = sharewrite.set_share_user_access(
    "127.0.0.1",
    SAMPLE_ESA_ID,
    "Locked",
    "Everyone",
    "1",
    port=fake_drobo.port,
    timeout=5,
)
check("the write is accepted and verified",
      result.get("verified") is True, result)

written_document = html.unescape(fake_drobo.sent[1])
check(
    "read, write, then verify -- three connections",
    len(fake_drobo.sent) == 3,
    len(fake_drobo.sent),
)
check(
    "Locked's Everyone is now access 1",
    "<ShareName>Locked</ShareName>" in written_document
    and "<ShareUserAccess>1</ShareUserAccess>" in written_document,
    written_document,
)
check("ConfigVersionBasis is preserved exactly",
      "<ConfigVersionBasis>15</ConfigVersionBasis>" in written_document,
      written_document)
check("the empty UserList is preserved", "UserList" in written_document,
      written_document)
check("the OTHER share is still present and unchanged",
      "<ShareName>Other</ShareName>" in written_document, written_document)
check("Other's access is still 0 (not touched)",
      written_document.count("<ShareUserAccess>0</ShareUserAccess>") == 1,
      written_document)
check("exactly one access code became 1",
      written_document.count("<ShareUserAccess>1</ShareUserAccess>") == 1,
      written_document)

print("\n== the guards: a disallowed code never reaches the device ==")

# Code 2 is unconfirmed and not on the device -> refuse, send nothing.
fake_drobo = FakeDrobo(
    [_read_reply(TWO_LOCKED_SHARES_CONFIG), _accepted_reply(),
     _read_reply(TWO_LOCKED_SHARES_CONFIG)])
try:
    sharewrite.set_share_user_access(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "Locked",
        "Everyone",
        "2",
        port=fake_drobo.port,
        timeout=5,
    )
    check("an unconfirmed code is refused", False)
except ShareEditError as exc:
    check(
        "an unconfirmed code is refused",
        "neither confirmed" in str(exc),
        str(exc),
    )
check("...and only the read happened, no write", len(fake_drobo.sent) == 1,
      fake_drobo.sent)

# An unknown share -> refuse after the read, before any write.
fake_drobo = FakeDrobo(
    [_read_reply(TWO_LOCKED_SHARES_CONFIG), _accepted_reply(),
     _read_reply(TWO_LOCKED_SHARES_CONFIG)])
try:
    sharewrite.set_share_user_access(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "Nope",
        "Everyone",
        "1",
        port=fake_drobo.port,
        timeout=5,
    )
    check("an unknown share is refused", False)
except ShareEditError as exc:
    check(
        "an unknown share is refused", "no share named" in str(exc), str(exc)
    )
check("...and nothing was written", len(fake_drobo.sent) == 1, fake_drobo.sent)

# Bad inputs are refused before any network at all.
check(
    "an empty share name is refused before connecting",
    _raises(
        lambda: sharewrite.set_share_user_access(
            "127.0.0.1", SAMPLE_ESA_ID, "", "Everyone", "1", port=1, timeout=1
        )
    ),
)
check(
    "a non-numeric code is refused before connecting",
    _raises(
        lambda: sharewrite.set_share_user_access(
            "127.0.0.1",
            SAMPLE_ESA_ID,
            "Locked",
            "Everyone",
            "x",
            port=1,
            timeout=1,
        )
    ),
)

print("\n== a code already on the device is allowed even if unconfirmed ==")
# If some share already carries code 2, copying it to another is allowed
# -- it is a real code on this device, not an invented one.
CONFIG_WITH_ACCESS_CODE_TWO = _compact_share_config(
    _compact_share("A", "2") + _compact_share("B", "0"))
after_code_two_copied_config = _compact_share_config(
    _compact_share("A", "2") + _compact_share("B", "2"))
fake_drobo = FakeDrobo(
    [_read_reply(CONFIG_WITH_ACCESS_CODE_TWO), _accepted_reply(),
     _read_reply(after_code_two_copied_config)])
result = sharewrite.set_share_user_access(
    "127.0.0.1",
    SAMPLE_ESA_ID,
    "B",
    "Everyone",
    "2",
    port=fake_drobo.port,
    timeout=5,
)
check(
    "copying an existing on-device code is allowed",
    result.get("verified") is True,
    result,
)

print("\n== revoke sets 0, and reports a device that did not apply it ==")
after_revoke_config = _compact_share_config(_compact_share("Locked", "0")
                                            + _compact_share("Other", "0"))
fake_drobo = FakeDrobo(
    [
        _read_reply(
            _compact_share_config(
                _compact_share("Locked", "1") + _compact_share("Other", "0")
            )
        ),
        _accepted_reply(),
        _read_reply(after_revoke_config),
    ]
)
result = sharewrite.revoke_share_user(
    "127.0.0.1",
    SAMPLE_ESA_ID,
    "Locked",
    "Everyone",
    port=fake_drobo.port,
    timeout=5,
)
check(
    "revoke sets access 0",
    "<ShareUserAccess>0</ShareUserAccess>"
    in html.unescape(fake_drobo.sent[1]),
)

# Device accepts but read-back still shows the old value -> raise.
fake_drobo = FakeDrobo(
    [
        _read_reply(TWO_LOCKED_SHARES_CONFIG),
        _accepted_reply(),
        _read_reply(TWO_LOCKED_SHARES_CONFIG),
    ]
)  # verify still shows 0
try:
    sharewrite.set_share_user_access(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "Locked",
        "Everyone",
        "1",
        port=fake_drobo.port,
        timeout=5,
    )
    check("accepted-but-unapplied raises", False)
except ShareEditError as exc:
    check("accepted-but-unapplied raises", "not applied" in str(exc), str(exc))

print(
    "\n== duplicate/case-colliding user rows are refused, not half-changed =="
)
# A share whose ShareUsers holds TWO entries folding to the same name.
# Changing the first and leaving the second is how a revoke silently
# half-succeeds -- so this must refuse before any write, and send
# nothing.
CASE_DUPLICATE_USERS_SHARE = (
    "<Share><ShareName>Photos</ShareName><ShareState>0</ShareState>"
    "<TimeMachineEnabled>0</TimeMachineEnabled><ShareUsers>"
    "<ShareUser><ShareUsername>Everyone</ShareUsername>"
    "<ShareUserAccess>1</ShareUserAccess></ShareUser>"
    "<ShareUser><ShareUsername>everyone</ShareUsername>"
    "<ShareUserAccess>1</ShareUserAccess></ShareUser>"
    "</ShareUsers></Share>")
fake_drobo = FakeDrobo(
    [
        _read_reply(_compact_share_config(CASE_DUPLICATE_USERS_SHARE)),
        _accepted_reply(),
        _read_reply(_compact_share_config(CASE_DUPLICATE_USERS_SHARE)),
    ]
)
try:
    sharewrite.revoke_share_user(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "Photos",
        "Everyone",
        port=fake_drobo.port,
        timeout=5,
    )
    check("a revoke on colliding rows is refused", False)
except ShareEditError as exc:
    check(
        "a revoke on colliding rows is refused",
        "folding to" in str(exc),
        str(exc),
    )
check("...and NOTHING was written -- no half-revoke on the wire",
      len(fake_drobo.sent) == 1, fake_drobo.sent)

# Verify requires EVERY matching row to equal the target: a device that
# reads back one Everyone at 0 and a residual one at 1 must NOT report
# success.
RESIDUAL_GRANT_SHARE = ("<Share><ShareName>Photos</ShareName><ShareUsers>"
                        "<ShareUser><ShareUsername>Everyone</ShareUsername>"
                        "<ShareUserAccess>0</ShareUserAccess></ShareUser>"
                        "<ShareUser><ShareUsername>Everyone</ShareUsername>"
                        "<ShareUserAccess>1</ShareUserAccess></ShareUser>"
                        "</ShareUsers></Share>")
fake_drobo = FakeDrobo(
    [_read_reply(_compact_share_config(RESIDUAL_GRANT_SHARE))]
)  # one read for the verify
check("read-back with a residual grant fails verification",
      sharewrite._access_reads_back_as("127.0.0.1", SAMPLE_ESA_ID, "Photos",
                                       "Everyone",
                                       "0", fake_drobo.port, 5) is False)

print(
    "\n== share names differing only by case: exact wins, "
    "else refuse (audit HIGH) =="
)
# 'Photos' and 'photos' both exist. Requesting 'photos' must hit
# 'photos' exactly -- never the first case-insensitive match. Verified
# by asserting the write changed the SECOND share, not the first.
CASE_COLLIDING_CONFIG = _compact_share_config(_compact_share("Photos", "0")
                                              + _compact_share("photos", "0"))
after_lowercase_unlock_config = _compact_share_config(
    _compact_share("Photos", "0") + _compact_share("photos", "1"))
fake_drobo = FakeDrobo(
    [_read_reply(CASE_COLLIDING_CONFIG), _accepted_reply(),
     _read_reply(after_lowercase_unlock_config)])
result = sharewrite.set_share_user_access(
    "127.0.0.1",
    SAMPLE_ESA_ID,
    "photos",
    "Everyone",
    "1",
    port=fake_drobo.port,
    timeout=5,
)
written_document = html.unescape(fake_drobo.sent[1])
check(
    "an exact-case match is chosen over an earlier fold",
    "<ShareName>Photos</ShareName><ShareState>0</ShareState>"
    "<TimeMachineEnabled>0</TimeMachineEnabled>"
    "<ShareUsers><ShareUser><ShareUsername>Everyone</ShareUsername>"
    "<ShareUserAccess>0</ShareUserAccess>"
    in written_document
    and written_document.count("<ShareUserAccess>1</ShareUserAccess>") == 1,
    written_document,
)
check(
    "...and it verifies against the right share",
    result.get("verified") is True,
    result,
)

# Requesting 'PHOTOS' -- no exact match, two folds -> refuse, send
# nothing.
fake_drobo = FakeDrobo(
    [_read_reply(CASE_COLLIDING_CONFIG), _accepted_reply(),
     _read_reply(CASE_COLLIDING_CONFIG)])
try:
    sharewrite.set_share_user_access(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "PHOTOS",
        "Everyone",
        "1",
        port=fake_drobo.port,
        timeout=5,
    )
    check("an ambiguous fold with no exact match is refused", False)
except ShareEditError as exc:
    check(
        "an ambiguous fold with no exact match is refused",
        "Ambiguous" in str(exc),
        str(exc),
    )
check("...and nothing was written", len(fake_drobo.sent) == 1, fake_drobo.sent)

# Verify must not confirm the wrong share either: read-back where
# 'Photos'=1 but the requested 'photos'=0 must be False.
fake_drobo = FakeDrobo(
    [
        _read_reply(
            _compact_share_config(
                _compact_share("Photos", "1") + _compact_share("photos", "0")
            )
        )
    ]
)
check(
    "read-back resolves the exact share, not the first fold",
    sharewrite._access_reads_back_as(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "photos",
        "Everyone",
        "1",
        fake_drobo.port,
        5,
    )
    is False,
)

print("\n== a no-op write is skipped, not sent ==")
# Row already holds the target code -> nothing on the wire, reported as
# unchanged.
fake_drobo = FakeDrobo(
    [_read_reply(_compact_share_config(_compact_share("Locked", "1")))])
result = sharewrite.set_share_user_access(
    "127.0.0.1",
    SAMPLE_ESA_ID,
    "Locked",
    "Everyone",
    "1",
    port=fake_drobo.port,
    timeout=5,
)
check(
    "already-set access sends nothing (only the read happened)",
    len(fake_drobo.sent) == 1,
    fake_drobo.sent,
)
check(
    "...and reports unchanged + verified",
    result.get("unchanged") is True and result.get("verified") is True,
    result,
)

print("\n== malformed rows are refused, not half-edited ==")
DOUBLE_ACCESS_ELEMENT_SHARE = (
    "<Share><ShareName>Odd</ShareName><ShareUsers><ShareUser>"
    "<ShareUsername>Everyone</ShareUsername>"
    "<ShareUserAccess>0</ShareUserAccess><ShareUserAccess>1</ShareUserAccess>"
    "</ShareUser></ShareUsers></Share>")
fake_drobo = FakeDrobo(
    [_read_reply(_compact_share_config(DOUBLE_ACCESS_ELEMENT_SHARE)),
     _accepted_reply()])
try:
    sharewrite.set_share_user_access(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "Odd",
        "Everyone",
        "1",
        port=fake_drobo.port,
        timeout=5,
    )
    check("a user row with two <ShareUserAccess> is refused", False)
except ShareEditError as exc:
    check(
        "a user row with two <ShareUserAccess> is refused",
        "unambiguously" in str(exc),
        str(exc),
    )
check("...nothing written", len(fake_drobo.sent) == 1, fake_drobo.sent)

print(
    "\n== an accepted write whose verify read-back fails is named "
    "as ACCEPTED-unverified =="
)
# Read OK, write accepted, then the verify connection dies -> must NOT
# look like a rejected write. Only two replies queued: the third
# connection is refused.
fake_drobo = FakeDrobo(
    [_read_reply(TWO_LOCKED_SHARES_CONFIG), _accepted_reply()])
try:
    sharewrite.set_share_user_access(
        "127.0.0.1",
        SAMPLE_ESA_ID,
        "Locked",
        "Everyone",
        "1",
        port=fake_drobo.port,
        timeout=3,
    )
    check(
        "verify transport failure raises ShareWriteOutcomeUnknownError", False
    )
except sharewrite.ShareWriteOutcomeUnknownError as exc:
    check(
        "verify transport failure raises ShareWriteOutcomeUnknownError",
        "ACCEPTED" in str(exc),
        str(exc),
    )
    check(
        "...and warns not to blindly retry", "do NOT simply retry" in str(exc)
    )
except ShareEditError as exc:
    check(
        "verify transport failure raises ShareWriteOutcomeUnknownError "
        "(got plain ShareEditError)",
        False,
        str(exc),
    )
check("...it is still a ShareEditError subclass so broad handlers catch it",
      issubclass(sharewrite.ShareWriteOutcomeUnknownError, ShareEditError))

print("\n== a truncated reply is truncation, not a rejection ==")
# A reply whose declared length exceeds what arrives must raise 'short
# payload', not be parsed as a body lacking <Result> (which read as 'did
# not accept').
truncating_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
truncating_server.bind(("127.0.0.1", 0))
truncating_server.listen(1)


truncating_port = truncating_server.getsockname()[1]


def _serve_truncated_reply():
    connection, _ = truncating_server.accept()
    connection.settimeout(3)
    header = connection.recv(16)
    payload_length = struct.unpack(">I", header[12:16])[0]
    connection.recv(payload_length)  # login
    connection.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK, b""))
    header = connection.recv(16)
    payload_length = struct.unpack(">I", header[12:16])[0]
    connection.recv(payload_length)  # command
    untruncated_reply = _accepted_reply()
    frame = config_cmd._build_frame(config_cmd.TYPE_RESULT, untruncated_reply)
    connection.sendall(
        frame[: 16 + len(untruncated_reply) // 2]
    )  # header promises full, body cut short
    connection.close()
    truncating_server.close()


threading.Thread(target=_serve_truncated_reply, daemon=True).start()
try:
    config_cmd.get_config("127.0.0.1", SAMPLE_ESA_ID, "shares",
                          port=truncating_port, timeout=3)
    check("truncated payload raises", False)
except config_cmd.ConfigError as exc:
    check(
        "truncated payload is reported as 'short payload'",
        "short payload" in str(exc),
        str(exc),
    )

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
