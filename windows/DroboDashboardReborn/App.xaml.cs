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

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        for (int i = 0; i < e.Args.Length - 1; i++)
        {
            if (string.Equals(e.Args[i], "--agent-config", StringComparison.OrdinalIgnoreCase)
                && !string.IsNullOrWhiteSpace(e.Args[i + 1]))
            {
                AgentConfigOverride = e.Args[i + 1];
                break;
            }
        }

        // Last line of defence. Twenty-odd event handlers in MainWindow are
        // `async void` (they have to be -- that is the shape WPF gives an
        // event handler), and an exception escaping one after its first
        // `await` is rethrown on the dispatcher, where the default outcome is
        // the Windows crash dialog and a dead app. The handlers catch what
        // they expect; this catches what they did not. It reports and keeps
        // the app alive rather than pretending nothing happened.
        DispatcherUnhandledException += (_, args) =>
        {
            string nl = Environment.NewLine;
            System.Windows.MessageBox.Show(
                "Something went wrong inside the app:" + nl + nl
                + args.Exception.Message + nl + nl
                + "The app will keep running. If this keeps happening, use "
                + "\"Copy diagnostics\" on the Tools tab and report it.",
                "Drobo Dashboard REBORN", MessageBoxButton.OK, MessageBoxImage.Warning);
            args.Handled = true;
        };
        // A faulted Task nobody awaited would otherwise surface only in the
        // finalizer, long after the cause. Observe it so it cannot escalate.
        TaskScheduler.UnobservedTaskException += (_, args) => args.SetObserved();

        // Shown before StartupUri constructs MainWindow, so it is on screen
        // during the expensive part rather than after it.
        Splash = new SplashWindow();
        Splash.Show();
    }
}
