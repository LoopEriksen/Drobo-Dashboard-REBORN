using System.Windows.Media;

namespace DroboDashboardReborn;

/// <summary>
/// One line of the Drobo's own event log (/api/eventlog), ready for the
/// Tools tab's EVENT LOG card. Wears the same severity chip as an AlertRow
/// on purpose -- "warning" must look the same everywhere in the app -- but
/// stays its own type: an alert is the agent's judgement with a lifecycle
/// (active/cleared), a log line is a moment that already happened.
/// </summary>
public sealed class EventLogRow
{
    public string SeverityText { get; set; } = "";

    /// <summary>When the entry happened, as local wall-clock text
    /// (e.g. "2026-08-21 14:03:22").</summary>
    public string When { get; set; } = "";

    public string Message { get; set; } = "";

    public Brush ChipBackground { get; set; } = Brushes.Transparent;
    public Brush SeverityBrush { get; set; } = Brushes.Gray;
}
