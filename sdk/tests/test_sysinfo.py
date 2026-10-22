"""
Tests for eCmdGetSysInfo -- the Drobo's own temperature and uptime.

The fixture is the real reply from a live 5N on 2026-07-26, byte for byte
apart from the uptime, which is a synthetic value. The important thing it
captures is the *escaping*: the device puts an entity-escaped XML document
inside <ResultDetails>, not real child elements, and reading it as though it
were plain XML is exactly the mistake that made this command look broken for
weeks.

    py test_sysinfo.py
"""
import html
import os
import sys
import warnings

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import commands  # noqa: E402
from drobo_nasd import config_cmd  # noqa: E402
from drobo_nasd import droboapps  # noqa: E402
from drobo_nasd import sysinfo  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


# The real thing, as it comes off the wire.
REAL_SYSINFO_REPLY = b"""<?xml version="1.0" encoding="UTF-8"?>

<TMCmd>
    <CmdID>61</CmdID>
    <Result>8589934592</Result>
    <ResultDetails>&lt;?xml version=&quot;1.0&quot; encoding=&quot;UTF-8&quot;?&gt;

&lt;SysInfo&gt;
    &lt;UpTime&gt;1209645&lt;/UpTime&gt;
    &lt;Temperature&gt;36&lt;/Temperature&gt;
&lt;/SysInfo&gt;
</ResultDetails>
</TMCmd>\x00"""

print("== the real device reply ==")
sysinfo_reading = sysinfo.parse_sysinfo(REAL_SYSINFO_REPLY)
check("temperature read through the escaping",
      sysinfo_reading["temperature_c"] == 36, sysinfo_reading)
check("uptime read through the escaping", sysinfo_reading["uptime_seconds"] == 1209645, sysinfo_reading)

print("\n== the other shape, in case firmware ever changes its mind ==")
PLAIN_SYSINFO_REPLY = (b"<TMCmd><CmdID>61</CmdID><ResultDetails><SysInfo>"
                       b"<UpTime>42</UpTime><Temperature>31</Temperature>"
                       b"</SysInfo></ResultDetails></TMCmd>\x00")
plain_sysinfo_reading = sysinfo.parse_sysinfo(PLAIN_SYSINFO_REPLY)
check("unescaped children parse too",
      plain_sysinfo_reading == {"temperature_c": 31,
                                "uptime_seconds": 42}, plain_sysinfo_reading)

print("\n== a value we don't believe is not shown at all ==")
for bogus_temperature in (b"0", b"-5", b"250", b"not-a-number"):
    sysinfo_reply = (b"<TMCmd><ResultDetails><SysInfo><Temperature>"
                     + bogus_temperature +
                     b"</Temperature></SysInfo></ResultDetails></TMCmd>\x00")
    reading = sysinfo.parse_sysinfo(sysinfo_reply)
    check(f"temperature {bogus_temperature.decode()!r} is rejected, "
          "not displayed", reading["temperature_c"] is None, reading)

check("a believable low reading IS kept",
      sysinfo.parse_sysinfo(
          b"<TMCmd><ResultDetails><SysInfo><Temperature>1</Temperature>"
          b"</SysInfo></ResultDetails></TMCmd>\x00")["temperature_c"] == 1)
check("a believable high reading IS kept",
      sysinfo.parse_sysinfo(
          b"<TMCmd><ResultDetails><SysInfo><Temperature>95</Temperature>"
          b"</SysInfo></ResultDetails></TMCmd>\x00")["temperature_c"] == 95)

print("\n== missing data is 'unknown', never zero ==")
empty_sysinfo_reply = b"<TMCmd><ResultDetails><SysInfo></SysInfo></ResultDetails></TMCmd>\x00"
check("no fields -> both None, no exception",
      sysinfo.parse_sysinfo(empty_sysinfo_reply) == {"temperature_c": None,
                                                     "uptime_seconds": None},
      sysinfo.parse_sysinfo(empty_sysinfo_reply))
check("empty payload -> both None",
      sysinfo.parse_sysinfo(b"\x00") == {"temperature_c": None,
                                         "uptime_seconds": None})
check("temperature can arrive without uptime",
      sysinfo.parse_sysinfo(
          b"<TMCmd><ResultDetails><SysInfo><Temperature>40</Temperature>"
          b"</SysInfo></ResultDetails></TMCmd>\x00")
      == {"temperature_c": 40, "uptime_seconds": None})
check("empty <ResultDetails> is not an error",
      sysinfo.parse_sysinfo(
          b"<TMCmd><ResultDetails></ResultDetails></TMCmd>\x00")
      == {"temperature_c": None, "uptime_seconds": None})

print("\n== malformed input raises, it doesn't return nonsense ==")
for bad_reply, label in ((b"<not xml\x00", "malformed outer XML"),
                         (b"<TMCmd><CmdID>61</CmdID></TMCmd>\x00", "no ResultDetails"),
                         (b"<TMCmd><ResultDetails>&lt;SysInfo&gt;&lt;/Sys&gt;"
                          b"</ResultDetails></TMCmd>\x00",
                          "malformed inner XML")):
    try:
        sysinfo.parse_sysinfo(bad_reply)
        check(f"{label} raises", False)
    except sysinfo.SysInfoError:
        check(f"{label} raises SysInfoError", True)

print("\n== safety ==")
check("eCmdGetSysInfo has a confirmed id", commands.COMMANDS.get("eCmdGetSysInfo") == 61,
      commands.COMMANDS.get("eCmdGetSysInfo"))
check("eCmdGetSysInfo is not on the do-not-send list",
      not commands.is_dangerous("eCmdGetSysInfo"))
check("SysInfoError is catchable as ConfigError (callers already handle that)",
      issubclass(sysinfo.SysInfoError,
                 config_cmd.ConfigError))


# ---------------------------------------------------------------------------
# eCmdGetPerformance -- added 2026-07-26, after the re-sweep found this command
# answers on a plain login. The first sweep filed it as "empty" only because
# its whole payload arrives as an entity-escaped document, which the tool
# counted as zero fields. Same blind spot that hid temperature.
# ---------------------------------------------------------------------------
print("\n== eCmdGetPerformance ==")

_PERFORMANCE_INNER_XML = ("<Performance><Iops>12</Iops><ReadThroughout>3456</ReadThroughout>"
                          "<WriteThroughout>789</WriteThroughout><TierIOps>4</TierIOps></Performance>")


def _performance_reply(inner_xml: str) -> bytes:
    return (b"<TMCmd><CmdID>62</CmdID><ResultDetails>"
            + html.escape(inner_xml).encode()
            + b"</ResultDetails></TMCmd>\x00")


performance = sysinfo.parse_performance(
    _performance_reply(_PERFORMANCE_INNER_XML))
check("read throughput parsed", performance["read_mb_per_s"] == 3456,
      performance)
check("write throughput parsed", performance["write_mb_per_s"] == 789,
      performance)
check("iops parsed", performance["iops"] == 12, performance)
check("tier iops parsed", performance["tier_iops"] == 4, performance)

# An idle array really does send all zeros. That is a READING, not a gap --
# the driver relies on being able to tell those apart.
idle_performance = sysinfo.parse_performance(_performance_reply(
    "<Performance><Iops>0</Iops><ReadThroughout>0</ReadThroughout>"
    "<WriteThroughout>0</WriteThroughout><TierIOps>0</TierIOps></Performance>"))
check("an idle array's zeros parse as 0, not None",
      idle_performance["iops"] == 0 and idle_performance["read_mb_per_s"] == 0,
      idle_performance)

# The device also sometimes answers with real child elements rather than an
# escaped document; both shapes must work.
plain_performance = sysinfo.parse_performance(
    b"<TMCmd><ResultDetails><Performance><Iops>7</Iops>"
    b"<ReadThroughout>10</ReadThroughout></Performance></ResultDetails></TMCmd>\x00")
check("unescaped shape parses too", plain_performance["iops"] == 7 and plain_performance["read_mb_per_s"] == 10, plain_performance)

partial_performance = sysinfo.parse_performance(_performance_reply("<Performance><Iops>5</Iops></Performance>"))
check("a missing field is None, not 0", partial_performance["write_mb_per_s"] is None, partial_performance)
check("...while the field that IS present still reads", partial_performance["iops"] == 5, partial_performance)

negative_rate_performance = sysinfo.parse_performance(_performance_reply(
    "<Performance><ReadThroughout>-1</ReadThroughout></Performance>"))
check("a negative rate is rejected rather than shown", negative_rate_performance["read_mb_per_s"] is None, negative_rate_performance)

check("empty payload gives all-None rather than raising",
      sysinfo.parse_performance(b"\x00")["iops"] is None)
try:
    sysinfo.parse_performance(b"<not xml\x00")
    check("malformed XML raises", False)
except sysinfo.SysInfoError:
    check("malformed XML raises SysInfoError", True)

check("eCmdGetPerformance is resolved by NAME and is not a write",
      commands.COMMANDS["eCmdGetPerformance"] == 62
      and "eCmdGetPerformance" not in commands.DO_NOT_SEND)

print("\n== eCmdGetDemoModeInfo: is this unit showing fabricated capacity? ==")
# One of the seven commands that answers with real data. Obscure, and worth
# having for exactly one reason: a Drobo left in shop-display mode reports
# capacity it does not have, and every number this project shows would be a
# lie with nothing else to catch it.


def _result_reply(inner_xml: str) -> bytes:
    return (f"<Result><ResultDetails>{inner_xml}</ResultDetails></Result>").encode() + b"\x00"


demo_off_reading = droboapps.parse_demo_mode(_result_reply(
    "&lt;FirmwareInfo&gt;<DemoMode>0</DemoMode><ScaleFactor>1</ScaleFactor>&lt;/FirmwareInfo&gt;"))
check("a normal unit reports demo mode off",
      demo_off_reading["demo_mode"] is False, demo_off_reading)
check("-- with its scale factor", demo_off_reading["scale_factor"] == 1,
      demo_off_reading)

demo_on_reading = droboapps.parse_demo_mode(_result_reply(
    "&lt;FirmwareInfo&gt;<DemoMode>1</DemoMode><ScaleFactor>16</ScaleFactor>&lt;/FirmwareInfo&gt;"))
check("a shop-display unit reports demo mode ON",
      demo_on_reading["demo_mode"] is True, demo_on_reading)
check("-- and the multiplier it invents capacity with", demo_on_reading["scale_factor"] == 16, demo_on_reading)

# "Didn't say" is not "off". Defaulting an absent field to False would quietly
# assert a unit is genuine when we simply have not been told.
for payload in (b"", _result_reply(""), _result_reply("&lt;FirmwareInfo&gt;&lt;/FirmwareInfo&gt;")):
    demo_reading = droboapps.parse_demo_mode(payload)
    check(f"{payload[:24]!r} leaves demo mode UNKNOWN, not False",
          demo_reading["demo_mode"] is None, demo_reading)

unexpected_demo_reading = droboapps.parse_demo_mode(_result_reply(
    "&lt;FirmwareInfo&gt;<DemoMode>yes</DemoMode>&lt;/FirmwareInfo&gt;"))
check("an unexpected value is unknown rather than coerced to true",
      unexpected_demo_reading["demo_mode"] is None, unexpected_demo_reading)
bad_scale = droboapps.parse_demo_mode(_result_reply(
    "&lt;FirmwareInfo&gt;<DemoMode>0</DemoMode><ScaleFactor>lots</ScaleFactor>&lt;/FirmwareInfo&gt;"))
check("a non-numeric scale factor is dropped, not guessed",
      bad_scale["scale_factor"] is None and bad_scale["demo_mode"] is False, bad_scale)

print("\n== an Element's truth value is never relied on ==")
# An Element with no children is falsy, so `find(...) or inner` fell
# back to the parent whenever the node it found was empty, and Python
# 3.12+ warns on every such test. With that warning made an error,
# every shape must still parse, to the same values.
STRICT_CASES = (
    ("unescaped <SysInfo>", sysinfo.parse_sysinfo, PLAIN_SYSINFO_REPLY,
     {"temperature_c": 31, "uptime_seconds": 42}),
    ("an empty <SysInfo> inside a wrapper", sysinfo.parse_sysinfo,
     b"<TMCmd><ResultDetails>&lt;Wrap&gt;&lt;SysInfo/&gt;&lt;/Wrap&gt;"
     b"</ResultDetails></TMCmd>\x00",
     {"temperature_c": None, "uptime_seconds": None}),
    ("unescaped <Performance>", sysinfo.parse_performance,
     b"<TMCmd><ResultDetails><Performance><Iops>7</Iops>"
     b"</Performance></ResultDetails></TMCmd>\x00",
     {"read_mb_per_s": None, "write_mb_per_s": None, "iops": 7,
      "tier_iops": None}),
    ("unescaped <FirmwareInfo>", droboapps.parse_demo_mode,
     _result_reply("<FirmwareInfo><DemoMode>0</DemoMode></FirmwareInfo>"),
     {"demo_mode": False, "scale_factor": None}),
    ("unescaped <DroboApps>", droboapps.parse_droboapps,
     _result_reply(
         "<DroboApps><App><Name>rsync</Name><Stopped>0</Stopped></App>"
         "</DroboApps>"),
     {"sdk_version": "",
      "apps": [{"name": "rsync", "version": "", "description": "",
                "running": True, "status": "", "has_web_ui": False}]}),
)
for label, parse, payload, expected in STRICT_CASES:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        try:
            parse_outcome = parse(payload)
        except DeprecationWarning as exc:
            parse_outcome = exc
    check(f"{label} parses with DeprecationWarning made an error",
          parse_outcome == expected, parse_outcome)

print("\n== eCmdGetDimming: the reading that had no way out ==")
# Parser existed and worked since 2026-07-26; nothing exposed it until
# 2026-07-28. A measurement nobody can see is not a feature.
check("the live device's reading parses", droboapps.parse_dimming(_result_reply("59")) == 59)
check("zero is a real brightness, not 'no reading'", droboapps.parse_dimming(_result_reply("0")) == 0)
check("no reading is None, distinct from zero", droboapps.parse_dimming(_result_reply("")) is None)
check("an empty payload is None", droboapps.parse_dimming(b"") is None)
check("a non-numeric reading is None", droboapps.parse_dimming(_result_reply("bright")) is None)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
