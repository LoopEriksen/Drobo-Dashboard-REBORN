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
