using System.Windows;

namespace DroboDashboardReborn;

/// <summary>
/// Application entry point.
///
/// Its one job beyond the default is the splash screen: put something on screen
/// immediately, because the gap between double-clicking and the main window
/// appearing runs to several seconds (WPF startup, then an mDNS browse that is
/// slow by design), and several seconds of empty desktop is indistinguishable
/// from a program that failed to start.
/// </summary>
public partial class App : Application
{
    /// <summary>
    /// The splash, while it exists. MainWindow reports progress to it during
    /// startup and closes it once the window has actually rendered.
    ///
    /// Null once dismissed, and callers must null-check rather than assume:
    /// SplashWindow's own safety timer can close it at any moment, so "is it
    /// still there?" is a real question rather than a formality.
    /// </summary>
    internal static SplashWindow? Splash { get; set; }

    /// <summary>
    /// The agent config.json to take the address and token from, when a
    /// launcher names one with <c>--agent-config "path"</c>. Null otherwise.
    ///
    /// The app finds its token by looking for config files, and in a
    /// developer checkout the first one it finds is agent\config.json: the
    /// REAL agent's. The simulated launcher starts a different agent, on
    /// agent\config.mock.json with a different token, so the app arrived with
    /// the wrong token and every request was refused. The launcher knows which
    /// config it started, so now it says so. Found 2026-09-14.
    /// </summary>
    internal static string? AgentConfigOverride { get; private set; }

    /// <summary>
    /// Reads the launcher's arguments, installs the last-chance exception
    /// handlers, then shows the splash. The splash comes last but still
    /// before StartupUri constructs MainWindow, which is the slow part.
    /// </summary>
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        ReadAgentConfigArgument(e.Args);
        InstallLastChanceExceptionHandlers();

        // Shown before StartupUri constructs MainWindow, so it is on screen
        // during the expensive part rather than after it.
        Splash = new SplashWindow();
        Splash.Show();
    }

    /// <summary>
    /// Sets <see cref="AgentConfigOverride"/> from the first
    /// <c>--agent-config "path"</c> pair with a non-blank path. The last
    /// argument is never taken as the flag, because it has no value after it.
    /// </summary>
    private static void ReadAgentConfigArgument(string[] args)
    {
        for (int argIndex = 0; argIndex < args.Length - 1; argIndex++)
        {
            if (string.Equals(args[argIndex], "--agent-config", StringComparison.OrdinalIgnoreCase)
                && !string.IsNullOrWhiteSpace(args[argIndex + 1]))
            {
                AgentConfigOverride = args[argIndex + 1];
                break;
            }
        }
    }

    /// <summary>
    /// Last line of defence. Twenty-odd event handlers in MainWindow are
    /// <c>async void</c> (they have to be -- that is the shape WPF gives an
    /// event handler), and an exception escaping one after its first
    /// <c>await</c> is rethrown on the dispatcher, where the default outcome
    /// is the Windows crash dialog and a dead app. The handlers catch what
    /// they expect; this catches what they did not. It reports and keeps
    /// the app alive rather than pretending nothing happened.
    ///
    /// Since 2026-11-20 every one of them writes the whole exception to
    /// app.log (AppErrorLog), and the dialog shows the whole of it too: until
    /// then it showed the message alone, one sentence with no clue where it
    /// came from, and an unobserved task fault was dropped without a trace.
    /// </summary>
    private void InstallLastChanceExceptionHandlers()
    {
        DispatcherUnhandledException += (_, args) =>
        {
            ReportUnexpectedError(args.Exception, "UI thread", appKeepsRunning: true);
            args.Handled = true;
        };
        // An exception on a thread of the app's own, outside the dispatcher.
        // The runtime ends the process after this whatever the handler does,
        // so the most it can do is write it down and say so first.
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            if (args.ExceptionObject is Exception exception)
                ReportUnexpectedError(exception, "background thread", appKeepsRunning: !args.IsTerminating);
        };
        // A faulted Task nobody awaited surfaces only in the finalizer, long
        // after the cause. Observed so it cannot escalate, and logged so it
        // is not lost; no dialog, because it arrives at a random later moment
        // with nothing on screen to connect it to.
        // This runs on the finalizer thread, where an exception of its own
        // would end the process, so even the formatting is inside the try.
        TaskScheduler.UnobservedTaskException += (_, args) =>
        {
            try
            {
                AppErrorLog.Append(AppErrorLog.FormatEntry(
                    args.Exception, "unobserved task", AppErrorLog.AppVersion(), DateTimeOffset.Now));
            }
            catch
            {
                // Logging must never become the failure.
            }
            args.SetObserved();
        };
    }

    /// <summary>The dialog shows at most this much of the error text; a
    /// deep chain of inner exceptions would otherwise make a message box
    /// taller than the screen. The log always holds all of it.</summary>
    private const int MaxErrorCharactersShown = 6000;

    /// <summary>
    /// Writes the error to app.log, then shows it whole: type, message and
    /// stack, which a standard Windows message box copies with Ctrl+C. The
    /// dialog says where the log is only when the write worked. Never throws:
    /// a failure inside a last-chance handler would be a second crash.
    ///
    /// The dialog's copy is masked (MaskedForPosting) because the dialog asks
    /// for it to be pasted into a public report, and an exception's message
    /// often quotes a path inside the Windows user's profile folder, which
    /// carries the user name, or a \\server\share. app.log keeps the full
    /// text, for the tester's own use.
    /// </summary>
    private static void ReportUnexpectedError(Exception exception, string caughtBy, bool appKeepsRunning)
    {
        try
        {
            var entry = AppErrorLog.FormatEntry(exception, caughtBy, AppErrorLog.AppVersion(), DateTimeOffset.Now);
            bool logged = AppErrorLog.Append(entry);
            System.Windows.MessageBox.Show(
                DescribeForDialog(MaskedForPosting(entry), entry, logged, appKeepsRunning),
                "Drobo Dashboard REBORN", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        catch
        {
            // Nothing further can be done from here.
        }
    }

    /// <summary>
    /// The entry with names, addresses, serials and the Windows user name
    /// replaced by placeholders, the remembered Drobo's values included.
    /// Null when masking itself failed; the dialog then shows the full text
    /// and asks for it to be read before it is posted, rather than show
    /// nothing at all.
    /// </summary>
    private static string? MaskedForPosting(string entry)
    {
        DroboSettings.RememberedDevice? rememberedDevice = null;
        try
        {
            rememberedDevice = DroboSettings.LoadAll().Remembered;
        }
        catch
        {
            // settings.json may be what failed; mask the shapes alone.
        }
        try
        {
            return DiagnosticsMasking.MaskText(entry, rememberedDevice);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>The dialog's text: what happened to the app, the error
    /// itself, and the next step. <paramref name="maskedEntry"/> is null
    /// when the entry could not be masked; the full entry is shown then,
    /// with a warning to read it first.</summary>
    private static string DescribeForDialog(string? maskedEntry, string entry, bool logged, bool appKeepsRunning)
    {
        string newLine = Environment.NewLine;
        var text = maskedEntry ?? entry;
        var shown = text.Length > MaxErrorCharactersShown
            ? text[..MaxErrorCharactersShown] + newLine + "... (the rest is in app.log)"
            : text;
        // Named by its variable rather than its full path, which would put
        // the Windows user name back into the text a tester copies.
        var where = logged
            ? @"The full text, unmasked, was saved to %LOCALAPPDATA%\DroboDashboardReborn\app.log." + newLine
            : "It could not be saved to app.log, so copy it now." + newLine;
        var posting = maskedEntry is not null
            ? "Names, addresses and the Windows user name above are replaced with placeholders "
              + "such as USER-1, so you can paste it into a tester report; read it first all the same."
            : "It could not be checked for names and addresses, so read it before you post it.";
        return "Something went wrong inside the app."
               + (appKeepsRunning ? " The app will keep running." : " The app has to close.")
               + newLine + newLine + shown.TrimEnd() + newLine + newLine
               + "Press Ctrl+C to copy this message. " + where + posting + newLine
               + "If this keeps happening, add \"Copy diagnostics\" from the Tools tab to the report.";
    }
}
