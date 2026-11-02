using System;
using System.Text;
using System.Text.Json;
using static DroboDashboardReborn.JsonHelpers;

namespace DroboDashboardReborn;

/// <summary>
/// Turns the agent's /api/status JSON into a plain-text report a person can
/// actually skim -- the same fields the Status tab already renders, laid out
/// as labeled lines instead of a JSON blob. Used only by "Copy diagnostics"
/// -> "Save to a file"; the clipboard path still copies the raw JSON exactly
/// as before, since that's what someone pastes into a bug tracker that wants
/// structured data.
/// </summary>
internal static class DiagnosticsReport
{
    /// <summary>Builds the full file contents: a human-readable summary, then the
    /// raw JSON under its own heading for anyone who wants it (or whose forum
    /// thread wants exact numbers). The serial-number warning is repeated here,
    /// not just on the Tools tab, because this text can end up saved to a file
    /// and forwarded on somewhere the on-screen warning is long gone.</summary>
    public static string BuildText(string json)
    {
        var report = new StringBuilder();
        report.AppendLine("Drobo Dashboard REBORN -- Diagnostics");
        report.AppendLine("Generated " + DateTime.Now.ToString("yyyy-MM-dd HH:mm"));
        report.AppendLine();
        report.AppendLine("This file includes your drive serial numbers, and -- when the Drobo could");
        report.AppendLine("not be reached -- the private network addresses of this PC. Check who you");
        report.AppendLine("send it to before sharing it anywhere public (forums, support tickets, etc).");
        report.AppendLine();

        try
        {
            using var doc = JsonDocument.Parse(json);
            AppendSummary(report, doc.RootElement);
        }
        catch (Exception ex)
        {
            report.AppendLine("Could not read the agent's response as JSON (" + ex.Message + ") -- see the raw text below.");
            report.AppendLine();
        }

        report.AppendLine(new string('-', 72));
        report.AppendLine("RAW JSON (for support tickets or advanced troubleshooting)");
        report.AppendLine(new string('-', 72));
        report.AppendLine(json);
        return report.ToString();
    }

    /// <summary>
    /// The human-readable part, one heading per section, in the order a
    /// reader needs them: why the Drobo was unreachable (if known), then
    /// the device, capacity, drives, recent events and performance.
    /// Sections the reply does not carry are left out rather than printed
    /// empty.
    /// </summary>
    private static void AppendSummary(StringBuilder report, JsonElement root)
    {
        // Read before anything is written, as it always has been: a reply
        // with no "device" throws here, and BuildText reports that instead.
        var device = root.GetProperty("device");
        var reachable = root.TryGetProperty("reachable", out var reachableElement)
                        && reachableElement.ValueKind == JsonValueKind.True;

        AppendNetworkHint(report, root);
        AppendDevice(report, root, device, reachable);
        AppendCapacity(report, root);
        AppendDrives(report, root);
        AppendRecentEvents(report, root);
        AppendPerformance(report, root);
    }

    /// <summary>
    /// First, before any device fields: if the Drobo could not be reached
    /// and the agent worked out why, that is the whole story and everything
    /// below it is stale. A report that opens with an empty DEVICE block
    /// reads as "the NAS is broken" when the answer may be that this PC is
    /// on the wrong network -- and the person reading the report is often
    /// not the person who generated it.
    /// </summary>
    private static void AppendNetworkHint(StringBuilder report, JsonElement root)
    {
        if (!root.TryGetProperty("network_hint", out var hint) || hint.ValueKind != JsonValueKind.Object) return;

        report.AppendLine("WHY THE DROBO COULD NOT BE REACHED");
        report.AppendLine("  " + GetStringOr(hint, "headline", ""));
        report.AppendLine("  " + GetStringOr(hint, "detail", ""));
        var caveat = GetStringOr(hint, "caveat", "");
        if (caveat.Length > 0) report.AppendLine("  " + caveat);
        report.AppendLine();
    }

    /// <summary>The DEVICE block, always written: name, model, firmware,
    /// address, overall state and uptime, plus the firmware note when the
    /// agent flags the installed build.</summary>
    private static void AppendDevice(StringBuilder report, JsonElement root, JsonElement device, bool reachable)
    {
        report.AppendLine("DEVICE");
        AppendField(report, "Name", GetStringOr(device, "name", "Drobo"));
        AppendField(report, "Model", GetStringOr(device, "model", ""));
        AppendField(report, "Firmware", GetStringOr(device, "firmware", "?"));
        AppendField(report, "Address", GetStringOr(device, "ip", ""));
        AppendField(report, "Status", GetStringOr(root, "overall_state", "unknown") + (reachable ? "" : "  (unreachable)"));
        AppendField(report, "Uptime", FormatUptime(GetInt64OrZero(device, "uptime_seconds")));
        // Sits with the version it is about. Only present for the builds Drobo
        // withdrew, and worth carrying into a support thread: anyone reading
        // this report otherwise has to know the 5N's release history to spot it.
        if (root.TryGetProperty("firmware_advisory", out var firmwareAdvisory)
            && firmwareAdvisory.ValueKind == JsonValueKind.Object
            && firmwareAdvisory.TryGetProperty("notable", out var notable) && notable.ValueKind == JsonValueKind.True)
        {
            AppendField(report, "Firmware note", GetStringOr(firmwareAdvisory, "headline", ""));
        }
        report.AppendLine();
    }

    /// <summary>The CAPACITY block, in bytes formatted for reading, with the
    /// used share as a percentage.</summary>
    private static void AppendCapacity(StringBuilder report, JsonElement root)
    {
        if (!root.TryGetProperty("capacity", out var capacity) || capacity.ValueKind != JsonValueKind.Object) return;

        report.AppendLine("CAPACITY");
        double usedFraction = capacity.TryGetProperty("used_fraction", out var usedFractionElement)
                              && usedFractionElement.ValueKind == JsonValueKind.Number
            ? usedFractionElement.GetDouble() : 0;
        AppendField(report, "Used", $"{FormatBytes(GetInt64OrZero(capacity, "used_bytes"))}  ({usedFraction * 100:0.0}%)");
        AppendField(report, "Free", FormatBytes(GetInt64OrZero(capacity, "free_bytes")));
        AppendField(report, "Usable", FormatBytes(GetInt64OrZero(capacity, "usable_bytes")));
        AppendField(report, "Protection", FormatBytes(GetInt64OrZero(capacity, "protection_bytes")));
        AppendField(report, "Raw", FormatBytes(GetInt64OrZero(capacity, "raw_bytes")));
        report.AppendLine();
    }

    /// <summary>The DRIVES block: one line per bay, empty bays included so
    /// the reader can see which slots are free.</summary>
    private static void AppendDrives(StringBuilder report, JsonElement root)
    {
        if (!root.TryGetProperty("drives", out var drives) || drives.ValueKind != JsonValueKind.Array) return;

        report.AppendLine("DRIVES");
        foreach (var drive in drives.EnumerateArray())
            AppendDriveLine(report, drive);
        report.AppendLine();
    }

    /// <summary>One bay's line. The serial is included on purpose, which is
    /// why BuildText's header warns about sharing the file.</summary>
    private static void AppendDriveLine(StringBuilder report, JsonElement drive)
    {
        int bay = GetInt32Or(drive, "bay", 0);
        bool present = IsTrue(drive, "present");
        if (!present)
        {
            report.AppendLine($"  Bay {bay}   empty");
            return;
        }

        var state = GetStringOr(drive, "state", "unknown");
        var model = GetStringOr(drive, "model", "disk");
        var serial = GetStringOr(drive, "serial", "");
        double? tempC = drive.TryGetProperty("temperature_c", out var temperatureElement)
                        && temperatureElement.ValueKind == JsonValueKind.Number
            ? temperatureElement.GetDouble() : (double?)null;
        var temperatureText = tempC.HasValue ? $"{tempC:0.0}C" : "";
        report.AppendLine($"  Bay {bay}   {state,-8} {model}   serial {serial}   {temperatureText}".TrimEnd());
    }

    /// <summary>The RECENT EVENTS block, left out entirely when the log is
    /// empty or missing so an empty heading does not suggest a fault.</summary>
    private static void AppendRecentEvents(StringBuilder report, JsonElement root)
    {
        if (!root.TryGetProperty("event_log", out var events) || events.ValueKind != JsonValueKind.Array
            || events.GetArrayLength() <= 0) return;

        report.AppendLine("RECENT EVENTS");
        foreach (var logEntry in events.EnumerateArray())
            report.AppendLine($"  {GetStringOr(logEntry, "ts_iso", "?"),-20} {GetStringOr(logEntry, "severity", "info"),-8} {GetStringOr(logEntry, "message", "")}");
        report.AppendLine();
    }

    /// <summary>The PERFORMANCE block, with the time the Drobo measured the
    /// figures when the agent knows it.</summary>
    private static void AppendPerformance(StringBuilder report, JsonElement root)
    {
        if (!root.TryGetProperty("performance", out var performance) || performance.ValueKind != JsonValueKind.Object) return;

        report.AppendLine("PERFORMANCE");
        AppendField(report, "Read", GetInt64OrZero(performance, "read_mb_per_s") + " MB/s");
        AppendField(report, "Write", GetInt64OrZero(performance, "write_mb_per_s") + " MB/s");
        AppendField(report, "IOPS", GetInt64OrZero(performance, "iops").ToString());
        // When the Drobo sent these: a failed fetch keeps the last figures,
        // so they can be much older than the report.
        if (performance.TryGetProperty("measured_at", out var measuredAtUnixSeconds)
            && measuredAtUnixSeconds.ValueKind == JsonValueKind.Number)
            AppendField(report, "Measured at", DateTimeOffset.FromUnixTimeMilliseconds((long)(measuredAtUnixSeconds.GetDouble() * 1000))
                .ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss"));
        report.AppendLine();
    }

    /// <summary>One indented "label  value" line. The fixed 12-column label
    /// keeps every block's values lined up in a plain-text file.</summary>
    private static void AppendField(StringBuilder report, string label, string value) =>
        report.AppendLine($"  {label,-12}{value}");

    /// <summary>
    /// Uptime as a reader would say it ("3 days 4h", "2h 5m", "7m"), since
    /// a raw seconds count means nothing at a glance. Zero or less reads
    /// "just started" -- and so does a missing or null uptime_seconds,
    /// which GetInt64OrZero turns into 0. Under a minute reads "1m", not
    /// "0m".
    /// </summary>
    private static string FormatUptime(long uptimeSeconds)
    {
        if (uptimeSeconds <= 0) return "just started";
        var days = uptimeSeconds / 86400;
        var hours = (uptimeSeconds % 86400) / 3600;
        var minutes = (uptimeSeconds % 3600) / 60;
        if (days > 0) return $"{days} day{(days == 1 ? "" : "s")}" + (hours > 0 ? $" {hours}h" : "");
        if (hours > 0) return $"{hours}h" + (minutes > 0 ? $" {minutes}m" : "");
        return $"{Math.Max(1, minutes)}m";
    }
}
