using System.Windows;
using System.Windows.Media;

namespace DroboDashboardReborn;

/// <summary>
/// One row of software installed ON the Drobo itself (a DroboApp: an add-on package that runs on the device),
/// bound into the ItemsControl on the Tools tab -- see
/// MainWindow.RefreshDroboApps/RenderDroboApps and GET /api/droboapps.
///
/// Running/HasWebUi are already the plain booleans a person would say out
/// loud: the agent flips the device's own inverted "Stopped" field before
/// this ever sees it (see droboapps.py's parse_droboapps), so nothing here
/// re-inverts anything or treats a false value as "missing".
/// </summary>
public sealed class DroboAppRow
{
    /// <summary>The app's name as the device lists it; also what a start or
    /// stop request names, so it is never reworded.</summary>
    public string Name { get; set; } = "";

    /// <summary>The version string the device reports, shown as-is.</summary>
    public string Version { get; set; } = "";

    /// <summary>The app's own description from the device, shown as-is.</summary>
    public string Description { get; set; } = "";

    /// <summary>The device's own free-text status line (e.g. "Listening on port
    /// 22."), separate from Running -- often empty, and shown only when it
    /// actually says something.</summary>
    public string Status { get; set; } = "";

    /// <summary>Collapsed when <see cref="Status"/> is empty, so a silent app
    /// leaves no blank line.</summary>
    public Visibility StatusVisibility { get; set; } = Visibility.Collapsed;

    /// <summary>Whether the app is running, already in plain terms (see the
    /// class remarks). The chip and <see cref="ActionVerb"/> are derived from
    /// it when the row is built.</summary>
    public bool Running { get; set; }

    /// <summary>The chip's word, "RUNNING" or "STOPPED". Defaults to
    /// STOPPED so a row built without a state never claims to be running.</summary>
    public string RunningText { get; set; } = "STOPPED";

    /// <summary>The chip's text colour: green when running, muted when not.</summary>
    public Brush RunningBrush { get; set; } = Brushes.Gray;

    /// <summary>The chip's fill, paired with <see cref="RunningBrush"/>.</summary>
    public Brush RunningChipBackground { get; set; } = Brushes.Transparent;

    /// <summary>Whether the device says the app has its own web page.</summary>
    public bool HasWebUi { get; set; }

    /// <summary>Shows the "WEB UI" chip only for apps that have one.</summary>
    public Visibility WebUiVisibility { get; set; } = Visibility.Collapsed;

    /// <summary>The row's action button label, which is also the exact verb
    /// sent to POST /api/droboapps/action -- "Stop" for a running app, "Start"
    /// for a stopped one. The endpoint's verbs are case-sensitive and only
    /// these two are confirmed, so the label and the wire value are kept as
    /// one string on purpose.</summary>
    public string ActionVerb { get; set; } = "Start";
}
