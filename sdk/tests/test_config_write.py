"""
Tests for the configuration WRITE path -- config_cmd.set_config and the first
write built on it, droboapps.set_dimming.

    py test_config_write.py

This is the most safety-critical code in the SDK: it is the only sanctioned way
to send eCmdSetConfig, the single command the real Dashboard uses for every
config change including rename and share permissions. So the tests lean hard on
the ways a write could go WRONG -- a sibling field clobbered, an unknown field
dropped, a value out of band, the device not actually accepting it -- rather
than just proving the happy path.

Everything runs against a fake device on loopback. No hardware.

The write path does TWO exchanges: config_cmd.set_config reads the section
(one connection), then sends the modified document (a second connection). The
fake device below therefore serves a queue of replies, one per connection, and
records the bytes of the write so a test can assert exactly what went out.
"""
import html
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import droboapps  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


def _raises(attempt, expected_error_type):
    try:
        attempt()
        return False
    except expected_error_type:
        return True
    except Exception:
        return False


def _write_result_reply(code: int) -> bytes:
    """A reply carrying a <Result> code and no details -- what a write gets back."""
    return (f"<TMCmd><CmdID>31</CmdID><Result>{code}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


def _general_reply(feature_states="7", spindown_delay="14", dim_level="59",
                   extra_general_xml="") -> bytes:
    """
    A read reply for the General section, in the shape the REAL 5N returns:
    the fields wrapped in <DRINASConfig><General> (measured on firmware 4.3.1,
    2026-08-10 -- earlier fixtures used a flat shape and hid a wrapper bug that
    only surfaced on the first hardware read). `extra_general_xml` injects an
    unexpected element inside <General> so a test can prove the writer
    refuses to drop it.
    """
    general_fields_xml = (f"<FeatureStates>{feature_states}</FeatureStates>"
                          f"<SpindownDelay>{spindown_delay}</SpindownDelay>"
                          f"<Dim>{dim_level}</Dim>{extra_general_xml}")
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><General>{general_fields_xml}"
            f"</General></DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _dimming_reply(value) -> bytes:
    """A read-back reply for eCmdGetDimming: a bare number in <ResultDetails>."""
    return (f"<TMCmd><ResultDetails>{value}</ResultDetails></TMCmd>").encode() + b"\x00"


def _apps_reply(*names) -> bytes:
    """A eCmdGetDroboAppsStatus reply listing the given app names (all stopped)."""
    apps_xml = "".join(f"<App><Name>{n}</Name><Version>1.0</Version>"
                       f"<Stopped>1</Stopped></App>" for n in names)
    escaped_inner_xml = html.escape(f"<DroboApps><SDKVersion>2.1</SDKVersion>{apps_xml}</DroboApps>")
    return (f"<TMCmd><CmdID>77</CmdID><ResultDetails>{escaped_inner_xml}"
            "</ResultDetails>"
            f"</TMCmd>").encode() + b"\x00"


class FakeDrobo:
    """
    Serves a fixed queue of replies, one per incoming connection, and records
    the command payload of each. Enough to stand in for the read-then-write
    dance without a device.
    """

    def __init__(self, replies, login_ok=True):
        self.replies = list(replies)
        self.login_ok = login_ok
        self.sent = []  # command payloads received, in order
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
                connection.sendall(config_cmd._build_frame(config_cmd.TYPE_LOGIN_OK if self.login_ok else 0x99, b""))
                if not self.login_ok:
                    connection.close(); continue
                payload = self._receive_frame(connection)  # command
                self.sent.append((payload or b"").rstrip(b"\x00").decode("utf-8", "replace"))
                connection.sendall(
                    config_cmd._build_frame(config_cmd.TYPE_RESULT, reply))
                connection.close()
            except OSError:
                break
        self.server_socket.close()


SAMPLE_ESA_ID = "drb000000SAMPLE"

# ---------------------------------------------------------------------------
print("== the happy path: change only Dim, keep the siblings ==")

# Three connections now: read General, write, read-back to verify it applied.
fake_drobo = FakeDrobo([_general_reply(dim_level="59"), _write_result_reply(config_cmd.RESULT_ACCEPTED), _dimming_reply(10)])
result = droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                               port=fake_drobo.port, timeout=5)
check("set_dimming reports the accepted result",
      result["result_code"] == config_cmd.RESULT_ACCEPTED, result)
check("the write is reported verified after read-back", result.get("verified") is True, result)

# The second connection carried the write. Unescape it and look.
written_document = (html.unescape(fake_drobo.sent[1])
                    if len(fake_drobo.sent) > 1 else "")
check("a write was sent, then a read-back", len(fake_drobo.sent) == 3,
      fake_drobo.sent)
check("the write wraps the block in <DRINASConfig>", "<DRINASConfig>" in written_document, written_document)
check("the new brightness is on the wire", "<Dim>10</Dim>" in written_document,
      written_document)
check("FeatureStates was preserved from the read, not invented",
      "<FeatureStates>7</FeatureStates>" in written_document, written_document)
check("SpindownDelay was preserved from the read",
      "<SpindownDelay>14</SpindownDelay>" in written_document,
      written_document)
check("field order matches the device's own",
      "<FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay><Dim>10</Dim>"
      in written_document, written_document)
check("the old brightness is gone", "<Dim>59</Dim>" not in written_document,
      written_document)
check("the write is escaped on the wire, not raw",
      "&lt;DRINASConfig&gt;" in fake_drobo.sent[1], fake_drobo.sent[1][:60])

print("\n== reversibility: the value round-trips through the band edges ==")
for brightness in (0, 255, 1):
    fake_drobo = FakeDrobo([_general_reply(dim_level="59"), _write_result_reply(config_cmd.RESULT_ACCEPTED), _dimming_reply(brightness)])
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, brightness,
                          port=fake_drobo.port, timeout=5)
    written_document = html.unescape(fake_drobo.sent[1])
    check(f"brightness {brightness} is written verbatim",
          f"<Dim>{brightness}</Dim>" in written_document, written_document)

print("\n== accepted is not applied: read-back must agree ==")
# Device says ACCEPTED but the brightness did not change -> must raise.
fake_drobo = FakeDrobo([_general_reply(dim_level="59"), _write_result_reply(config_cmd.RESULT_ACCEPTED), _dimming_reply(59)])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=fake_drobo.port, timeout=5)
    check("accepted-but-unchanged raises", False)
except droboapps.DimmingWriteError as exc:
    check("accepted-but-unchanged raises DimmingWriteError", "not applied" in str(exc), str(exc))

# verify=False skips the read-back (only read + write connections).
fake_drobo = FakeDrobo(
    [_general_reply(dim_level="59"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED)])
result = droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10, port=fake_drobo.port, timeout=5, verify=False)
check("verify=False skips the read-back", len(fake_drobo.sent) == 2 and "verified" not in result, result)

print("\n== the guards: a bad value never reaches the device ==")


def refusal_for(value):
    fake_drobo = FakeDrobo(
        [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
    try:
        droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, value,
                              port=fake_drobo.port, timeout=5)
        return None
    except droboapps.DimmingWriteError as exc:
        return (str(exc), len(fake_drobo.sent))


for bad_value, reason in [(-1, "negative"), (256, "above the band"), (5900, "fat-fingered")]:
    refusal = refusal_for(bad_value)
    check(f"{reason} value ({bad_value}) is refused",
          refusal is not None, refusal)
    check(f"...and nothing was sent for {bad_value}",
          refusal and refusal[1] == 0, refusal)

check("True is refused (a bool is not a brightness)", refusal_for(True) is not None)
check("a float is refused", refusal_for(3.5) is not None)

print("\n== the siblings are sacred: unknown/missing fields refuse the write ==")

# An unknown field in the block -> refuse, do not drop it. Block-preservation
# failures raise GeneralWriteError (the shared base), since they are shared by
# every General-block write, not just brightness.
fake_drobo = FakeDrobo(
    [_general_reply(extra_general_xml="<NightMode>1</NightMode>"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=fake_drobo.port, timeout=5)
    check("an unknown General field refuses the write", False)
except droboapps.GeneralWriteError as exc:
    check("an unknown General field refuses the write", "does not know how to" in str(exc), str(exc))
check("...and the write was never sent (only the read happened)",
      len(fake_drobo.sent) == 1, fake_drobo.sent)

# A missing expected field -> refuse rather than write a block unlike what was read.
fake_drobo = FakeDrobo([("<TMCmd><CmdID>30</CmdID><ResultDetails>"
                         "<FeatureStates>7</FeatureStates><Dim>59</Dim>"
                         "</ResultDetails></TMCmd>").encode() + b"\x00",
                        _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=fake_drobo.port, timeout=5)
    check("a missing General field refuses the write", False)
except droboapps.GeneralWriteError as exc:
    check("a missing General field refuses the write", "missing expected" in str(exc), str(exc))

print("\n== the device's verdict is believed, not assumed ==")

# A well-formed reply that is NOT the accept code must raise -- the Identify
# lesson: accepted is not the same as replied.
fake_drobo = FakeDrobo([_general_reply(), _write_result_reply(0)])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=fake_drobo.port, timeout=5)
    check("a non-accept Result raises", False)
except config_cmd.ConfigWriteError as exc:
    check("a non-accept Result raises ConfigWriteError", "did not accept" in str(exc), str(exc))

# A reply with no <Result> at all is also not an accept.
fake_drobo = FakeDrobo(
    [_general_reply(),
     b"<TMCmd><CmdID>31</CmdID><ResultDetails/></TMCmd>\x00"])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=fake_drobo.port, timeout=5)
    check("a Result-less reply raises", False)
except config_cmd.ConfigWriteError:
    check("a Result-less reply raises ConfigWriteError", True)

print("\n== set_config itself: the transport guarantees ==")

# build_section_document returning junk is refused before anything is
# sent.
fake_drobo = FakeDrobo(
    [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    config_cmd.set_config("127.0.0.1", SAMPLE_ESA_ID, "general", lambda before: "", port=fake_drobo.port, timeout=5)
    check("an empty build_section_document is refused", False)
except config_cmd.ConfigWriteError:
    check("an empty build_section_document is refused", True)

fake_drobo = FakeDrobo(
    [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    config_cmd.set_config("127.0.0.1", SAMPLE_ESA_ID, "general", lambda before: "not xml", port=fake_drobo.port, timeout=5)
    check("a non-XML build_section_document is refused", False)
except config_cmd.ConfigWriteError:
    check("a non-XML build_section_document is refused", True)

check("an unknown section is refused",
      _raises(
          lambda: config_cmd.set_config("127.0.0.1", SAMPLE_ESA_ID, "nonsense",
                                        lambda before: "<x/>", port=1,
                                        timeout=1),
          config_cmd.ConfigWriteError))

# build_section_document sees the REAL prior read, not an empty dict.
captured_baseline = {}
fake_drobo = FakeDrobo(
    [_general_reply(feature_states="5", spindown_delay="0", dim_level="59"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED)])


def _capture_baseline(before):
    captured_baseline.update({"before": before})
    return "<General><FeatureStates>5</FeatureStates><SpindownDelay>0</SpindownDelay><Dim>2</Dim></General>"


config_cmd.set_config("127.0.0.1", SAMPLE_ESA_ID, "general", _capture_baseline,
                      port=fake_drobo.port,
                      timeout=5)
check("build_section_document is handed the device's current document",
      isinstance(captured_baseline.get("before"), dict)
      and captured_baseline["before"], captured_baseline)

print("\n== the write is confined to the section it read ==")
# A build_section_document whose document is NOT the section's element is
# refused, so an empty-DRIShareConfig 'wipe every share' cannot be shipped under
# section=general.
fake_drobo = FakeDrobo(
    [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    config_cmd.set_config("127.0.0.1", SAMPLE_ESA_ID, "general",
                          lambda before: "<DRIShareConfig></DRIShareConfig>",
                          port=fake_drobo.port, timeout=5)
    check("a document for the wrong section is refused", False)
except config_cmd.ConfigWriteError as exc:
    check("a document for the wrong section is refused", "does not match" in str(exc), str(exc))
check("...and nothing was written (only the read happened)", len(fake_drobo.sent) == 1, fake_drobo.sent)

print("\n== a masked credential is never written back ==")
# The write baseline read is un-redacted (redact=False), so a real value is
# preserved. But belt-and-braces: if the sentinel ever appears in the built
# document, set_config refuses rather than writing "[redacted]" over a secret.
fake_drobo = FakeDrobo(
    [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    config_cmd.set_config(
        "127.0.0.1", SAMPLE_ESA_ID, "general",
        lambda before: (
            "<General><FeatureStates>[redacted]</FeatureStates>"
            "<SpindownDelay>0</SpindownDelay><Dim>1</Dim></General>"),
        port=fake_drobo.port, timeout=5)
    check("a document carrying the redaction sentinel is refused", False)
except config_cmd.ConfigWriteError as exc:
    check("a document carrying the redaction sentinel is refused",
          "redaction sentinel" in str(exc), str(exc))
check("...and the masked write never reached the device", len(fake_drobo.sent) == 1, fake_drobo.sent)

# And the baseline read really is un-redacted: a secret-named field in the
# General reply comes through to build_section_document intact, not
# masked.
grabbed_baseline = {}
fake_drobo = FakeDrobo([("<TMCmd><CmdID>30</CmdID><ResultDetails>"
                         "<FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay>"
                         "<Dim>59</Dim><AccessKey>hunter2</AccessKey>"
                         "</ResultDetails></TMCmd>").encode() + b"\x00",
                        _write_result_reply(config_cmd.RESULT_ACCEPTED)])


def _grab_baseline_then_stop(before):
    grabbed_baseline.update(before)
    raise config_cmd.ConfigWriteError("stop here, we only wanted the read")


try:
    config_cmd.set_config("127.0.0.1", SAMPLE_ESA_ID, "general",
                          _grab_baseline_then_stop, port=fake_drobo.port,
                          timeout=5)
except config_cmd.ConfigWriteError:
    pass
check("the write-path read is NOT redacted "
      "(real secret reaches build_section_document)",
      grabbed_baseline.get("AccessKey") == "hunter2", grabbed_baseline)

print("\n== the gate is real: eCmdSetConfig is a vetted write ==")
check("eCmdSetConfig is on the vetted-writes list", "eCmdSetConfig" in commands.VETTED_WRITES)
check("...and is STILL refused to ordinary senders (in DO_NOT_SEND)",
      "eCmdSetConfig" in commands.DO_NOT_SEND)

print("\n== a non-scalar General sibling is refused, not repr'd ==")
# _element_to_dict yields a dict for an element with children; set_dimming must
# not serialize {'x': '1'} into the block.
fake_drobo = FakeDrobo([("<TMCmd><CmdID>30</CmdID><ResultDetails>"
                         "<FeatureStates><Nested>1</Nested></FeatureStates>"
                         "<SpindownDelay>14</SpindownDelay><Dim>59</Dim>"
                         "</ResultDetails></TMCmd>").encode() + b"\x00",
                        _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    droboapps.set_dimming("127.0.0.1", SAMPLE_ESA_ID, 10,
                          port=fake_drobo.port, timeout=5)
    check("a nested sibling value refuses the write", False)
except droboapps.GeneralWriteError as exc:
    check("a nested sibling value refuses the write", "not a scalar" in str(exc), str(exc))
check("...and nothing was written", len(fake_drobo.sent) == 1, fake_drobo.sent)

print("\n== spin-down rides the same vetted General path ==")
# A nonzero delay while spin-down is already enabled (bit set, FeatureStates=7)
# changes only SpindownDelay and keeps Dim + FeatureStates.
fake_drobo = FakeDrobo(
    [_general_reply(feature_states="7", spindown_delay="14", dim_level="59"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED),
     _general_reply(feature_states="7", spindown_delay="30", dim_level="59")])
result = droboapps.set_spindown("127.0.0.1", SAMPLE_ESA_ID, 30,
                                port=fake_drobo.port, timeout=5)
written_document = html.unescape(fake_drobo.sent[1])
check("a nonzero delay changes only its own field",
      "<FeatureStates>7</FeatureStates><SpindownDelay>30</SpindownDelay><Dim>59</Dim>" in written_document, written_document)
check("the LED brightness is untouched by a spin-down write", "<Dim>59</Dim>" in written_document, written_document)
check("spin-down verifies via read-back",
      result.get("verified") is True, result)

# MEASURED ON HARDWARE 2026-08-12: a bare delay of 0 does NOT disable spin-down.
# With the enable bit set the device accepts 0, returns the success code, and
# stores 15. So set_spindown(0) must be REFUSED before any network write.
fake_drobo = FakeDrobo(
    [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    droboapps.set_spindown("127.0.0.1", SAMPLE_ESA_ID, 0,
                           port=fake_drobo.port, timeout=5)
    check("set_spindown(0) is refused (device coerces a bare 0)", False)
except droboapps.SpindownWriteError as exc:
    check("set_spindown(0) is refused (device coerces a bare 0)",
          "disable_spindown" in str(exc), str(exc))
check("...and nothing was sent", len(fake_drobo.sent) == 0, fake_drobo.sent)

# Enabling from OFF: the bit must be set in the SAME write as the delay.
fake_drobo = FakeDrobo(
    [_general_reply(feature_states="5", spindown_delay="0", dim_level="59"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED),
     _general_reply(feature_states="7", spindown_delay="14", dim_level="59")])
droboapps.set_spindown("127.0.0.1", SAMPLE_ESA_ID, 14,
                       port=fake_drobo.port, timeout=5)
written_document = html.unescape(fake_drobo.sent[1])
check("enabling from off sets the FeatureStates bit AND the delay together",
      "<FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay>" in written_document, written_document)

# disable = clear the bit AND zero the delay in ONE write -- the exact pair the
# original Dashboard sends (capture 2026-08-06: 7/14 -> 5/0).
fake_drobo = FakeDrobo(
    [_general_reply(feature_states="7", spindown_delay="14", dim_level="59"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED),
     _general_reply(feature_states="5", spindown_delay="0", dim_level="59")])
result = droboapps.disable_spindown("127.0.0.1", SAMPLE_ESA_ID,
                                    port=fake_drobo.port, timeout=5)
written_document = html.unescape(fake_drobo.sent[1])
check("disable clears the bit and zeroes the delay in one write",
      "<FeatureStates>5</FeatureStates><SpindownDelay>0</SpindownDelay><Dim>59</Dim>" in written_document, written_document)
check("disable verifies BOTH fields on read-back", result.get("verified") is True, result)

# A disable the device did not actually apply (bit still set) must raise.
fake_drobo = FakeDrobo(
    [_general_reply(feature_states="7", spindown_delay="14", dim_level="59"),
     _write_result_reply(config_cmd.RESULT_ACCEPTED),
     _general_reply(feature_states="7", spindown_delay="15", dim_level="59")])  # coerced, as measured
try:
    droboapps.disable_spindown("127.0.0.1", SAMPLE_ESA_ID,
                               port=fake_drobo.port, timeout=5)
    check("a coerced/unapplied disable raises", False)
except droboapps.SpindownWriteError as exc:
    check("a coerced/unapplied disable raises", "not applied" in str(exc), str(exc))

# Its guards are the shared ones (block preservation) plus its own band check.
fake_drobo = FakeDrobo(
    [_general_reply(), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
check("a spin-down value beyond the band is refused",
      _raises(lambda: droboapps.set_spindown("127.0.0.1", SAMPLE_ESA_ID, 99999, port=fake_drobo.port, timeout=5),
              droboapps.SpindownWriteError))
check("...and it is a GeneralWriteError too (shared base)",
      issubclass(droboapps.SpindownWriteError, droboapps.GeneralWriteError)
      and issubclass(droboapps.DimmingWriteError, droboapps.GeneralWriteError))

print("\n== DroboApps start/stop: a gated, validated direct write ==")

# Happy path: read the app list (AlphaApp installed), then Stop it.
fake_drobo = FakeDrobo([_apps_reply("AlphaApp", "betaapp"), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
result = droboapps.droboapps_action("127.0.0.1", SAMPLE_ESA_ID, "AlphaApp", "Stop", port=fake_drobo.port, timeout=5)
check("the action reports accepted", result["result_code"] == config_cmd.RESULT_ACCEPTED, result)
check("the confirmed params are on the wire",
      "<Name>AlphaApp</Name><Action>Stop</Action>" in fake_drobo.sent[1],
      fake_drobo.sent[1])
check("it read the app list first, then acted", len(fake_drobo.sent) == 2,
      fake_drobo.sent)

# A bad verb is refused before anything is read or sent.
fake_drobo = FakeDrobo(
    [_apps_reply("AlphaApp"), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
check("a lowercase 'start' is refused",
      _raises(lambda: droboapps.droboapps_action("127.0.0.1", SAMPLE_ESA_ID, "AlphaApp", "start",
                                                 port=fake_drobo.port,
                                                 timeout=5),
              droboapps.DroboAppsActionError))
check("...and nothing was sent for a bad verb", len(fake_drobo.sent) == 0,
      fake_drobo.sent)

# An app that is not installed is refused (the read happened, the action did not).
fake_drobo = FakeDrobo(
    [_apps_reply("AlphaApp"), _write_result_reply(config_cmd.RESULT_ACCEPTED)])
try:
    droboapps.droboapps_action("127.0.0.1", SAMPLE_ESA_ID, "Nonexistent", "Start", port=fake_drobo.port, timeout=5)
    check("an uninstalled app is refused", False)
except droboapps.DroboAppsActionError as exc:
    check("an uninstalled app is refused", "not an installed" in str(exc), str(exc))
check("...and the action was never sent (only the status read)", len(fake_drobo.sent) == 1, fake_drobo.sent)

# check_installed=False skips the read and sends directly.
fake_drobo = FakeDrobo([_write_result_reply(config_cmd.RESULT_ACCEPTED)])
droboapps.droboapps_action("127.0.0.1", SAMPLE_ESA_ID, "AlphaApp", "Start", port=fake_drobo.port, timeout=5,
                           check_installed=False)
check("check_installed=False skips the status read", len(fake_drobo.sent) == 1, fake_drobo.sent)

# A non-accept result raises.
fake_drobo = FakeDrobo([_apps_reply("AlphaApp"), _write_result_reply(0)])
try:
    droboapps.droboapps_action("127.0.0.1", SAMPLE_ESA_ID, "AlphaApp", "Stop", port=fake_drobo.port, timeout=5)
    check("a non-accept result raises", False)
except droboapps.DroboAppsActionError as exc:
    check("a non-accept result raises DroboAppsActionError", "did not accept" in str(exc), str(exc))

check("eCmdDroboAppsAction is a vetted write, still in DO_NOT_SEND",
      "eCmdDroboAppsAction" in commands.VETTED_WRITES and "eCmdDroboAppsAction" in commands.DO_NOT_SEND)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
