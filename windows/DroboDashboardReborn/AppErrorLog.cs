using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;

namespace DroboDashboardReborn;

/// <summary>
/// What the app writes down when something goes wrong that no handler
/// expected: the whole exception, with the app version and the time, appended
/// to %LOCALAPPDATA%\DroboDashboardReborn\app.log.
///
/// Why: until 2026-11-20 the last-chance handler showed one sentence, such as
/// "Object reference not set to an instance of an object", and wrote nothing
/// down, so a tester's report carried that sentence and no way to find where
/// it came from. Nobody has pressed every screen on a Windows PC yet, so those
/// reports are coming. The formatting and the scrubbing are pure, so the test
/// program checks them on any machine; only Append touches the disk.
/// </summary>
internal static class AppErrorLog
{
    /// <summary>The log is cut back to its newest half past this size, so a
    /// fault that repeats every poll cannot fill the disk; 1 MB holds a few
    /// hundred stack traces.</summary>
    internal const long MaxLogSizeBytes = 1_000_000;

    /// <summary>The agent's access token as the app currently holds it, set
    /// by MainWindow whenever the token box changes. Kept so it can be
    /// removed from anything written down; the log must never carry it.
    /// Read from any thread, hence volatile.</summary>
    private static volatile string _agentToken = "";

    /// <summary>A token shorter than this is still being typed, and removing
    /// every "a" or "ab" from a stack trace would make it unreadable.</summary>
    private const int ShortestTokenRemoved = 8;

    /// <summary>The folder settings.json already lives in, so a tester looks
    /// in one place for everything this app keeps.</summary>
    public static string LogFolder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DroboDashboardReborn");

    /// <summary>Full path of app.log.</summary>
    public static string LogPath => Path.Combine(LogFolder, "app.log");

    /// <summary>Remembers the current agent token so FormatEntry can remove
    /// it. Called from MainWindow's token box.</summary>
    public static void RememberAgentToken(string? token) => _agentToken = token ?? "";

    /// <summary>The app's version as the Tools tab shows it: x.y.z, without
    /// the "+git-sha" build metadata the SDK appends.</summary>
    public static string AppVersion()
    {
        var version = Assembly.GetExecutingAssembly()
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
        if (string.IsNullOrEmpty(version))
            version = Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "unknown";
        var metadataStart = version.IndexOf('+');
        return metadataStart >= 0 ? version[..metadataStart] : version;
    }

    /// <summary>
    /// One log entry: a header line naming when, which version and which
    /// handler caught it, then Exception.ToString() -- type, message, stack
    /// and every inner exception -- with secrets removed. The error dialog
    /// shows the same text with names and addresses masked as well
    /// (DiagnosticsMasking.MaskText), since it is the copy a tester posts;
    /// the log keeps them, for the tester's own use.
    /// </summary>
    public static string FormatEntry(Exception exception, string caughtBy, string appVersion, DateTimeOffset when)
    {
        var entry = new StringBuilder();
        entry.Append("=== ").Append(when.ToString("yyyy-MM-dd HH:mm:ss zzz"))
             .Append("  Drobo Dashboard REBORN v").Append(appVersion)
             .Append("  (").Append(caughtBy).AppendLine(")");
        entry.AppendLine(RemoveSecrets(exception.ToString()));
        return entry.ToString();
    }

    // The forms a secret takes in text: a token or password given as
    // key=value, key: value or "key": "value", and the agent's own header.
    private static readonly Regex SecretAssignment = new(
        @"(?i)(?<key>x-agent-token|token|password|passwd|pwd|secret)(?<separator>""?\s*[:=]\s*""?)(?<value>[^\s""&,;]+)",
        RegexOptions.CultureInvariant);

    /// <summary>
    /// The text with the remembered agent token, and anything written as a
    /// token or password assignment, replaced by [removed]. No path in the
    /// app is known to put either into an exception message; this is so that
    /// one added later still cannot reach the log or the dialog.
    /// </summary>
    public static string RemoveSecrets(string text)
    {
        var token = _agentToken;
        var scrubbed = token.Length >= ShortestTokenRemoved
            ? text.Replace(token, "[removed]", StringComparison.Ordinal)
            : text;
        return SecretAssignment.Replace(scrubbed, match =>
            match.Groups["key"].Value + match.Groups["separator"].Value + "[removed]");
    }

    /// <summary>
    /// Appends an entry to app.log, creating the folder when needed and
    /// cutting the file back first when it has grown past MaxLogSizeBytes.
    /// Never throws: it runs inside the last-chance handlers, and a logging
    /// failure there must not become a second crash. Returns whether the
    /// entry was written, so the dialog only names a file that holds it.
    /// </summary>
    public static bool Append(string entry)
    {
        try
        {
            Directory.CreateDirectory(LogFolder);
            TrimIfTooLarge(LogPath);
            File.AppendAllText(LogPath, entry + Environment.NewLine, Encoding.UTF8);
            return true;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>Keeps the newest half of an oversized log, starting at an
    /// entry boundary so the file never opens mid-trace. The oldest entries
    /// are the ones a tester's report is least likely to be about.</summary>
    internal static void TrimIfTooLarge(string logPath)
    {
        var logFile = new FileInfo(logPath);
        if (!logFile.Exists || logFile.Length <= MaxLogSizeBytes) return;
        var text = File.ReadAllText(logPath, Encoding.UTF8);
        File.WriteAllText(logPath, NewestHalf(text), Encoding.UTF8);
    }

    /// <summary>The second half of the log text, from the first entry header
    /// that starts a line in it; the whole second half when there is
    /// none.</summary>
    internal static string NewestHalf(string logText)
    {
        var half = logText[(logText.Length / 2)..];
        var firstHeader = half.IndexOf("\n=== ", StringComparison.Ordinal);
        return firstHeader >= 0 ? half[(firstHeader + 1)..] : half;
    }
}
