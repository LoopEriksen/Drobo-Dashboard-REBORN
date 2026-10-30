using System;
using System.ComponentModel;
using System.Drawing;
using System.Windows;

namespace DroboDashboardReborn;

/// <summary>
/// Owns the notification-area (system tray) icon for the app's lifetime.
///
/// Kept entirely separate from MainWindow so the WinForms bits this needs
/// (NotifyIcon, ContextMenuStrip -- see &lt;UseWindowsForms&gt; in the csproj)
/// don't spread into the WPF window's own code. MainWindow only needs to:
///   - construct one of these once, after InitializeComponent
///   - call <see cref="UpdateStatus"/> whenever it refreshes device status
///   - call <see cref="Dispose"/> when the window actually closes
///
/// Behaviour, per the maintainer's ask:
///   - Tray icon present the whole time the app runs, tinted/badged the
///     same as the mark used everywhere else.
///   - Tooltip reflects the device name and overall health, refreshed from
///     data the app already polls -- nothing new is fetched here.
///   - Left-click restores/shows the window. Right-click: Open, Exit.
///   - Closing to the tray is OPT-IN (see DroboSettings.CloseToTrayEnabled,
///     default OFF) -- the maintainer has already complained twice about this
///     app being intrusive, so the window's X really closes it unless the
///     user turned this on themselves.
///   - The icon reflects health: it swaps to a badged variant while a
///     critical alert is active, using alert data the app already fetches.
///
/// NOTIFICATIONS -- the rule here changed on 2026-07-28, and how it changed
/// matters. This class used to say "no toasts, ever", written after the maintainer
/// twice reported the app being intrusive. The maintainer then NARROWED that rather
/// than reversing it: notifications are allowed, but only for alerts, each type
/// switchable on its own, and every one off until switched on.
///
/// So <see cref="ShowAlert"/> exists, and it is the only thing in this class
/// that can interrupt anyone. What it will never do is decide FOR itself: it
/// shows what AlertNotifier hands it, and AlertNotifier only ever hands over a
/// newly-active alert whose category the user switched on. A notification here
/// means something is actually wrong. Nothing about a routine poll can produce
/// one.
///
/// It still never steals focus -- a balloon tip is drawn by the shell without
/// activating the window, which is the whole reason it is the right mechanism
/// here rather than anything that raises a window.
/// </summary>
internal sealed class TrayIconManager : IDisposable
{
    /// <summary>NotifyIcon.Text throws if longer than this (a native shell
    /// limit), so the tooltip is cut to it.</summary>
    private const int MaxTooltipLength = 127;

    /// <summary>The shell truncates a balloon's text past about 255
    /// characters. A message longer than MaxBalloonTextLength is cut to
    /// TruncatedBalloonTextLength plus "..." here instead, so the cut lands
    /// where this code chose rather than wherever the shell's fell.</summary>
    private const int MaxBalloonTextLength = 240;
    private const int TruncatedBalloonTextLength = 237;

    /// <summary>How long a balloon is asked to stay up. Windows treats it as
    /// a hint and applies its own accessibility timing.</summary>
    private const int BalloonTimeoutMilliseconds = 10_000;

    private readonly Window _window;
    private readonly System.Windows.Forms.NotifyIcon _notifyIcon;
    private readonly Icon _normalIcon;
    private readonly Icon _alertIcon;

    // Set only by the tray menu's own "Exit" item, so Window_Closing can
    // tell "the user asked to actually quit" apart from "the user clicked
    // the X" -- those must behave differently whenever close-to-tray is on.
    private bool _exitRequested;
    private bool _disposed;

    /// <summary>Puts the icon in the tray straight away, for the whole life
    /// of the app, and hooks the window's Closing so close-to-tray can be
    /// honoured -- the hook is the only way to tell the X apart from the
    /// tray menu's Exit.</summary>
    public TrayIconManager(Window window)
    {
        _window = window;
        _normalIcon = LoadEmbeddedIcon("Assets/AppIcon.ico");
        _alertIcon = LoadEmbeddedIcon("Assets/AppIconAlert.ico");

        var menu = new System.Windows.Forms.ContextMenuStrip();
        menu.Items.Add("Open", null, (_, _) => ShowWindow());
        menu.Items.Add(new System.Windows.Forms.ToolStripSeparator());
        menu.Items.Add("Exit", null, (_, _) => ExitApp());

        _notifyIcon = new System.Windows.Forms.NotifyIcon
        {
            Icon = _normalIcon,
            Text = "Drobo Dashboard REBORN",
            ContextMenuStrip = menu,
            Visible = true,
        };
        _notifyIcon.MouseClick += (_, e) =>
        {
            if (e.Button == System.Windows.Forms.MouseButtons.Left)
                ShowWindow();
        };

        _window.Closing += Window_Closing;
    }

    /// <summary>
    /// Loads one of the two icons baked in as a WPF resource (see the
    /// &lt;Resource&gt; items in the csproj) via a pack URI, rather than a
    /// loose file on disk -- so it keeps working after the self-contained
    /// single-file publish build-installer.ps1 produces, where there is no
    /// loose Assets folder next to the .exe.
    /// </summary>
    private static Icon LoadEmbeddedIcon(string relativePath)
    {
        var uri = new Uri("pack://application:,,,/" + relativePath, UriKind.Absolute);
        var resourceInfo = System.Windows.Application.GetResourceStream(uri)
            ?? throw new InvalidOperationException($"Missing embedded icon resource: {relativePath}");
        using var stream = resourceInfo.Stream;
        return new Icon(stream);
    }

    /// <summary>
    /// Refreshes the tooltip and (if needed) swaps the badged icon in.
    /// Called on the same poll tick MainWindow already renders status and
    /// alerts on -- this never fetches anything itself.
    /// </summary>
    public void UpdateStatus(string deviceName, string overallState, bool hasCriticalAlert)
    {
        if (_disposed) return;

        var stateText = overallState switch
        {
            "ok" => "Healthy",
            "warning" => "Warning",
            "failed" => "Failed",
            "unreachable" => "Unreachable",
            _ => "Unknown",
        };

        // NotifyIcon.Text throws if longer than MaxTooltipLength (a native
        // shell limit) -- device names are short in practice, but truncate
        // rather than let a long one crash the next poll tick.
        var tooltip = $"{deviceName} — {stateText}";
        if (tooltip.Length > MaxTooltipLength) tooltip = tooltip[..MaxTooltipLength];
        _notifyIcon.Text = tooltip;
        _notifyIcon.Icon = hasCriticalAlert ? _alertIcon : _normalIcon;
    }

    /// <summary>
    /// Show one desktop notification for an alert.
    ///
    /// The caller has already decided this is worth showing (AlertNotifier);
    /// this only draws it. A balloon tip is rendered by the Windows shell as an
    /// ordinary notification, appearing in the Action Center like any other --
    /// and, importantly, WITHOUT activating or raising the window, so it cannot
    /// take what you were typing into.
    ///
    /// Severity picks the icon only. The text is the agent's own alert message,
    /// unedited: it was written to be read by a person and rewording it here
    /// would mean two different descriptions of the same fault depending on
    /// where you saw it.
    /// </summary>
    public void ShowAlert(string severity, string message)
    {
        if (_disposed || string.IsNullOrWhiteSpace(message)) return;

        var balloonIcon = severity switch
        {
            "critical" => System.Windows.Forms.ToolTipIcon.Error,
            "warning" => System.Windows.Forms.ToolTipIcon.Warning,
            _ => System.Windows.Forms.ToolTipIcon.Info,
        };
        var title = severity switch
        {
            "critical" => "Drobo: action needed",
            "warning" => "Drobo: warning",
            _ => "Drobo",
        };

        // The shell truncates past ~255 chars; trimming here means the cut
        // lands somewhere we chose rather than mid-word wherever it fell.
        var text = message.Length > MaxBalloonTextLength
            ? message[..TruncatedBalloonTextLength] + "..."
            : message;

        try
        {
            _notifyIcon.ShowBalloonTip(BalloonTimeoutMilliseconds, title, text, balloonIcon);
        }
        catch (Exception)
        {
            // A notification that cannot be drawn is not worth taking the app
            // down for. The alert is on the Alerts tab either way, which is the
            // authoritative place -- this was only the courtesy copy.
        }
    }

    /// <summary>Brings the window back from the tray or the taskbar. A
    /// minimised window is restored after Show and before Activate,
    /// because Show and Activate alone leave it minimised.</summary>
    private void ShowWindow()
    {
        _window.Show();
        if (_window.WindowState == WindowState.Minimized)
            _window.WindowState = WindowState.Normal;
        _window.Activate();
    }

    /// <summary>The tray menu's Exit: marks the close as a real one first,
    /// so Window_Closing lets it through even with close-to-tray on.</summary>
    private void ExitApp()
    {
        _exitRequested = true;
        _window.Close();
    }

    /// <summary>Turns the window's X into "hide to the tray", but only when
    /// the user switched close-to-tray on and the close did not come from
    /// the tray menu's Exit. The setting is read fresh each time, so a
    /// change on the Settings tab applies to the very next close.</summary>
    private void Window_Closing(object? sender, CancelEventArgs e)
    {
        if (_exitRequested) return; // a real exit was requested -- let it happen
        if (!DroboSettings.LoadAll().CloseToTrayEnabled) return; // opt-in only; default is "X really closes"

        e.Cancel = true;
        _window.Hide();
    }

    /// <summary>Removes the icon from the tray and releases both icons, in the
    /// order the comment below explains. A second call does nothing.</summary>
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;

        _window.Closing -= Window_Closing;

        // The classic leaked-tray-icon bug is exactly this line missing:
        // Visible must be set false before Dispose, or the icon lingers in
        // the notification area (invisible, but still occupying a slot)
        // until the user mouses over where it used to be.
        _notifyIcon.Visible = false;
        _notifyIcon.Dispose();
        _normalIcon.Dispose();
        _alertIcon.Dispose();
    }
}
