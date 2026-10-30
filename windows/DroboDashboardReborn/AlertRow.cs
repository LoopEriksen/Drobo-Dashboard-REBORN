using System.Windows.Media;

namespace DroboDashboardReborn;

/// <summary>
/// One alert row. The same class covers two different lists: the Status
/// tab's alerts panel (active alerts only, as before) and the new Alerts
/// popup's "Active"/"Resolved" groups (see MainWindow.RenderAlerts) --
/// ClearedText and Active are simply unused/blank for the Status tab's
/// purposes and populated only when the row is built for the popup.
/// </summary>
public sealed class AlertRow
{
    /// <summary>Stable id from the agent (e.g. "drive-failed-3"), used to tell
    /// which alerts in the "all" history are the same ones still showing as active.</summary>
    public string Key { get; set; } = "";

    /// <summary>The agent's severity word exactly as sent ("critical",
    /// "warning", "info"), kept as-is because it is the key the rows are
    /// sorted by (worst first) and what a desktop notification is given.</summary>
    public string Severity { get; set; } = "info";

    /// <summary>The severity as the chip shows it (upper-case). Separate from
    /// <see cref="Severity"/> so display wording never changes the sort key.</summary>
    public string SeverityText { get; set; } = "";

    /// <summary>The agent's message text, shown as it arrives.</summary>
    public string Message { get; set; } = "";

    /// <summary>Display text such as "since 3m ago", not a time value: the
    /// sortable time is <see cref="SortTimeUnixSeconds"/>. The XAML binds
    /// to this name, so it keeps it.</summary>
    public string Since { get; set; } = "";

    /// <summary>True when the alert is in the agent's active list (the
    /// "Active" group), false when it is only in the history ("Resolved").
    /// Nothing binds to it today; it records which group the row was built
    /// for.</summary>
    public bool Active { get; set; } = true;

    /// <summary>"cleared 3m ago" -- blank for an alert that's still active.</summary>
    public string ClearedText { get; set; } = "";

    /// <summary>first_seen for an active alert, cleared_at for a resolved one.
    /// Not shown anywhere -- only used to sort newest-first within a severity band.</summary>
    public double SortTimeUnixSeconds { get; set; }

    /// <summary>The severity chip's fill. Computed by MainWindow per
    /// severity so the colours live in one place, not in each template.</summary>
    public Brush ChipBackground { get; set; } = Brushes.Transparent;

    /// <summary>The severity chip's text colour, paired with
    /// <see cref="ChipBackground"/> for contrast.</summary>
    public Brush SeverityBrush { get; set; } = Brushes.Gray;
}
