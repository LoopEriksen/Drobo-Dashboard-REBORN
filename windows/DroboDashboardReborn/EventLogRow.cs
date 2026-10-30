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
    /// <summary>The chip's label: the severity in capitals, or "LOG" when
    /// the agent gives the line none, so the chip is never blank.</summary>
    public string SeverityText { get; set; } = "";

    /// <summary>When the entry happened, as local wall-clock text
    /// (e.g. "2026-08-21 14:03:22").</summary>
    public string When { get; set; } = "";

    public string Message { get; set; } = "";

    /// <summary>The chip's colours, from the same severity table AlertRow
    /// uses, so a log line and an alert of one severity look alike.</summary>
    public Brush ChipBackground { get; set; } = Brushes.Transparent;
    public Brush SeverityBrush { get; set; } = Brushes.Gray;
}
