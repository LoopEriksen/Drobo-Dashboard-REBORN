namespace DroboDashboardReborn;

/// <summary>
/// What one alert row puts on the screen: its key, its severity word, its
/// message and its two relative times. Two rows with the same text look the
/// same, whatever else differs between them (the sort time, for one, which
/// is never shown).
///
/// Why it exists: since 2026-11-25 an alert's message is a CopyableText (a
/// read-only TextBox), so a tester can select it and copy it into a report.
/// The Alerts tab used to empty its lists and fill them again on every
/// 10-second status poll, which did no harm to plain text but destroyed
/// every message box, and with it the tester's selection and keyboard
/// focus, every 10 seconds: Ctrl+C pressed after a poll copied nothing and
/// left the old clipboard in place. The lists are now left alone while the
/// rows read the same. "since 3m ago" counts in whole minutes, so an
/// unchanged list is rebuilt at most about once a minute.
///
/// Kept apart from AlertRow, which carries WPF brushes, so the rule can be
/// checked without a window (see DroboDashboardReborn.Tests).
/// </summary>
public readonly record struct AlertRowText(
    string Key, string Severity, string Message, string Since, string ClearedText)
{
    /// <summary>True when <paramref name="freshRows"/> would show exactly
    /// what <paramref name="shownRows"/> shows, row for row and in the same
    /// order. Compared ordinally: a change of case in a message is a
    /// change.</summary>
    public static bool SameInOrder(IReadOnlyList<AlertRowText> shownRows,
                                   IReadOnlyList<AlertRowText> freshRows)
    {
        if (shownRows.Count != freshRows.Count) return false;
        for (int index = 0; index < shownRows.Count; index++)
        {
            if (shownRows[index] != freshRows[index]) return false;
        }
        return true;
    }
}
