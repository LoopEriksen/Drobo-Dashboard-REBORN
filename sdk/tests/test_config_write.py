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

from drobo_nasd import config_cmd as cc
from drobo_nasd import droboapps as da

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def _raises(fn, exc_type):
    try:
        fn()
        return False
    except exc_type:
        return True
    except Exception:
        return False


def _result(code: int) -> bytes:
    """A reply carrying a <Result> code and no details -- what a write gets back."""
    return (f"<TMCmd><CmdID>31</CmdID><Result>{code}</Result>"
            f"<ResultDetails/></TMCmd>").encode() + b"\x00"


def _general_reply(feature="7", spindown="14", dim="59", extra="") -> bytes:
    """
    A read reply for the General section, in the shape the REAL 5N returns:
    the fields wrapped in <DRINASConfig><General> (measured on firmware 4.3.1,
    2026-08-10 -- earlier fixtures used a flat shape and hid a wrapper bug that
    only surfaced on the first hardware read). `extra` injects an unexpected
    element inside <General> so a test can prove the writer refuses to drop it.
    """
    body = (f"<FeatureStates>{feature}</FeatureStates>"
            f"<SpindownDelay>{spindown}</SpindownDelay>"
            f"<Dim>{dim}</Dim>{extra}")
    return (f"<TMCmd><CmdID>30</CmdID><ResultDetails><DRINASConfig><General>{body}"
            f"</General></DRINASConfig></ResultDetails></TMCmd>").encode() + b"\x00"


def _dimming_reply(value) -> bytes:
    """A read-back reply for eCmdGetDimming: a bare number in <ResultDetails>."""
    return (f"<TMCmd><ResultDetails>{value}</ResultDetails></TMCmd>").encode() + b"\x00"


def _apps_reply(*names) -> bytes:
    """A eCmdGetDroboAppsStatus reply listing the given app names (all stopped)."""
    apps = "".join(f"<App><Name>{n}</Name><Version>1.0</Version>"
                   f"<Stopped>1</Stopped></App>" for n in names)
    inner = html.escape(f"<DroboApps><SDKVersion>2.1</SDKVersion>{apps}</DroboApps>")
    return (f"<TMCmd><CmdID>77</CmdID><ResultDetails>{inner}</ResultDetails>"
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
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(4)
        threading.Thread(target=self._run, daemon=True).start()

    def _recv_frame(self, conn):
        head = conn.recv(16)
        if len(head) < 16:
            return None
        ln = struct.unpack(">I", head[12:16])[0]
        buf = b""
        while len(buf) < ln:
            chunk = conn.recv(ln - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def _run(self):
        for reply in self.replies:
            try:
                conn, _ = self.srv.accept()
                conn.settimeout(5)
                self._recv_frame(conn)  # login
                conn.sendall(cc._build_frame(cc.TYPE_LOGIN_OK if self.login_ok else 0x99, b""))
                if not self.login_ok:
                    conn.close(); continue
                payload = self._recv_frame(conn)  # command
                self.sent.append((payload or b"").rstrip(b"\x00").decode("utf-8", "replace"))
                conn.sendall(cc._build_frame(cc.TYPE_RESULT, reply))
                conn.close()
            except OSError:
                break
        self.srv.close()


ESA = "drb000000SAMPLE"

# ---------------------------------------------------------------------------
print("== the happy path: change only Dim, keep the siblings ==")

# Three connections now: read General, write, read-back to verify it applied.
dev = FakeDrobo([_general_reply(dim="59"), _result(cc.RESULT_ACCEPTED), _dimming_reply(10)])
res = da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
check("set_dimming reports the accepted result",
      res["result_code"] == cc.RESULT_ACCEPTED, res)
check("the write is reported verified after read-back", res.get("verified") is True, res)

# The second connection carried the write. Unescape it and look.
write = html.unescape(dev.sent[1]) if len(dev.sent) > 1 else ""
check("a write was sent, then a read-back", len(dev.sent) == 3, dev.sent)
check("the write wraps the block in <DRINASConfig>", "<DRINASConfig>" in write, write)
check("the new brightness is on the wire", "<Dim>10</Dim>" in write, write)
check("FeatureStates was preserved from the read, not invented",
      "<FeatureStates>7</FeatureStates>" in write, write)
check("SpindownDelay was preserved from the read",
      "<SpindownDelay>14</SpindownDelay>" in write, write)
check("field order matches the device's own",
      "<FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay><Dim>10</Dim>"
      in write, write)
check("the old brightness is gone", "<Dim>59</Dim>" not in write, write)
check("the write is escaped on the wire, not raw",
      "&lt;DRINASConfig&gt;" in dev.sent[1], dev.sent[1][:60])

print("\n== reversibility: the value round-trips through the band edges ==")
for v in (0, 255, 1):
    d = FakeDrobo([_general_reply(dim="59"), _result(cc.RESULT_ACCEPTED), _dimming_reply(v)])
    da.set_dimming("127.0.0.1", ESA, v, port=d.port, timeout=5)
    w = html.unescape(d.sent[1])
    check(f"brightness {v} is written verbatim", f"<Dim>{v}</Dim>" in w, w)

print("\n== accepted is not applied: read-back must agree ==")
# Device says ACCEPTED but the brightness did not change -> must raise.
dev = FakeDrobo([_general_reply(dim="59"), _result(cc.RESULT_ACCEPTED), _dimming_reply(59)])
try:
    da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("accepted-but-unchanged raises", False)
except da.DimmingWriteError as exc:
    check("accepted-but-unchanged raises DimmingWriteError", "not applied" in str(exc), str(exc))

# verify=False skips the read-back (only read + write connections).
dev = FakeDrobo([_general_reply(dim="59"), _result(cc.RESULT_ACCEPTED)])
res = da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5, verify=False)
check("verify=False skips the read-back", len(dev.sent) == 2 and "verified" not in res, res)

print("\n== the guards: a bad value never reaches the device ==")


def refused(value):
    d = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
    try:
        da.set_dimming("127.0.0.1", ESA, value, port=d.port, timeout=5)
        return None
    except da.DimmingWriteError as exc:
        return (str(exc), len(d.sent))


for bad, why in [(-1, "negative"), (256, "above the band"), (5900, "fat-fingered")]:
    r = refused(bad)
    check(f"{why} value ({bad}) is refused", r is not None, r)
    check(f"...and nothing was sent for {bad}", r and r[1] == 0, r)

check("True is refused (a bool is not a brightness)", refused(True) is not None)
check("a float is refused", refused(3.5) is not None)

print("\n== the siblings are sacred: unknown/missing fields refuse the write ==")

# An unknown field in the block -> refuse, do not drop it. Block-preservation
# failures raise GeneralWriteError (the shared base), since they are shared by
# every General-block write, not just brightness.
dev = FakeDrobo([_general_reply(extra="<NightMode>1</NightMode>"),
                 _result(cc.RESULT_ACCEPTED)])
try:
    da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("an unknown General field refuses the write", False)
except da.GeneralWriteError as exc:
    check("an unknown General field refuses the write", "does not know how to" in str(exc), str(exc))
check("...and the write was never sent (only the read happened)",
      len(dev.sent) == 1, dev.sent)

# A missing expected field -> refuse rather than write a block unlike what was read.
dev = FakeDrobo([(f"<TMCmd><CmdID>30</CmdID><ResultDetails>"
                  f"<FeatureStates>7</FeatureStates><Dim>59</Dim>"
                  f"</ResultDetails></TMCmd>").encode() + b"\x00",
                 _result(cc.RESULT_ACCEPTED)])
try:
    da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("a missing General field refuses the write", False)
except da.GeneralWriteError as exc:
    check("a missing General field refuses the write", "missing expected" in str(exc), str(exc))

print("\n== the device's verdict is believed, not assumed ==")

# A well-formed reply that is NOT the accept code must raise -- the Identify
# lesson: accepted is not the same as replied.
dev = FakeDrobo([_general_reply(), _result(0)])
try:
    da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("a non-accept Result raises", False)
except cc.ConfigWriteError as exc:
    check("a non-accept Result raises ConfigWriteError", "did not accept" in str(exc), str(exc))

# A reply with no <Result> at all is also not an accept.
dev = FakeDrobo([_general_reply(),
                 b"<TMCmd><CmdID>31</CmdID><ResultDetails/></TMCmd>\x00"])
try:
    da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("a Result-less reply raises", False)
except cc.ConfigWriteError:
    check("a Result-less reply raises ConfigWriteError", True)

print("\n== set_config itself: the transport guarantees ==")

# build_section_document returning junk is refused before anything is
# sent.
dev = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
try:
    cc.set_config("127.0.0.1", ESA, "general", lambda before: "", port=dev.port, timeout=5)
    check("an empty build_section_document is refused", False)
except cc.ConfigWriteError:
    check("an empty build_section_document is refused", True)

dev = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
try:
    cc.set_config("127.0.0.1", ESA, "general", lambda before: "not xml", port=dev.port, timeout=5)
    check("a non-XML build_section_document is refused", False)
except cc.ConfigWriteError:
    check("a non-XML build_section_document is refused", True)

check("an unknown section is refused",
      _raises(lambda: cc.set_config("127.0.0.1", ESA, "nonsense",
                                    lambda b: "<x/>", port=1, timeout=1),
              cc.ConfigWriteError))

# build_section_document sees the REAL prior read, not an empty dict.
seen = {}
dev = FakeDrobo([_general_reply(feature="5", spindown="0", dim="59"),
                 _result(cc.RESULT_ACCEPTED)])


def _capture(before):
    seen.update({"before": before})
    return "<General><FeatureStates>5</FeatureStates><SpindownDelay>0</SpindownDelay><Dim>2</Dim></General>"


cc.set_config("127.0.0.1", ESA, "general", _capture, port=dev.port, timeout=5)
check("build_section_document is handed the device's current document",
      isinstance(seen.get("before"), dict) and seen["before"], seen)

print("\n== the write is confined to the section it read ==")
# A build_section_document whose document is NOT the section's element is
# refused, so an empty-DRIShareConfig 'wipe every share' cannot be shipped under
# section=general.
dev = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
try:
    cc.set_config("127.0.0.1", ESA, "general",
                  lambda b: "<DRIShareConfig></DRIShareConfig>", port=dev.port, timeout=5)
    check("a document for the wrong section is refused", False)
except cc.ConfigWriteError as exc:
    check("a document for the wrong section is refused", "does not match" in str(exc), str(exc))
check("...and nothing was written (only the read happened)", len(dev.sent) == 1, dev.sent)

print("\n== a masked credential is never written back ==")
# The write baseline read is un-redacted (redact=False), so a real value is
# preserved. But belt-and-braces: if the sentinel ever appears in the built
# document, set_config refuses rather than writing "[redacted]" over a secret.
dev = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
try:
    cc.set_config("127.0.0.1", ESA, "general",
                  lambda b: "<General><FeatureStates>[redacted]</FeatureStates>"
                            "<SpindownDelay>0</SpindownDelay><Dim>1</Dim></General>",
                  port=dev.port, timeout=5)
    check("a document carrying the redaction sentinel is refused", False)
except cc.ConfigWriteError as exc:
    check("a document carrying the redaction sentinel is refused",
          "redaction sentinel" in str(exc), str(exc))
check("...and the masked write never reached the device", len(dev.sent) == 1, dev.sent)

# And the baseline read really is un-redacted: a secret-named field in the
# General reply comes through to build_section_document intact, not
# masked.
grabbed = {}
dev = FakeDrobo([(f"<TMCmd><CmdID>30</CmdID><ResultDetails>"
                  f"<FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay>"
                  f"<Dim>59</Dim><AccessKey>hunter2</AccessKey>"
                  f"</ResultDetails></TMCmd>").encode() + b"\x00",
                 _result(cc.RESULT_ACCEPTED)])


def _grab(before):
    grabbed.update(before)
    raise cc.ConfigWriteError("stop here, we only wanted the read")


try:
    cc.set_config("127.0.0.1", ESA, "general", _grab, port=dev.port, timeout=5)
except cc.ConfigWriteError:
    pass
check("the write-path read is NOT redacted "
      "(real secret reaches build_section_document)",
      grabbed.get("AccessKey") == "hunter2", grabbed)

print("\n== the gate is real: eCmdSetConfig is a vetted write ==")
from drobo_nasd import commands as C
check("eCmdSetConfig is on the vetted-writes list", "eCmdSetConfig" in C.VETTED_WRITES)
check("...and is STILL refused to ordinary senders (in DO_NOT_SEND)",
      "eCmdSetConfig" in C.DO_NOT_SEND)

print("\n== a non-scalar General sibling is refused, not repr'd ==")
# _element_to_dict yields a dict for an element with children; set_dimming must
# not serialize {'x': '1'} into the block.
dev = FakeDrobo([(f"<TMCmd><CmdID>30</CmdID><ResultDetails>"
                  f"<FeatureStates><Nested>1</Nested></FeatureStates>"
                  f"<SpindownDelay>14</SpindownDelay><Dim>59</Dim>"
                  f"</ResultDetails></TMCmd>").encode() + b"\x00",
                 _result(cc.RESULT_ACCEPTED)])
try:
    da.set_dimming("127.0.0.1", ESA, 10, port=dev.port, timeout=5)
    check("a nested sibling value refuses the write", False)
except da.GeneralWriteError as exc:
    check("a nested sibling value refuses the write", "not a scalar" in str(exc), str(exc))
check("...and nothing was written", len(dev.sent) == 1, dev.sent)

print("\n== spin-down rides the same vetted General path ==")
# A nonzero delay while spin-down is already enabled (bit set, FeatureStates=7)
# changes only SpindownDelay and keeps Dim + FeatureStates.
dev = FakeDrobo([_general_reply(feature="7", spindown="14", dim="59"),
                 _result(cc.RESULT_ACCEPTED),
                 _general_reply(feature="7", spindown="30", dim="59")])
res = da.set_spindown("127.0.0.1", ESA, 30, port=dev.port, timeout=5)
w = html.unescape(dev.sent[1])
check("a nonzero delay changes only its own field",
      "<FeatureStates>7</FeatureStates><SpindownDelay>30</SpindownDelay><Dim>59</Dim>" in w, w)
check("the LED brightness is untouched by a spin-down write", "<Dim>59</Dim>" in w, w)
check("spin-down verifies via read-back", res.get("verified") is True, res)

# MEASURED ON HARDWARE 2026-08-12: a bare delay of 0 does NOT disable spin-down.
# With the enable bit set the device accepts 0, returns the success code, and
# stores 15. So set_spindown(0) must be REFUSED before any network write.
d = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
try:
    da.set_spindown("127.0.0.1", ESA, 0, port=d.port, timeout=5)
    check("set_spindown(0) is refused (device coerces a bare 0)", False)
except da.SpindownWriteError as exc:
    check("set_spindown(0) is refused (device coerces a bare 0)",
          "disable_spindown" in str(exc), str(exc))
check("...and nothing was sent", len(d.sent) == 0, d.sent)

# Enabling from OFF: the bit must be set in the SAME write as the delay.
dev = FakeDrobo([_general_reply(feature="5", spindown="0", dim="59"),
                 _result(cc.RESULT_ACCEPTED),
                 _general_reply(feature="7", spindown="14", dim="59")])
da.set_spindown("127.0.0.1", ESA, 14, port=dev.port, timeout=5)
w = html.unescape(dev.sent[1])
check("enabling from off sets the FeatureStates bit AND the delay together",
      "<FeatureStates>7</FeatureStates><SpindownDelay>14</SpindownDelay>" in w, w)

# disable = clear the bit AND zero the delay in ONE write -- the exact pair the
# original Dashboard sends (capture 2026-08-06: 7/14 -> 5/0).
dev = FakeDrobo([_general_reply(feature="7", spindown="14", dim="59"),
                 _result(cc.RESULT_ACCEPTED),
                 _general_reply(feature="5", spindown="0", dim="59")])
res = da.disable_spindown("127.0.0.1", ESA, port=dev.port, timeout=5)
w = html.unescape(dev.sent[1])
check("disable clears the bit and zeroes the delay in one write",
      "<FeatureStates>5</FeatureStates><SpindownDelay>0</SpindownDelay><Dim>59</Dim>" in w, w)
check("disable verifies BOTH fields on read-back", res.get("verified") is True, res)

# A disable the device did not actually apply (bit still set) must raise.
dev = FakeDrobo([_general_reply(feature="7", spindown="14", dim="59"),
                 _result(cc.RESULT_ACCEPTED),
                 _general_reply(feature="7", spindown="15", dim="59")])  # coerced, as measured
try:
    da.disable_spindown("127.0.0.1", ESA, port=dev.port, timeout=5)
    check("a coerced/unapplied disable raises", False)
except da.SpindownWriteError as exc:
    check("a coerced/unapplied disable raises", "not applied" in str(exc), str(exc))

# Its guards are the shared ones (block preservation) plus its own band check.
d = FakeDrobo([_general_reply(), _result(cc.RESULT_ACCEPTED)])
check("a spin-down value beyond the band is refused",
      _raises(lambda: da.set_spindown("127.0.0.1", ESA, 99999, port=d.port, timeout=5),
              da.SpindownWriteError))
check("...and it is a GeneralWriteError too (shared base)",
      issubclass(da.SpindownWriteError, da.GeneralWriteError)
      and issubclass(da.DimmingWriteError, da.GeneralWriteError))

print("\n== DroboApps start/stop: a gated, validated direct write ==")

# Happy path: read the app list (AlphaApp installed), then Stop it.
dev = FakeDrobo([_apps_reply("AlphaApp", "betaapp"), _result(cc.RESULT_ACCEPTED)])
res = da.droboapps_action("127.0.0.1", ESA, "AlphaApp", "Stop", port=dev.port, timeout=5)
check("the action reports accepted", res["result_code"] == cc.RESULT_ACCEPTED, res)
check("the confirmed params are on the wire",
      "<Name>AlphaApp</Name><Action>Stop</Action>" in dev.sent[1], dev.sent[1])
check("it read the app list first, then acted", len(dev.sent) == 2, dev.sent)

# A bad verb is refused before anything is read or sent.
d = FakeDrobo([_apps_reply("AlphaApp"), _result(cc.RESULT_ACCEPTED)])
check("a lowercase 'start' is refused",
      _raises(lambda: da.droboapps_action("127.0.0.1", ESA, "AlphaApp", "start",
                                          port=d.port, timeout=5), da.DroboAppsActionError))
check("...and nothing was sent for a bad verb", len(d.sent) == 0, d.sent)

# An app that is not installed is refused (the read happened, the action did not).
dev = FakeDrobo([_apps_reply("AlphaApp"), _result(cc.RESULT_ACCEPTED)])
try:
    da.droboapps_action("127.0.0.1", ESA, "Nonexistent", "Start", port=dev.port, timeout=5)
    check("an uninstalled app is refused", False)
except da.DroboAppsActionError as exc:
    check("an uninstalled app is refused", "not an installed" in str(exc), str(exc))
check("...and the action was never sent (only the status read)", len(dev.sent) == 1, dev.sent)

# check_installed=False skips the read and sends directly.
dev = FakeDrobo([_result(cc.RESULT_ACCEPTED)])
da.droboapps_action("127.0.0.1", ESA, "AlphaApp", "Start", port=dev.port, timeout=5,
                    check_installed=False)
check("check_installed=False skips the status read", len(dev.sent) == 1, dev.sent)

# A non-accept result raises.
dev = FakeDrobo([_apps_reply("AlphaApp"), _result(0)])
try:
    da.droboapps_action("127.0.0.1", ESA, "AlphaApp", "Stop", port=dev.port, timeout=5)
    check("a non-accept result raises", False)
except da.DroboAppsActionError as exc:
    check("a non-accept result raises DroboAppsActionError", "did not accept" in str(exc), str(exc))

check("eCmdDroboAppsAction is a vetted write, still in DO_NOT_SEND",
      "eCmdDroboAppsAction" in C.VETTED_WRITES and "eCmdDroboAppsAction" in C.DO_NOT_SEND)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
