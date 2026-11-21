using System.Text.Json;
using DroboDashboardReborn;

// Tests for AlertNotifier -- when this app is allowed to interrupt somebody.
//
// The maintainer reported twice that this app was intrusive, and the rules here are
// the whole of the promise made when notifications were allowed back in:
// alerts only, each type switchable, everything off until switched on, and
// never a second time for something you already know about.
//
//     dotnet run --project windows/DroboDashboardReborn.Tests

var fails = new List<string>();

void Check(string label, bool cond, object? extra = null)
{
    Console.WriteLine((cond ? "  PASS  " : "  FAIL  ") + label
        + (!cond && extra is not null ? "  " + extra : ""));
    if (!cond) fails.Add(label);
}

static (string, string, string) Alert(string key, string sev = "critical", string msg = "something happened")
    => (key, sev, msg);

static Dictionary<string, bool> AllOn()
{
    var d = AlertNotifier.DefaultEnabled();
    foreach (var k in d.Keys.ToList()) d[k] = true;
    return d;
}

// ---------------------------------------------------------------------------
Console.WriteLine("\n== a fresh install is silent ==");
var defaults = AlertNotifier.DefaultEnabled();
Check("every category defaults to off", defaults.Values.All(v => !v),
      string.Join(",", defaults.Where(kv => kv.Value).Select(kv => kv.Key)));
Check("every listed category has a default", AlertNotifier.Categories.All(c => defaults.ContainsKey(c.Id)));
Check("categories all have a label and a description",
      AlertNotifier.Categories.All(c => c.Label.Length > 0 && c.Description.Length > 0));

// ---------------------------------------------------------------------------
Console.WriteLine("\n== RULE 2: opening the app never announces what you came to look at ==");
// Three standing alerts must produce zero notifications. You are already
// looking at the screen that lists them.
var n = new AlertNotifier();
var first = n.Evaluate(
    new[] { Alert("drive-failed-3"), Alert("capacity-critical"), Alert("unreachable") },
    masterEnabled: true, enabled: AllOn());
Check("first pass notifies about nothing", first.Count == 0, first.Count);

// ...but it did LEARN them, so they stay quiet next time too.
var second = n.Evaluate(
    new[] { Alert("drive-failed-3"), Alert("capacity-critical"), Alert("unreachable") },
    masterEnabled: true, enabled: AllOn());
Check("-- and they stay quiet on the next pass", second.Count == 0, second.Count);

// ---------------------------------------------------------------------------
Console.WriteLine("\n== a NEW alert after that does notify ==");
var third = n.Evaluate(
    new[] { Alert("drive-failed-3"), Alert("capacity-critical"), Alert("unreachable"),
            Alert("battery-failed") },
    masterEnabled: true, enabled: AllOn());
Check("exactly the new alert notifies", third.Count == 1, third.Count);
Check("-- and it is the right one", third.Count == 1 && third[0].Key == "battery-failed", third.FirstOrDefault());
Check("-- carrying its category", third.Count == 1 && third[0].Category == "battery", third.FirstOrDefault());
Check("-- and the message the agent wrote",
      third.Count == 1 && third[0].Message == "something happened", third.FirstOrDefault());

// ---------------------------------------------------------------------------
Console.WriteLine("\n== RULE 3: never twice for the same standing problem ==");
// The agent re-sends long-running alerts on its own timer so they are not
// forgotten. That is right for a phone push and wrong for a desktop toast
// every hour about a drive you already know is dead.
var repeat = n.Evaluate(
    new[] { Alert("drive-failed-3"), Alert("battery-failed") },
    masterEnabled: true, enabled: AllOn());
Check("a still-active alert does not notify again", repeat.Count == 0, repeat.Count);

// ---------------------------------------------------------------------------
Console.WriteLine("\n== RULE 4: but again if it clears and comes back ==");
n.Evaluate(new[] { Alert("drive-failed-3") }, true, AllOn());          // battery cleared
var returned = n.Evaluate(
    new[] { Alert("drive-failed-3"), Alert("battery-failed") }, true, AllOn());
Check("an alert that returned is new information", returned.Count == 1, returned.Count);
Check("-- and it is the one that came back",
      returned.Count == 1 && returned[0].Key == "battery-failed", returned.FirstOrDefault());

// ---------------------------------------------------------------------------
Console.WriteLine("\n== RULE 1: the switches actually switch ==");
var m = new AlertNotifier();
m.Evaluate(Array.Empty<(string, string, string)>(), true, AllOn());     // seed

var masterOff = m.Evaluate(new[] { Alert("drive-failed-1") }, masterEnabled: false, enabled: AllOn());
Check("the master switch off silences everything", masterOff.Count == 0, masterOff.Count);

var onlyDrives = AlertNotifier.DefaultEnabled();
onlyDrives["drives"] = true;
var m2 = new AlertNotifier();
m2.Evaluate(Array.Empty<(string, string, string)>(), true, onlyDrives);
var filtered = m2.Evaluate(
    new[] { Alert("drive-failed-1"), Alert("capacity-critical"), Alert("battery-failed") },
    masterEnabled: true, enabled: onlyDrives);
Check("only the enabled category notifies", filtered.Count == 1, filtered.Count);
Check("-- and it is the enabled one",
      filtered.Count == 1 && filtered[0].Category == "drives", filtered.FirstOrDefault());

var m3 = new AlertNotifier();
m3.Evaluate(Array.Empty<(string, string, string)>(), true, AlertNotifier.DefaultEnabled());
var allOff = m3.Evaluate(new[] { Alert("drive-failed-1"), Alert("unreachable") },
                         true, AlertNotifier.DefaultEnabled());
Check("defaults (all off) notify about nothing", allOff.Count == 0, allOff.Count);

// ---------------------------------------------------------------------------
Console.WriteLine("\n== every alert key the agent raises maps to a category ==");
// Taken from monitor.py's _evaluate_alerts. A key that maps nowhere is silent, which
// is the safe direction to be wrong -- but these are the ones we know about
// and they should all land somewhere.
var known = new (string Key, string Expected)[]
{
    ("drive-failed-3", "drives"),
    ("drive-warning-2", "drives"),
    ("drive-errors-1", "drives"),
    ("temp-critical-4", "temperature"),
    ("temp-warning-4", "temperature"),
    ("chassis-temp-critical", "temperature"),
    ("chassis-temp-warning", "temperature"),
    ("capacity-critical", "capacity"),
    ("capacity-warning", "capacity"),
    ("battery-failed", "battery"),
    ("battery-warning", "battery"),
    ("unreachable", "unreachable"),
    ("double-degraded", "pack"),
    ("relayout", "pack"),
    ("pack-status-dnas_status", "pack"),
    ("power-failed", "hardware"),
    ("power-warning", "hardware"),
    ("fan-failed", "hardware"),
    ("fan-warning", "hardware"),
};
foreach (var (key, expected) in known)
    Check($"{key} -> {expected}", AlertNotifier.CategoryFor(key) == expected, AlertNotifier.CategoryFor(key));

Console.WriteLine("\n== an unknown key is silent, not a surprise ==");
// If a future alert type is added to the agent and nobody updates the list,
// the failure mode must be a missing notification rather than an unexpected
// one -- given what this app has already been told off for.
Check("an unrecognised key has no category", AlertNotifier.CategoryFor("brand-new-thing") == "");
Check("an empty key has no category", AlertNotifier.CategoryFor("") == "");
var u = new AlertNotifier();
u.Evaluate(Array.Empty<(string, string, string)>(), true, AllOn());
var unknown = u.Evaluate(new[] { Alert("brand-new-thing") }, true, AllOn());
Check("-- and it notifies about nothing even with everything switched on",
      unknown.Count == 0, unknown.Count);

// A "-cleared" key is the agent's RESOLUTION notice, carrying text like
// "Resolved: Drive in bay 3 has failed". Matched on prefix alone it would land
// in "drives" and pop up an alarm about a drive you have just replaced. It
// never reaches here today (monitor.py doesn't list cleared alerts as active),
// but that must stay impossible by rule rather than by luck.
Check("a resolution notice maps to no category at all",
      AlertNotifier.CategoryFor("drive-failed-3-cleared") == "",
      AlertNotifier.CategoryFor("drive-failed-3-cleared"));
var c = new AlertNotifier();
c.Evaluate(Array.Empty<(string, string, string)>(), true, AllOn());
var cleared = c.Evaluate(new[] { Alert("drive-failed-3-cleared", "info", "Resolved: ...") },
                         true, AllOn());
Check("-- so a resolved problem can never raise an alarm", cleared.Count == 0, cleared.Count);

// ---------------------------------------------------------------------------
Console.WriteLine("\n== switching Drobos starts over ==");
// The alerts of the device you just left say nothing about the one you just
// opened. Carrying them across would either suppress a real notification or
// invent one.
var s = new AlertNotifier();
s.Evaluate(new[] { Alert("drive-failed-3") }, true, AllOn());          // seeded, silent
s.Reset();
var afterReset = s.Evaluate(new[] { Alert("drive-failed-3") }, true, AllOn());
Check("the first pass after a reset is silent again", afterReset.Count == 0, afterReset.Count);
var thenNew = s.Evaluate(new[] { Alert("drive-failed-3"), Alert("unreachable") }, true, AllOn());
Check("-- and normal behaviour resumes after that", thenNew.Count == 1, thenNew.Count);

// ---------------------------------------------------------------------------
Console.WriteLine("\n== empty and malformed input ==");
var e = new AlertNotifier();
e.Evaluate(Array.Empty<(string, string, string)>(), true, AllOn());
var none = e.Evaluate(Array.Empty<(string, string, string)>(), true, AllOn());
Check("no alerts means no notices", none.Count == 0, none.Count);
var blank = e.Evaluate(new[] { Alert("") }, true, AllOn());
Check("a blank key is ignored", blank.Count == 0, blank.Count);
var missingCat = e.Evaluate(new[] { Alert("drive-failed-9") }, true,
                            new Dictionary<string, bool>());  // category absent entirely
Check("a category missing from settings counts as off", missingCat.Count == 0, missingCat.Count);


// ---------------------------------------------------------------------------
// Protection -- a figure a real Drobo 5N never reports.
//
// The agent sends protection_bytes 0 with protection_reported false for the
// real device. Until 2026-11-20 the Status tab and the diagnostics printed
// that 0 as "0 B" beside real figures, which reads as "my data is
// unprotected". Kept before the CredentialStore section so these run on any
// machine, not only on Windows.
Console.WriteLine("\n== Protection: an unreported figure says so ==");
string ProtectionOf(string capacityJson) =>
    Attempt(() => JsonHelpers.FormatProtection(ParseJson(capacityJson))) as string ?? "(not a string)";
var unreportedProtection = ProtectionOf("{\"protection_bytes\":0,\"protection_reported\":false}");
Check("an unreported 0 reads not reported by this Drobo",
      unreportedProtection == "not reported by this Drobo", unreportedProtection);
var reportedProtection = ProtectionOf("{\"protection_bytes\":4000787030016,\"protection_reported\":true}");
Check("a reported figure reads as bytes", reportedProtection == "4.00 TB", reportedProtection);
var reportedZero = ProtectionOf("{\"protection_bytes\":0,\"protection_reported\":true}");
Check("a reported 0 is a real 0 B", reportedZero == "0 B", reportedZero);
var olderAgentZero = ProtectionOf("{\"protection_bytes\":0}");
Check("an agent older than the flag: a 0 reads not reported",
      olderAgentZero == "not reported by this Drobo", olderAgentZero);
var olderAgentFigure = ProtectionOf("{\"protection_bytes\":2000000000000}");
Check("-- and a figure above 0 still reads as bytes", olderAgentFigure == "2.00 TB", olderAgentFigure);
var nullCapacityProtection = ProtectionOf("null");
Check("a null capacity does not throw", nullCapacityProtection == "not reported by this Drobo",
      nullCapacityProtection);

static string ProtectionLine(string report) =>
    report.Split('\n').FirstOrDefault(line => line.TrimStart().StartsWith("Protection"))?.Trim()
    ?? "(no Protection line)";
var diagnosticsProtection = ProtectionLine(DiagnosticsReport.BuildText(
    "{\"reachable\":true,\"device\":{\"name\":\"n\"},"
    + "\"capacity\":{\"used_bytes\":1,\"protection_bytes\":0,\"protection_reported\":false}}"));
Check("the diagnostics say not reported by this Drobo, not 0 B",
      diagnosticsProtection.EndsWith("not reported by this Drobo"), diagnosticsProtection);

// ---------------------------------------------------------------------------
// DroboSelection -- what a picker row's Connect asks the agent for. Until
// 2026-11-20 Connect asked nothing, and opened whichever Drobo the agent
// already watched (readiness M2, S8). Placed before the CredentialStore
// checks, which need Windows, so these run on any machine.
Console.WriteLine("\n== DroboSelection: the picker asks the agent for the row clicked ==");
var rowBody = DroboSelection.RequestBody(" 192.0.2.22 ", "drb000000SAMPLEB");
Check("a row with a serial sends its address and serial",
      rowBody.Count == 2 && rowBody["host"] == "192.0.2.22" && rowBody["esa_id"] == "drb000000SAMPLEB",
      string.Join(",", rowBody.Select(kv => kv.Key + "=" + kv.Value)));
var typedBody = DroboSelection.RequestBody("192.0.2.21", "");
Check("-- a row with no serial (a typed address that did not answer) sends the address alone",
      typedBody.Count == 1 && typedBody["host"] == "192.0.2.21");
var reconnectBody = DroboSelection.RequestBody("192.0.2.22", "drb000000SAMPLEB", chosenByPerson: false);
Check("-- the start-up reconnect sends the address alone, so it cannot re-pair the agent without a click",
      reconnectBody.Count == 1 && reconnectBody["host"] == "192.0.2.22");

static JsonElement AgentAnswer(string json) => JsonDocument.Parse(json).RootElement.Clone();
var (selectedOutcome, selectedProblem) = DroboSelection.ReadAnswer(200, AgentAnswer("{\"ok\": true}"));
Check("a 200 opens the dashboard", selectedOutcome == DroboSelection.Outcome.Selected && selectedProblem is null);
const string agentRefusal = "The Drobo at 192.0.2.22 identifies as 'drb000000SAMPLEB', not 'drb000000SAMPLEA'.";
var (refusedOutcome, refusedProblem) = DroboSelection.ReadAnswer(
    409, AgentAnswer("{\"error\": \"" + agentRefusal + "\", \"reason\": \"paired_with_another\"}"));
Check("a refusal does not open it, and shows the agent's own sentence",
      refusedOutcome == DroboSelection.Outcome.Refused && refusedProblem == agentRefusal, refusedProblem);
var (_, reconnectProblem) = DroboSelection.ReadAnswer(
    409, AgentAnswer("{\"error\": \"" + agentRefusal + "\", \"reason\": \"paired_with_another\"}"),
    chosenByPerson: false);
Check("-- and a start-up reconnect refused that way also says which click switches",
      reconnectProblem == agentRefusal + " " + DroboSelection.ClickToSwitch, reconnectProblem);
var (bareOutcome, bareProblem) = DroboSelection.ReadAnswer(500, AgentAnswer("{}"));
Check("-- a refusal with no sentence still says something, with the HTTP code",
      bareOutcome == DroboSelection.Outcome.Refused && bareProblem is not null && bareProblem.Contains("500"), bareProblem);
var (tokenOutcome, tokenProblem) = DroboSelection.ReadAnswer(401, AgentAnswer("{\"error\": \"unauthorised\"}"));
Check("-- a refused token says so in the picker's words",
      tokenOutcome == DroboSelection.Outcome.Refused && tokenProblem is not null && tokenProblem.Contains("token"), tokenProblem);
var (oldAgentOutcome, _) = DroboSelection.ReadAnswer(
    404, AgentAnswer("{\"error\": \"not found\", \"path\": \"/api/drobo/select\"}"));
Check("an agent without the route (older than the app) still opens as before",
      oldAgentOutcome == DroboSelection.Outcome.AgentWithoutRoute);
var (otherNotFound, _) = DroboSelection.ReadAnswer(404, AgentAnswer("{\"error\": \"not found\", \"path\": \"/elsewhere\"}"));
Check("-- but a 404 about some other path is a refusal, not that",
      otherNotFound == DroboSelection.Outcome.Refused);

// Every change names the Drobo it was made against. Until 2026-11-21 none
// did, so once the agent had been switched to another Drobo, a share
// deleted from a list still showing the first went to the second's share of
// the same name. The agent now refuses a change whose serial is not the
// Drobo it watches. Also before the CredentialStore section.
Console.WriteLine("\n== DroboSelection: every change names the Drobo it was made against ==");
var deleteChange = AgentAnswer(DroboSelection.ChangeBody(
    new { name = "Public", confirm_name = "Public" }, " drb000000SAMPLEA "));
Check("a change carries the serial it was made against, beside its own fields",
      JsonHelpers.GetStringOr(deleteChange, "esa_id", "") == "drb000000SAMPLEA"
      && JsonHelpers.GetStringOr(deleteChange, "name", "") == "Public"
      && JsonHelpers.GetStringOr(deleteChange, "confirm_name", "") == "Public", deleteChange);
var unnamedChange = DroboSelection.ChangeBody(new { value = 40 }, "");
Check("-- and no serial when none is known, so the agent takes it as it did before",
      unnamedChange == "{\"value\":40}", unnamedChange);
var identifyChange = DroboSelection.ChangeBody(new { }, "drb000000SAMPLEB");
Check("-- Identify's body is the serial alone",
      identifyChange == "{\"esa_id\":\"drb000000SAMPLEB\"}", identifyChange);
Check("a reading of a second, different Drobo is another Drobo",
      DroboSelection.IsAnotherDrobo("drb000000SAMPLEA", "drb000000SAMPLEB"));
Check("-- but not the first serial seen, a reading with none, or the same Drobo again",
      !DroboSelection.IsAnotherDrobo("", "drb000000SAMPLEA")
      && !DroboSelection.IsAnotherDrobo("drb000000SAMPLEA", "")
      && !DroboSelection.IsAnotherDrobo("drb000000SAMPLEA", null)
      && !DroboSelection.IsAnotherDrobo("drb000000SAMPLEA", "drb000000SAMPLEA"));

// ---------------------------------------------------------------------------
// TesterNotes -- the words that say which writes are not yet proven.
//
// Labels, not gates (decided 2026-11-20). What matters is that every place
// a write is offered carries its sentence, and that the Identify card stops
// promising a blink nobody has seen. Placed before the CredentialStore
// section so it runs on any machine, not only on Windows.
Console.WriteLine("\n== TesterNotes: unproven writes are labelled ==");
Check("the Identify card no longer promises blinking",
      !TesterNotes.IdentifyCardText.Contains("should be blinking")
      && !TesterNotes.IdentifyCardText.StartsWith("Blinks"),
      TesterNotes.IdentifyCardText);
Check("-- it says what was seen and asks for the tester's result",
      TesterNotes.IdentifyCardText.Contains(TesterNotes.IdentifyNotSeen));
foreach (var (place, note, sentence) in new[]
{
    ("new-share dialog", TesterNotes.ShareCreateNote, TesterNotes.ShareCreateUnproven),
    ("delete dialog", TesterNotes.ShareDeleteNote, TesterNotes.ShareDeleteUnproven),
})
{
    Check($"the {place} names what is unproven and warns about user accounts",
          note.StartsWith(sentence) && note.EndsWith(TesterNotes.ShareWritesWithUserAccounts), note);
}
// The share-access sentence folds the user-accounts warning into itself,
// so the card's note is that sentence alone, not one with the warning
// appended a second time.
Check("the share-access card names what is unproven and warns about user accounts",
      TesterNotes.ShareAccessNote == TesterNotes.ShareAccessUnproven
      && TesterNotes.ShareAccessNote.Contains("user accounts")
      && !TesterNotes.ShareAccessNote.EndsWith(TesterNotes.ShareWritesWithUserAccounts),
      TesterNotes.ShareAccessNote);
Check("the DroboApps note says Start and Stop are untried on a real Drobo",
      TesterNotes.DroboAppsActionUnproven.Contains("real Drobo"));


// ---------------------------------------------------------------------------
// DiagnosticsMasking -- the copy of the diagnostics that is safe to post.
//
// Since 2026-11-20 "Copy diagnostics" gives the public copy by default. Until
// then the clipboard carried the raw JSON and the saved file every identifying
// value twice, and testers were asked to remove them by hand. Every value
// below is synthetic, in the shape the real one has; addresses are from the
// documentation ranges. These run before the CredentialStore section, so they
// run on any machine.
Console.WriteLine("\n== DiagnosticsMasking: identifying values become placeholders ==");
const string sampleStatusJson = """
{
  "reachable": false,
  "source": "drobo5n",
  "overall_state": "ok",
  "device": {"name": "Bench5N", "model": "Drobo 5N", "serial": "TST5N0000001",
             "firmware": "4.2.2", "ip": "192.0.2.10", "uptime_seconds": 7200},
  "capacity": {"used_bytes": 1500000, "free_bytes": 2500000},
  "drives": [
    {"bay": 1, "present": true, "state": "ok", "model": "TESTDISK 4TB", "serial": "TSTDISK0001"},
    {"bay": 2, "present": true, "state": "ok", "model": "TESTDISK 4TB", "serial": "TSTDISK0002"},
    {"bay": 3, "present": true, "state": "ok", "model": "TESTDISK 4TB", "serial": "TSTDISK0001"},
    {"bay": 4, "present": false, "state": "empty", "model": "", "serial": ""}
  ],
  "volumes": [{"lun": 0, "name": "Holiday Photos"}, {"lun": 1, "name": ""}],
  "event_log": [
    {"ts_iso": "2026-11-20T20:42:23", "severity": "info", "message": "Share Holiday Photos opened by tester"},
    {"ts_iso": "", "severity": "info", "message": "Share Holiday Photos opened by tester"}
  ],
  "error": "connect to 192.0.2.10:5001 timed out at 20:42:23; see C:\\Users\\username\\AppData\\Local\\x.log",
  "network_hint": {
    "target": "192.0.2.10",
    "addresses": ["198.51.100.7", "fe80::1c2b:3d4e:5f60:7182%12"],
    "networks": ["198.51.100.0/24"],
    "headline": "The Drobo (192.0.2.10) is not on this PC's network",
    "detail": "It is on 192.0.2.10; this PC is on 198.51.100.0/24 (02:00:5E:10:00:01, bench5n.local).",
    "caveat": "Mail tester@example.com or open \\\\192.0.2.10\\Holiday Photos to check TSTDISK0002."
  }
}
""";
var maskedText = Attempt(() => DiagnosticsMasking.MaskJson(sampleStatusJson)) as string ?? "";
var syntheticRealValues = new[]
{
    "Bench5N", "TST5N0000001", "192.0.2.10", "TSTDISK0001", "TSTDISK0002", "Holiday Photos",
    "opened by tester", "198.51.100", "fe80::", "02:00:5E", "bench5n", "username", "tester@example.com",
};
foreach (var realValue in syntheticRealValues)
    Check($"the masked copy does not contain {realValue}",
          !maskedText.Contains(realValue, StringComparison.OrdinalIgnoreCase), maskedText);

var maskedRoot = Attempt(() => ParseJson(maskedText)) is JsonElement parsedMasked ? parsedMasked : default;
static string Text(JsonElement element, params object[] path)
{
    foreach (var step in path)
    {
        if (element.ValueKind == JsonValueKind.Object && step is string key && element.TryGetProperty(key, out var member))
            element = member;
        else if (element.ValueKind == JsonValueKind.Array && step is int index && index < element.GetArrayLength())
            element = element[index];
        else
            return "(missing)";
    }
    return element.ValueKind == JsonValueKind.String ? element.GetString() ?? "" : element.ToString();
}
Check("the masked copy is still JSON of the same shape",
      maskedRoot.ValueKind == JsonValueKind.Object && maskedRoot.GetProperty("drives").GetArrayLength() == 4,
      maskedText);
Check("the Drobo's serial is SERIAL-1", Text(maskedRoot, "device", "serial") == "SERIAL-1",
      Text(maskedRoot, "device", "serial"));
Check("its name is NAME-1", Text(maskedRoot, "device", "name") == "NAME-1", Text(maskedRoot, "device", "name"));
Check("its address is ADDRESS-1", Text(maskedRoot, "device", "ip") == "ADDRESS-1", Text(maskedRoot, "device", "ip"));
Check("the drive serials are DRIVE-SERIAL-1 and -2",
      Text(maskedRoot, "drives", 0, "serial") == "DRIVE-SERIAL-1" && Text(maskedRoot, "drives", 1, "serial") == "DRIVE-SERIAL-2",
      Text(maskedRoot, "drives", 1, "serial"));
Check("-- and the same serial in another bay gets the same placeholder",
      Text(maskedRoot, "drives", 2, "serial") == "DRIVE-SERIAL-1", Text(maskedRoot, "drives", 2, "serial"));
Check("an empty bay's empty serial stays empty", Text(maskedRoot, "drives", 3, "serial") == "",
      Text(maskedRoot, "drives", 3, "serial"));
Check("the volume name is VOLUME-1, an empty one stays empty",
      Text(maskedRoot, "volumes", 0, "name") == "VOLUME-1" && Text(maskedRoot, "volumes", 1, "name") == "",
      Text(maskedRoot, "volumes", 0, "name"));
Check("event messages are EVENT-1, the same message twice the same placeholder",
      Text(maskedRoot, "event_log", 0, "message") == "EVENT-1" && Text(maskedRoot, "event_log", 1, "message") == "EVENT-1",
      Text(maskedRoot, "event_log", 1, "message"));
Check("the hint's target is the device's own ADDRESS-1", Text(maskedRoot, "network_hint", "target") == "ADDRESS-1",
      Text(maskedRoot, "network_hint", "target"));
Check("this PC's addresses and network get placeholders of their own",
      Text(maskedRoot, "network_hint", "addresses", 0) == "ADDRESS-2"
      && Text(maskedRoot, "network_hint", "addresses", 1) == "ADDRESS-3"
      && Text(maskedRoot, "network_hint", "networks", 0) == "NETWORK-1",
      Text(maskedRoot, "network_hint", "addresses", 1));
var maskedDetail = Text(maskedRoot, "network_hint", "detail");
Check("inside a sentence, a known address reads as its field's placeholder",
      maskedDetail.Contains("on ADDRESS-1;") && maskedDetail.Contains("NETWORK-1"), maskedDetail);
Check("-- a MAC address and a .local name are masked too",
      maskedDetail.Contains("MAC-1") && maskedDetail.Contains("NAME-2"), maskedDetail);
var maskedCaveat = Text(maskedRoot, "network_hint", "caveat");
Check("a UNC path keeps the placeholders of the address and volume it names",
      maskedCaveat.Contains(@"\\ADDRESS-1\VOLUME-1"), maskedCaveat);
var uncAlone = DiagnosticsMasking.ScrubText(@"open \\192.0.2.20\Backups\2026 and \\nas-two\Music",
                                            new DiagnosticsMasking.PlaceholderMap());
Check("-- and a server and share it has not seen become ADDRESS, NAME and SHARE",
      uncAlone == @"open \\ADDRESS-1\SHARE-1\2026 and \\NAME-1\SHARE-2", uncAlone);
Check("-- an email and a drive serial quoted in text are masked",
      maskedCaveat.Contains("EMAIL-1") && maskedCaveat.Contains("DRIVE-SERIAL-2"), maskedCaveat);
var maskedError = Text(maskedRoot, "error");
Check("the error keeps its sense: address, port, time and the rest of the profile path",
      maskedError.Contains("ADDRESS-1:5001") && maskedError.Contains("20:42:23")
      && maskedError.Contains(@"\USER-1\AppData\Local\x.log"), maskedError);
// A local account's profile folder can hold a space. Drive D: keeps the
// synthetic path out of the privacy guard's C:-only pattern.
var spacedProfile = DiagnosticsMasking.ScrubText(
    @"Could not write D:\Users\Sample Tester\AppData\Local\x.json",
    new DiagnosticsMasking.PlaceholderMap());
Check("a user name with a space is masked whole, the second word included",
      !spacedProfile.Contains("Tester") && !spacedProfile.Contains("Sample")
      && spacedProfile.EndsWith(@"\USER-1\AppData\Local\x.json"), spacedProfile);
var cutProfile = DiagnosticsMasking.ScrubText(
    @"Could not open D:\Users\sampletester and gave up",
    new DiagnosticsMasking.PlaceholderMap());
Check("-- and a path with nothing after the name still stops at the space",
      cutProfile.EndsWith(@"\USER-1 and gave up") && !cutProfile.Contains("sampletester"), cutProfile);
Check("firmware, model, state and times are left as they are",
      Text(maskedRoot, "device", "firmware") == "4.2.2" && Text(maskedRoot, "device", "model") == "Drobo 5N"
      && Text(maskedRoot, "overall_state") == "ok" && Text(maskedRoot, "event_log", 0, "ts_iso") == "2026-11-20T20:42:23",
      maskedText);
Check("numbers are left as they are", Text(maskedRoot, "capacity", "used_bytes") == "1500000",
      Text(maskedRoot, "capacity", "used_bytes"));

Console.WriteLine("\n== DiagnosticsMasking: a Drobo still called \"Drobo\" ==");
var defaultNamed = Attempt(() => ParseJson(DiagnosticsMasking.MaskJson(
    "{\"device\":{\"name\":\"Drobo\"},\"error\":\"the Drobo did not answer\"}"))) is JsonElement defaultNamedRoot
    ? defaultNamedRoot : default;
Check("the name field is still masked", Text(defaultNamed, "device", "name") == "NAME-1", Text(defaultNamed, "device", "name"));
Check("-- but \"the Drobo\" in a sentence is not turned into NAME-1",
      Text(defaultNamed, "error") == "the Drobo did not answer", Text(defaultNamed, "error"));

Console.WriteLine("\n== DiagnosticsMasking: another Drobo answering at the address ==");
// The exact words of discovery.IdentityMismatchError, which monitor.poll_once
// stores as the error with an empty device block: neither serial is a known
// value then, so they are caught by where they stand in the sentence.
const string mismatchError = "refusing 192.0.2.10:5000 -- it identifies as esa_id='TSTOTHER0002' "
    + "('OtherBox'), not the expected 'TST5N0000001'. This is not your Drobo; not connecting.";
var mismatchJson = "{\"reachable\": false, \"device\": {\"serial\": \"\", \"name\": \"\", \"ip\": \"\"}, "
    + "\"error\": " + JsonSerializer.Serialize(mismatchError) + "}";
var mismatchMasked = Attempt(() => DiagnosticsMasking.MaskJson(mismatchJson)) as string ?? "";
foreach (var realValue in new[] { "TSTOTHER0002", "OtherBox", "TST5N0000001", "192.0.2.10" })
    Check($"an identity mismatch's error does not keep {realValue}", mismatchMasked.Length > 0
          && !mismatchMasked.Contains(realValue, StringComparison.OrdinalIgnoreCase), mismatchMasked);
var mismatchMaskedError = Attempt(() => ParseJson(mismatchMasked)) is JsonElement mismatchRoot
    ? Text(mismatchRoot, "error") : "";
Check("-- the expected serial is SERIAL-1 and the other unit's SERIAL-2, its name NAME-1",
      mismatchMaskedError.Contains("not the expected 'SERIAL-1'")
      && mismatchMaskedError.Contains("esa_id='SERIAL-2' ('NAME-1')"), mismatchMaskedError);
var rememberedBench = new DroboSettings.RememberedDevice
{
    EsaId = "TST5N0000001", Name = "Bench5N", Host = "192.0.2.10", Model = "Drobo 5N",
};
var rememberedMasked = Attempt(() => DiagnosticsMasking.MaskJson(
    "{\"device\": {\"serial\": \"\"}, \"error\": \"Bench5N at 192.0.2.10 said: no answer from TST5N0000001\"}",
    rememberedBench)) as string ?? "";
Check("the remembered Drobo is masked even when the device block is empty",
      rememberedMasked.Contains("NAME-1 at ADDRESS-1 said: no answer from SERIAL-1"), rememberedMasked);
var rememberedReport = DiagnosticsReport.BuildPublicText(mismatchJson, rememberedBench);
Check("-- and the public report of a mismatch carries neither serial nor either name",
      !rememberedReport.Contains("TST5N0000001") && !rememberedReport.Contains("TSTOTHER0002")
      && !rememberedReport.Contains("OtherBox") && rememberedReport.Contains("'SERIAL-1'"), rememberedReport);
var driverMismatch = DiagnosticsMasking.ScrubText(
    "refusing 192.0.2.10:5000 -- it identifies as esa_id=\"TST'ODD0003\", not the expected 'TST5N0000001'. "
    + "Seen before (esa_id=TSTOTHER0002).", new DiagnosticsMasking.PlaceholderMap());
Check("the driver's wording, a double-quoted serial and a bare one in brackets are masked too",
      !driverMismatch.Contains("TST") && driverMismatch.Contains("esa_id=\"SERIAL-2\"")
      && driverMismatch.Contains("(esa_id=SERIAL-3)"), driverMismatch);

Console.WriteLine("\n== DiagnosticsMasking: values joined to other text, odd letters, assembly versions ==");
var underscored = Attempt(() => DiagnosticsMasking.MaskJson(
    "{\"device\": {\"serial\": \"TST5N0000001\"}, \"error\": \"wrote serial_TST5N0000001_backup.txt\"}"))
    as string ?? "";
Check("a serial joined by underscores is still masked",
      !underscored.Contains("TST5N0000001") && underscored.Contains("serial_SERIAL-1_backup.txt"), underscored);
var kelvinName = Attempt(() => DiagnosticsMasking.MaskJson(
    "{\"device\": {\"name\": \"kitchen\"}, \"error\": \"\u212Aitchen did not answer\"}")) as string ?? "";
Check("a name matched through a look-alike letter (the Kelvin sign) is masked, not a crash",
      kelvinName.Contains("NAME-1 did not answer"), kelvinName);
var assemblyText = DiagnosticsMasking.MaskText(
    "Could not load 'Some.Library, Version=1.2.0.0' from 192.0.2.30");
Check("an assembly version is kept, an address beside it is not",
      assemblyText.Contains("Version=1.2.0.0") && assemblyText.Contains("ADDRESS-1"), assemblyText);

Console.WriteLine("\n== DiagnosticsMasking: the error dialog's copy ==");
Exception pathError;
try { throw new IOException(@"Access to C:\Users\username\AppData\Local\x.json and \\bench5n\Photos was denied"); }
catch (Exception ex) { pathError = ex; }
var dialogEntry = AppErrorLog.FormatEntry(pathError, "UI thread", "9.9.9",
                                          new DateTimeOffset(2026, 11, 20, 12, 0, 0, TimeSpan.Zero));
var dialogText = DiagnosticsMasking.MaskText(dialogEntry, rememberedBench);
Check("the dialog's copy masks the user name and the UNC server and share",
      !dialogText.Contains("username") && !dialogText.Contains("bench5n", StringComparison.OrdinalIgnoreCase)
      && !dialogText.Contains("Photos") && dialogText.Contains(@"\USER-1\AppData\Local\x.json"), dialogText);
Check("-- and keeps the header, the type and the stack",
      dialogText.StartsWith("=== 2026-11-20 12:00:00 +00:00  Drobo Dashboard REBORN v9.9.9  (UI thread)")
      && dialogText.Contains("System.IO.IOException") && dialogText.Contains("   at "), dialogText);

Console.WriteLine("\n== DiagnosticsReport: the public copy ==");
var publicReport = DiagnosticsReport.BuildPublicText(sampleStatusJson);
foreach (var realValue in syntheticRealValues)
    Check($"the public report does not contain {realValue}",
          !publicReport.Contains(realValue, StringComparison.OrdinalIgnoreCase), publicReport);
Check("it says it is made for posting publicly", publicReport.Contains("made for posting in a public issue"), publicReport);
Check("its summary shows the placeholders",
      publicReport.Contains("SERIAL-1") || publicReport.Contains("DRIVE-SERIAL-1"), publicReport);
Check("-- and still leads with why the Drobo could not be reached",
      publicReport.Contains("WHY THE DROBO COULD NOT BE REACHED"), publicReport);
var notJsonReport = DiagnosticsReport.BuildPublicText("<html>TST5N0000001 at 192.0.2.10</html>");
Check("a reply that is not JSON is left out of the public copy, not posted unread",
      !notJsonReport.Contains("TST5N0000001") && !notJsonReport.Contains("192.0.2.10")
      && notJsonReport.Contains("none of"), notJsonReport);
var unmaskedReport = DiagnosticsReport.BuildText(sampleStatusJson);
Check("the unmasked copy keeps the real values, for the tester's own use",
      unmaskedReport.Contains("TST5N0000001") && unmaskedReport.Contains("TSTDISK0001"), unmaskedReport);
Check("-- and says it is the UNMASKED copy", unmaskedReport.Contains("UNMASKED"), unmaskedReport);


// ---------------------------------------------------------------------------
// AppErrorLog -- what an unexpected error leaves behind.
//
// Until 2026-11-20 the last-chance handler showed the exception's message
// alone and wrote nothing down. Only the pure parts are checked here; Append
// writes to the real %LOCALAPPDATA%, which a test must not touch.
Console.WriteLine("\n== AppErrorLog: the whole error, with version and time ==");
Exception caught;
try { throw new InvalidOperationException("outer problem", new FormatException("inner problem")); }
catch (Exception ex) { caught = ex; }
var errorEntry = AppErrorLog.FormatEntry(caught, "UI thread", "9.9.9",
                                         new DateTimeOffset(2026, 11, 20, 12, 0, 0, TimeSpan.Zero));
Check("the entry names the exception type and its message",
      errorEntry.Contains("System.InvalidOperationException") && errorEntry.Contains("outer problem"), errorEntry);
Check("-- the inner exception too", errorEntry.Contains("System.FormatException: inner problem"), errorEntry);
Check("-- and the stack", errorEntry.Contains("   at "), errorEntry);
Check("the header carries the time, the version and the handler",
      errorEntry.StartsWith("=== 2026-11-20 12:00:00 +00:00  Drobo Dashboard REBORN v9.9.9  (UI thread)"), errorEntry);

Console.WriteLine("\n== AppErrorLog: no token and no password is written down ==");
const string syntheticToken = "synthetic-token-0123456789abcdef";
AppErrorLog.RememberAgentToken(syntheticToken);
var tokenEntry = AppErrorLog.FormatEntry(new Exception("request with " + syntheticToken + " refused"),
                                         "UI thread", "9.9.9", DateTimeOffset.Now);
Check("the remembered agent token is removed", !tokenEntry.Contains(syntheticToken) && tokenEntry.Contains("[removed]"),
      tokenEntry);
AppErrorLog.RememberAgentToken("");
var assignmentText = AppErrorLog.RemoveSecrets(
    "password=hunter2 X-Agent-Token: abcdefgh12345678 \"token\": \"zyxwvut987\" pwd=x1");
Check("token and password assignments are removed",
      !assignmentText.Contains("hunter2") && !assignmentText.Contains("abcdefgh12345678")
      && !assignmentText.Contains("zyxwvut987") && !assignmentText.Contains("x1"), assignmentText);
Check("-- leaving ordinary words about passwords alone",
      AppErrorLog.RemoveSecrets("the username or password is wrong") == "the username or password is wrong");
AppErrorLog.RememberAgentToken("short");
Check("a token still being typed (under 8 characters) is not hunted for",
      AppErrorLog.RemoveSecrets("a short message") == "a short message");
AppErrorLog.RememberAgentToken("");

Console.WriteLine("\n== AppErrorLog: the log is capped ==");
var longLog = string.Concat(Enumerable.Range(1, 400).Select(entryNumber =>
    $"=== entry {entryNumber}\n" + new string('x', 3000) + "\n"));
var newestHalf = AppErrorLog.NewestHalf(longLog);
Check("the newest half starts at an entry header", newestHalf.StartsWith("=== entry "), newestHalf[..20]);
Check("-- keeps the newest entry and drops the oldest",
      newestHalf.Contains("=== entry 400\n") && !newestHalf.Contains("=== entry 1\n"));
var trimFolder = Path.Combine(Path.GetTempPath(), "drobo-applog-test-" + Guid.NewGuid().ToString("N"));
try
{
    Directory.CreateDirectory(trimFolder);
    var trimPath = Path.Combine(trimFolder, "app.log");
    File.WriteAllText(trimPath, longLog);
    AppErrorLog.TrimIfTooLarge(trimPath);
    var trimmedLength = new FileInfo(trimPath).Length;
    Check("a log over the cap is cut to under it", trimmedLength < AppErrorLog.MaxLogSizeBytes, trimmedLength);
    File.WriteAllText(trimPath, "=== small\n");
    AppErrorLog.TrimIfTooLarge(trimPath);
    Check("-- and one under the cap is left alone", File.ReadAllText(trimPath) == "=== small\n");
}
finally
{
    try { Directory.Delete(trimFolder, recursive: true); } catch { }
}


// ---------------------------------------------------------------------------
// CredentialStore -- saving a Drobo login on this PC.
//
// These touch the REAL Windows Credential Manager, deliberately: a store that
// is mocked in its own tests has not been shown to store anything. They are
// safe to run repeatedly. Everything is written under a .invalid host name
// (RFC 2606 reserves that suffix so it can never be a real machine) and
// removed again in the finally block, whether the checks passed or not.
Console.WriteLine("\n== CredentialStore: reading a server name out of a UNC path ==");
Check("a normal UNC path gives the server", CredentialStore.HostOf(@"\\10.0.0.5\Documents") == "10.0.0.5");
Check("a server with no share still gives the server", CredentialStore.HostOf(@"\\drobo") == "drobo");
Check("a trailing backslash does not become part of the name", CredentialStore.HostOf(@"\\drobo\") == "drobo");
Check("forward slashes are accepted too", CredentialStore.HostOf("//drobo/Photos") == "drobo");
Check("a deeper path still gives just the server", CredentialStore.HostOf(@"\\drobo\Photos\2026\a.jpg") == "drobo");
Check("a local path is not a UNC path", CredentialStore.HostOf(@"D:\Shares\Photos") is null);
Check("empty is not a UNC path", CredentialStore.HostOf("") is null);
Check("a bare double backslash names no server", CredentialStore.HostOf(@"\\") is null);

Console.WriteLine("\n== CredentialStore: save, find, forget ==");
const string testHost = "drobo-dashboard-reborn-selftest.invalid";
try
{
    // Start from nothing, so a leftover from an interrupted run cannot make a
    // save look successful when it actually did nothing.
    CredentialStore.Forget(testHost);
    Check("no saved login before we save one", !CredentialStore.HasSavedLogin(testHost));
    Check("and no username to report", CredentialStore.SavedUserName(testHost) is null);

    var problem = CredentialStore.Save(testHost, "testuser", "a-test-password");
    Check("saving reports no problem", problem is null, problem);
    Check("the login is now found", CredentialStore.HasSavedLogin(testHost));
    Check("the username comes back", CredentialStore.SavedUserName(testHost) == "testuser",
          CredentialStore.SavedUserName(testHost));

    // Saving again must REPLACE rather than fail or duplicate: a user
    // retyping a changed Drobo password is the normal case, not an error.
    var again = CredentialStore.Save(testHost, "seconduser", "a-different-password");
    Check("saving over an existing login succeeds", again is null, again);
    Check("and the new username replaced the old one",
          CredentialStore.SavedUserName(testHost) == "seconduser",
          CredentialStore.SavedUserName(testHost));

    Check("forgetting it succeeds", CredentialStore.Forget(testHost));
    Check("it is gone afterwards", !CredentialStore.HasSavedLogin(testHost));
    Check("forgetting again is not an error (nothing to forget is the same outcome)",
          CredentialStore.Forget(testHost));

    // Windows caps a share password at 512 bytes of UTF-16. Refusing is the
    // only safe answer: a silently truncated password would save fine and
    // then fail every future connection with nothing to explain it.
    var tooLong = CredentialStore.Save(testHost, "testuser", new string('x', 257));
    Check("an over-long password is refused, not truncated", tooLong is not null);
    Check("and nothing was written for it", !CredentialStore.HasSavedLogin(testHost));

    var exactlyMax = CredentialStore.Save(testHost, "testuser", new string('x', 256));
    Check("256 characters is still accepted (the boundary is not off by one)",
          exactlyMax is null, exactlyMax);
    CredentialStore.Forget(testHost);

    Check("a blank server name is refused", CredentialStore.Save("", "u", "p") is not null);
    Check("a blank username is refused", CredentialStore.Save(testHost, "", "p") is not null);
    Check("refusing a blank username wrote nothing", !CredentialStore.HasSavedLogin(testHost));
}
finally
{
    CredentialStore.Forget(testHost);
}

// ---------------------------------------------------------------------------
// JsonHelpers -- reading the agent's JSON without throwing mid-render.
//
// Every call below goes through Attempt, so a getter that throws shows up as a
// FAIL carrying the exception, not as a crash of this program. Until
// 2026-11-02 a JSON null where an object, number or flag was expected, or a
// number with a fraction where an integer was, threw inside the Status tab's
// rendering, and the app reported that as "Cannot reach the agent".

static JsonElement ParseJson(string json) => JsonDocument.Parse(json).RootElement.Clone();

static object? Attempt(Func<object?> read)
{
    try { return read(); }
    catch (Exception ex) { return ex.GetType().Name + ": " + ex.Message; }
}

Console.WriteLine("\n== JsonHelpers: a JSON null or a fraction never throws ==");
var oddReply = ParseJson("{\"a\":null,\"b\":41.5,\"c\":true,\"o\":null,\"big\":1e10,\"n\":7}");
var nullElement = oddReply.GetProperty("o");
var flagOnNull = Attempt(() => JsonHelpers.IsTrue(oddReply, "a"));
Check("a null flag is false", object.Equals(flagOnNull, false), flagOnNull);
var int64OnNull = Attempt(() => JsonHelpers.GetInt64OrZero(oddReply, "a"));
Check("a null integer is 0", object.Equals(int64OnNull, 0L), int64OnNull);
var doubleOnNull = Attempt(() => JsonHelpers.GetDoubleOrZero(oddReply, "a"));
Check("a null number is 0", object.Equals(doubleOnNull, 0.0), doubleOnNull);
var int32OnFraction = Attempt(() => JsonHelpers.GetInt32Or(oddReply, "b", 7));
Check("41.5 where an int is wanted gives the fallback, not FormatException",
      object.Equals(int32OnFraction, 7), int32OnFraction);
var int64OnFraction = Attempt(() => JsonHelpers.GetInt64OrZero(oddReply, "b"));
Check("41.5 where a long is wanted gives 0, not FormatException",
      object.Equals(int64OnFraction, 0L), int64OnFraction);
var int32TooBig = Attempt(() => JsonHelpers.GetInt32Or(oddReply, "big", 7));
Check("1e10 where an int is wanted gives the fallback", object.Equals(int32TooBig, 7), int32TooBig);
var int32Plain = Attempt(() => JsonHelpers.GetInt32Or(oddReply, "n", 0));
Check("a whole number is read as itself", object.Equals(int32Plain, 7), int32Plain);
var doublePlain = Attempt(() => JsonHelpers.GetDoubleOrZero(oddReply, "b"));
Check("41.5 is read as 41.5 where a number is wanted", object.Equals(doublePlain, 41.5), doublePlain);
var flagTrue = Attempt(() => JsonHelpers.IsTrue(oddReply, "c"));
Check("a true flag is still true", object.Equals(flagTrue, true), flagTrue);

// The element itself being null: `"capacity": null` handed to a renderer.
var stringOnNullElement = Attempt(() => JsonHelpers.GetStringOr(nullElement, "x", "fb"));
Check("a string read from a null element gives the fallback, not InvalidOperationException",
      object.Equals(stringOnNullElement, "fb"), stringOnNullElement);
var int64OnNullElement = Attempt(() => JsonHelpers.GetInt64OrZero(nullElement, "x"));
Check("an integer read from a null element is 0", object.Equals(int64OnNullElement, 0L), int64OnNullElement);
var flagOnNullElement = Attempt(() => JsonHelpers.IsTrue(nullElement, "x"));
Check("a flag read from a null element is false", object.Equals(flagOnNullElement, false), flagOnNullElement);
var flagOnDefault = Attempt(() => JsonHelpers.IsTrue(default, "x"));
Check("a flag read from a default element is false", object.Equals(flagOnDefault, false), flagOnDefault);

Console.WriteLine("\n== JsonHelpers: an agent port written as text is still a port ==");
// The agent runs on `"port": "7420"` (it reads int(config["agent"]["port"])),
// so the app must find its URL and token in that file too. The fallback here
// is 1234 so a correct read cannot be mistaken for the fallback.
foreach (var (portJson, expectedPort) in new (string, int)[]
{
    ("{\"port\":\"7420\"}", 7420),
    ("{\"port\":\" 7421 \"}", 7421),
    ("{\"port\":7422}", 7422),
    ("{\"port\":null}", 1234),
    ("{\"port\":\"abc\"}", 1234),
    ("{\"port\":70000}", 1234),
    ("{\"port\":0}", 1234),
    ("{\"port\":7.5}", 1234),
    ("{}", 1234),
})
{
    var portRead = Attempt(() => JsonHelpers.GetPortOr(ParseJson(portJson), "port", 1234));
    Check($"GetPortOr {portJson} -> {expectedPort}", object.Equals(portRead, expectedPort), portRead);
}

Console.WriteLine("\n== WriteOutcomes: an answer that is not an object is not a crash ==");
var outcomeOfNull = Attempt(() => WriteOutcomes.FromAgentAnswer(true, ParseJson("null")));
Check("a null answer is a failure, not InvalidOperationException",
      object.Equals(outcomeOfNull, WriteOutcome.Failed), outcomeOfNull);
var outcomeOfOk = Attempt(() => WriteOutcomes.FromAgentAnswer(true, ParseJson("{\"ok\":true}")));
Check("-- and {ok: true} on a 200 is still confirmed", object.Equals(outcomeOfOk, WriteOutcome.Confirmed), outcomeOfOk);


// ---------------------------------------------------------------------------
// DroboSettings -- what the app remembers between launches.
//
// Until 2026-11-02 one bad value in settings.json (a manual port of 7.5) left
// every later setting at its default, and the next save wrote those defaults
// back; and the file was rewritten in place, so a crash mid-write left a
// truncated file that read as a first launch. The file work below happens in a
// fresh folder under the temp directory, removed again in the finally block.
Console.WriteLine("\n== DroboSettings: a malformed port loses only itself ==");
var badPortSettings = DroboSettings.Parse(
    "{\"manual_devices\":[{\"host\":\"a\",\"port\":7.5},{\"host\":\"b\",\"port\":1e10},"
    + "{\"host\":\"c\",\"port\":7421}],"
    + "\"auto_discovery_enabled\":false,\"close_to_tray_enabled\":true,\"notifications_enabled\":true,"
    + "\"notification_categories\":{\"drives\":true}}");
Check("auto-discovery off is still read after a bad port", !badPortSettings.AutoDiscoveryEnabled);
Check("close to tray is still read", badPortSettings.CloseToTrayEnabled);
Check("notifications on are still read", badPortSettings.NotificationsEnabled);
Check("a notification category is still read",
      badPortSettings.NotificationCategories.TryGetValue("drives", out var drivesOn) && drivesOn);
Check("every address is kept", badPortSettings.ManualDevices.Count == 3, badPortSettings.ManualDevices.Count);
Check("7.5 and 1e10 read as no port given",
      badPortSettings.ManualDevices.Count == 3
      && badPortSettings.ManualDevices[0].Port == 0 && badPortSettings.ManualDevices[1].Port == 0);
Check("a good port is kept as it was",
      badPortSettings.ManualDevices.Count == 3 && badPortSettings.ManualDevices[2].Port == 7421);

var cutShort = DroboSettings.Parse("{\"auto_discovery_enabled\":false,\"notifications_ena");
Check("a file cut short gives first-launch defaults",
      cutShort.AutoDiscoveryEnabled && !cutShort.NotificationsEnabled
      && cutShort.ManualDevices.Count == 0 && cutShort.Remembered is null);
var notAnObject = Attempt(() => DroboSettings.Parse("[1, 2]").AutoDiscoveryEnabled);
Check("a document that is not an object gives the defaults, not a throw",
      object.Equals(notAnObject, true), notAnObject);

Console.WriteLine("\n== DroboSettings: a save replaces the file whole ==");
var settingsFolder = Path.Combine(Path.GetTempPath(),
                                  "drobo-dashboard-reborn-selftest-" + Guid.NewGuid().ToString("N"));
try
{
    Directory.CreateDirectory(settingsFolder);
    var settingsPath = Path.Combine(settingsFolder, "settings.json");
    var toSave = new DroboSettings.Settings
    {
        Remembered = new DroboSettings.RememberedDevice
        {
            EsaId = "SELFTEST-SERIAL", Host = "drobo-selftest.invalid", Name = "Selftest", Model = "Drobo 5N",
        },
        AutoDiscoveryEnabled = false,
        DiagnosticsPreference = DroboSettings.DiagnosticsPreference.File,
        CloseToTrayEnabled = true,
        NotificationsEnabled = true,
    };
    toSave.ManualDevices.Add(new DroboSettings.ManualDevice { Host = "drobo-selftest.invalid", Port = 5000 });
    toSave.NotificationCategories["drives"] = true;

    Check("saving reports success", DroboSettings.SaveAll(toSave, settingsPath));
    var loadedBack = DroboSettings.LoadAll(settingsPath);
    Check("the remembered Drobo comes back",
          loadedBack.Remembered is { EsaId: "SELFTEST-SERIAL", Host: "drobo-selftest.invalid",
                                     Name: "Selftest", Model: "Drobo 5N" });
    Check("the typed address comes back",
          loadedBack.ManualDevices.Count == 1 && loadedBack.ManualDevices[0].Host == "drobo-selftest.invalid"
          && loadedBack.ManualDevices[0].Port == 5000);
    Check("every switch comes back",
          !loadedBack.AutoDiscoveryEnabled && loadedBack.CloseToTrayEnabled && loadedBack.NotificationsEnabled
          && loadedBack.DiagnosticsPreference == DroboSettings.DiagnosticsPreference.File);
    Check("the notification category comes back",
          loadedBack.NotificationCategories.TryGetValue("drives", out var drivesBack) && drivesBack);
    Check("no temporary file is left behind",
          !File.Exists(settingsPath + ".tmp") && !Directory.Exists(settingsPath + ".tmp"));

    // A folder where the temporary file must go makes the write fail before
    // the real file is touched. Written in place, the save would have
    // succeeded and changed it.
    Directory.CreateDirectory(settingsPath + ".tmp");
    var changed = DroboSettings.LoadAll(settingsPath);
    changed.AutoDiscoveryEnabled = true;
    changed.NotificationsEnabled = false;
    var blockedSave = Attempt(() => DroboSettings.SaveAll(changed, settingsPath));
    Check("a save that cannot be written says so, without throwing",
          object.Equals(blockedSave, false), blockedSave);
    var afterBlockedSave = DroboSettings.LoadAll(settingsPath);
    Check("-- and the file keeps its old values",
          !afterBlockedSave.AutoDiscoveryEnabled && afterBlockedSave.NotificationsEnabled);
    Check("-- and the folder in the way is not deleted", Directory.Exists(settingsPath + ".tmp"));
}
finally
{
    try { Directory.Delete(settingsFolder, recursive: true); }
    catch { /* a leftover temp folder is harmless */ }
}


// ---------------------------------------------------------------------------
// WriteOutcomes -- a write whose answer never arrived.
//
// Until 2026-11-02 the app waited 8 seconds for a write's answer, as long as
// one of the agent's own waits on the Drobo, and showed every failed request
// as "Could not reach the agent" in red -- including writes the Drobo had
// taken. Only a request that cannot have reached the agent is a failure now.
Console.WriteLine("\n== WriteOutcomes: an unanswered write is never a red failure ==");
var timedOut = WriteOutcomes.FromTransportFailure(
    new TaskCanceledException("timed out", new TimeoutException()));
Check("a timeout is unsettled", timedOut == WriteOutcome.Unsettled, timedOut);
var cutOff = WriteOutcomes.FromTransportFailure(
    new HttpRequestException(HttpRequestError.ResponseEnded, "the response ended early"));
Check("a connection cut off after the request went out is unsettled",
      cutOff == WriteOutcome.Unsettled, cutOff);
var garbledAnswer = WriteOutcomes.FromTransportFailure(new JsonException("not JSON"));
Check("an answer that is not JSON is unsettled", garbledAnswer == WriteOutcome.Unsettled, garbledAnswer);
var refused = WriteOutcomes.FromTransportFailure(
    new HttpRequestException(HttpRequestError.ConnectionError, "connection refused"));
Check("an agent that is not listening is a plain failure (nothing was sent)",
      refused == WriteOutcome.Failed, refused);
Check("-- and is still called unreachable",
      WriteOutcomes.DescribeTransportFailure(
          new HttpRequestException(HttpRequestError.ConnectionError, "x")).StartsWith("Could not reach the agent"));
var timeoutWords = WriteOutcomes.DescribeTransportFailure(new TaskCanceledException("timed out"));
Check("a timeout says the change may have been done, and to look first",
      timeoutWords.Contains("may have been done") && timeoutWords.Contains("Refresh and look")
      && !timeoutWords.Contains("Could not reach"), timeoutWords);
Check("the write timeout outlasts the agent's worst case (4 connections x 3 waits x 8 s)",
      WriteOutcomes.AgentWriteTimeoutSeconds > 4 * 3 * 8, WriteOutcomes.AgentWriteTimeoutSeconds);


// ---------------------------------------------------------------------------
// DiagnosticsReport -- the file "Copy diagnostics" saves.
//
// Until 2026-11-02 a reply with no "device" (the agent's first seconds, before
// any reading) threw before anything was written, so the report lost its
// "why it could not be reached" section and claimed the JSON was unreadable;
// and an unknown uptime read "just started".
Console.WriteLine("\n== DiagnosticsReport: no reading yet still says why ==");
var noReadingReport = Attempt(() => DiagnosticsReport.BuildText(
    "{\"reachable\":false,\"error\":\"no reading taken yet\","
    + "\"network_hint\":{\"headline\":\"H1\",\"detail\":\"D1\"}}")) as string ?? "";
Check("the why-unreachable section is there",
      noReadingReport.Contains("WHY THE DROBO COULD NOT BE REACHED") && noReadingReport.Contains("H1"),
      noReadingReport);
Check("-- and the JSON is not called unreadable",
      !noReadingReport.Contains("Could not read the agent's response as JSON"), noReadingReport);
Check("-- and the agent's own error is reported",
      noReadingReport.Contains("No reading from the Drobo yet") && noReadingReport.Contains("no reading taken yet"),
      noReadingReport);

Console.WriteLine("\n== DiagnosticsReport: an unknown uptime is unknown ==");
static string UptimeLine(string report) =>
    report.Split('\n').FirstOrDefault(line => line.TrimStart().StartsWith("Uptime"))?.Trim() ?? "(no Uptime line)";
var noUptime = UptimeLine(DiagnosticsReport.BuildText("{\"reachable\":true,\"device\":{\"name\":\"n\"}}"));
Check("a missing uptime reads unknown, not just started", noUptime.EndsWith("unknown"), noUptime);
var nullUptime = UptimeLine(DiagnosticsReport.BuildText(
    "{\"reachable\":true,\"device\":{\"name\":\"n\",\"uptime_seconds\":null}}"));
Check("a null uptime reads unknown", nullUptime.EndsWith("unknown"), nullUptime);
var zeroUptime = UptimeLine(DiagnosticsReport.BuildText(
    "{\"reachable\":true,\"device\":{\"name\":\"n\",\"uptime_seconds\":0}}"));
Check("a reported 0 still reads just started", zeroUptime.EndsWith("just started"), zeroUptime);
var twoHours = UptimeLine(DiagnosticsReport.BuildText(
    "{\"reachable\":true,\"device\":{\"name\":\"n\",\"uptime_seconds\":7200}}"));
Check("7200 seconds still reads 2h", twoHours.EndsWith("2h"), twoHours);

Console.WriteLine("\n== DiagnosticsReport: an event whose time is not decoded says so ==");
var eventReport = DiagnosticsReport.BuildText(
    "{\"reachable\":true,\"device\":{\"name\":\"n\"},"
    + "\"event_log\":[{\"ts_iso\":\"\",\"severity\":\"info\",\"message\":\"M1\"}]}");
var eventLine = eventReport.Split('\n').FirstOrDefault(line => line.Contains("M1"))?.Trim() ?? "(no event line)";
Check("an empty ts_iso prints as not decoded", eventLine.StartsWith("time not decoded"), eventLine);


// ---------------------------------------------------------------------------
// The in-app file browser's rules (SharePaths, and the sizes it shows).
//
// Until 2026-11-02 the browser's root check was a bare StartsWith, so
// \\host\Photos2 passed for \\host\Photos, and it counted sizes in 1024s
// where the Status tab counts in 1000s.
Console.WriteLine("\n== SharePaths: inside the share means inside it ==");
const string photosRoot = @"\\host\Photos";
foreach (var (candidatePath, expectedInside) in new (string, bool)[]
{
    (@"\\host\Photos", true),
    (@"\\HOST\photos\2024", true),
    (@"\\host\Photos\2024\June", true),
    (@"\\host\Photos2", false),
    (@"\\host\Photos\..\Other", false),
    (@"\\host\Photos\.\2024", false),
    (@"\\host", false),
    ("", false),
})
{
    var inside = Attempt(() => SharePaths.IsWithinShareRoot(candidatePath, photosRoot));
    Check($"{(candidatePath.Length > 0 ? candidatePath : "(empty)")} inside {photosRoot}: {expectedInside}",
          object.Equals(inside, expectedInside), inside);
}

Console.WriteLine("\n== the file browser shows sizes as the Status tab does ==");
var browserSize = JsonHelpers.FormatBytes(1_500_000);
Check("1,500,000 bytes is 1.50 MB (the browser showed 1.4 MB)", browserSize == "1.50 MB", browserSize);


Console.WriteLine();
Console.WriteLine(fails.Count == 0 ? "ALL PASS" : $"{fails.Count} FAILURES: {string.Join(", ", fails)}");
return fails.Count == 0 ? 0 : 1;
