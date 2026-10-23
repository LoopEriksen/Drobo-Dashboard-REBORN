"""
Offline tests for drobosync.py.

The honest situation this suite encodes: every DroboSync reply this
project has ever observed was EMPTY, because only a single unit is
available to this project and the feature needs two. So the tests that
matter most are about behaving correctly when there is nothing to report
-- and about not pretending to know a schema nobody here has seen.

    py test_drobosync.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import commands, drobosync, sysinfo  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label +
          (f"  {failure_detail}" if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


def _result_reply(details_xml: str) -> bytes:
    return (
        f"<Result><ResultDetails>{details_xml}</ResultDetails></Result>"
    ).encode() + b"\x00"


print("\n== the reply we have actually seen: empty ==")
# Swept against the live 5N on 2026-07-26. All four read commands
# answered empty. That is CORRECT for a device with no sync partner, and
# must not be reported as a failure -- a red error for a perfectly
# healthy machine is worse than saying nothing.
for payload in (b"", b"\x00", _result_reply(""), b"   \x00"):
    parsed = drobosync.parse_sync_reply(payload)
    check(f"{payload!r} parses as not-configured",
          parsed["configured"] is False, parsed)
    check(f"{payload!r} raises nothing", isinstance(parsed, dict))

parsed_empty_reply = drobosync.parse_sync_reply(b"")
check("an empty reply carries no invented fields",
      parsed_empty_reply["fields"] == {}, parsed_empty_reply)
check("-- and no raw document", parsed_empty_reply["raw_xml"] == "",
      parsed_empty_reply)

print("\n== a populated reply, whose real shape nobody here has seen ==")
# So the parser stays GENERIC on purpose: it reports the leaf elements
# it found rather than mapping them onto field names we would be
# guessing at. If this ever runs against a real second Drobo, raw_xml is
# what tells us the truth.
populated_details_xml = (
    "&lt;DroboSync&gt;<Enabled>1</Enabled>"
    "<TargetName>Upstairs Drobo</TargetName>"
    "<LastRun>2026-07-20 03:00</LastRun>&lt;/DroboSync&gt;")
populated = drobosync.parse_sync_reply(_result_reply(populated_details_xml))
check("a reply with content is configured",
      populated["configured"] is True, populated)
check(
    "leaf values are carried under their own tags",
    populated["fields"].get("TargetName") == "Upstairs Drobo",
    populated["fields"],
)
check(
    "-- all of them, not a chosen few",
    set(populated["fields"]) >= {"Enabled", "TargetName", "LastRun"},
    populated["fields"],
)

print(
    "\n== escaped nesting is unwrapped, as everywhere else in this firmware =="
)
# Reading these as opaque strings is the mistake that made temperature
# and the performance counters look unobtainable for weeks.
nested = drobosync.parse_sync_reply(
    _result_reply(
        "&lt;DroboSync&gt;&lt;Enabled&gt;1&lt;/Enabled&gt;&lt;/DroboSync&gt;"
    )
)
check("a fully-escaped document is parsed, not treated as text",
      nested["fields"].get("Enabled") == "1", nested)
check("-- and the raw document is kept for whoever sees the first real one",
      "DroboSync" in nested["raw_xml"], nested["raw_xml"])

print("\n== malformed input is refused, not guessed at ==")
try:
    drobosync.parse_sync_reply(b"<Result><broken")
    check("malformed XML raises", False)
except drobosync.DroboSyncError:
    check("malformed XML raises DroboSyncError", True)
check("DroboSyncError is catchable as SysInfoError, so existing handlers work",
      issubclass(drobosync.DroboSyncError, sysinfo.SysInfoError))

print("\n== an unknown read is rejected before any socket is opened ==")
try:
    drobosync.read("10.0.0.50", "drbTEST", read_kind="nonsense")
    check("an unknown read raises", False)
except ValueError as exc:
    check("an unknown read raises ValueError naming the options",
          "settings" in str(exc), str(exc))

print("\n== the four reads we support are the four the firmware has ==")
check("exactly four read commands", len(
    drobosync.READ_COMMANDS) == 4, drobosync.READ_COMMANDS)
for name in drobosync.READ_COMMANDS.values():
    check(f"{name} has a firmware id", name in commands.COMMANDS, name)
    check(f"{name} is NOT refused (it is a query)",
          not commands.is_dangerous(name), name)

print("\n== the writes stay refused, permanently ==")
# Triggering a replication run moves real data between two arrays. It is
# not a safe write, it cannot be tested with one Drobo, and it is not
# going in.
for name in drobosync.WRITE_COMMANDS:
    check(f"{name} is in DO_NOT_SEND", commands.is_dangerous(name), name)
    check(f"{name} is not a safe write",
          not commands.is_safe_write(name), name)
check("no DroboSync write ever became a SAFE_WRITE",
      not (set(drobosync.WRITE_COMMANDS) & commands.SAFE_WRITES))

print("\n== summary(): what a front-end renders when there is one Drobo ==")
_original_read = drobosync.read
_reads_asked: list[str] = []


def _unconfigured_read(host, esa_id, what, port=None, timeout=None):
    _reads_asked.append(what)
    return {"configured": False, "raw_xml": "", "fields": {}, "what": what}


drobosync.read = _unconfigured_read
try:
    sync_summary = drobosync.summary("10.0.0.50", "drbTEST")
    check("summary reports not configured",
          sync_summary["configured"] is False, sync_summary)
    check("all four reads are represented in the answer",
          set(sync_summary["reads"]) == set(drobosync.READ_COMMANDS),
          sync_summary["reads"])

    # Each read costs its own connection, and this device's command port
    # stops answering after roughly ninety connections in an hour.
    # Spending four on a switched-off feature every time a page
    # refreshes would be actively harmful to a box that tires easily --
    # and with no partner configured there are no logs to fetch by
    # definition.
    check("only settings is actually asked when sync is off",
          _reads_asked == ["settings"], _reads_asked)
    check("the three skipped reads say they were not asked",
          all(sync_summary["reads"][read_name]["asked"] is False
              for read_name in ("summary_log", "detailed_log", "log")),
          sync_summary["reads"])
    check("-- while settings records that it WAS asked",
          sync_summary["reads"]["settings"]["asked"] is True,
          sync_summary["reads"]["settings"])

    # The note is the whole point of shipping this dormant: explain,
    # don't fail.
    check("it explains WHY rather than showing four empty panels",
          "needs two" in sync_summary["note"], sync_summary["note"])
    check("it says the work is already done and waiting",
          "built and waiting" in sync_summary["note"], sync_summary["note"])
    check("it does not read as an error",
          "error" not in sync_summary["note"].lower(), sync_summary["note"])
finally:
    drobosync.read = _original_read

print("\n== when a partner IS configured, the logs are fetched ==")
_reads_asked.clear()


def _configured_read(host, esa_id, what, port=None, timeout=None):
    _reads_asked.append(what)
    if what == "settings":
        return {"configured": True, "raw_xml": "<DroboSync/>",
                "fields": {"Enabled": "1"}, "what": what}
    return {"configured": False, "raw_xml": "", "fields": {}, "what": what}


drobosync.read = _configured_read
try:
    sync_summary = drobosync.summary("10.0.0.50", "drbTEST")
    check("a configured device reports configured",
          sync_summary["configured"] is True, sync_summary)
    check("-- and the logs ARE asked for", sorted(_reads_asked) ==
          ["detailed_log", "log", "settings", "summary_log"], _reads_asked)
    check("-- and the explanatory note is dropped, since it no longer applies",
          sync_summary["note"] == "", sync_summary["note"])
finally:
    drobosync.read = _original_read

print("\n== a read that fails is contained, not fatal to the others ==")


def _read_with_failing_settings(host, esa_id, what, port=None, timeout=None):
    if what == "settings":
        raise drobosync.DroboSyncError("simulated: device stopped answering")
    return {"configured": False, "raw_xml": "", "fields": {}, "what": what}


drobosync.read = _read_with_failing_settings
try:
    sync_summary = drobosync.summary("10.0.0.50", "drbTEST")
    check("a failing read still returns all four entries", len(
        sync_summary["reads"]) == 4, sync_summary["reads"])
    check("the failure is recorded against the read it belongs to",
          "error" in sync_summary["reads"]["settings"],
          sync_summary["reads"]["settings"])
    check("a failure is not mistaken for 'configured'",
          sync_summary["configured"] is False, sync_summary)
finally:
    drobosync.read = _original_read

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
