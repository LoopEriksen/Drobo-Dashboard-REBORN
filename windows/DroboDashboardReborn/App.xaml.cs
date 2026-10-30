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
    /// </summary>
    private void InstallLastChanceExceptionHandlers()
    {
        DispatcherUnhandledException += (_, args) =>
        {
            string newLine = Environment.NewLine;
            System.Windows.MessageBox.Show(
                "Something went wrong inside the app:" + newLine + newLine
                + args.Exception.Message + newLine + newLine
                + "The app will keep running. If this keeps happening, use "
                + "\"Copy diagnostics\" on the Tools tab and report it.",
                "Drobo Dashboard REBORN", MessageBoxButton.OK, MessageBoxImage.Warning);
            args.Handled = true;
        };
        // A faulted Task nobody awaited would otherwise surface only in the
        // finalizer, long after the cause. Observe it so it cannot escalate.
        TaskScheduler.UnobservedTaskException += (_, args) => args.SetObserved();
    }
}
