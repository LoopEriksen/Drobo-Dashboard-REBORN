using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace DroboDashboardReborn;

/// <summary>
/// "New share..." -- asks for a name, checks it against the agent's own name
/// rules as you type, and hands the POST back to MainWindow through a
/// delegate (so the one PostJson helper, with its token header and error
/// shape, stays the only HTTP path in the app).
///
/// On success the dialog closes with DialogResult true, ResultMessage carrying
/// the agent's message verbatim and ResultOutcome saying which kind of success
/// it was; MainWindow shows it on the Shares tab and refreshes the list. A
/// write nobody could settle closes it the same way, deliberately: the share
/// may already exist, so the useful next thing is the refreshed list on the
/// Shares tab, not a Create button sitting under a warning inviting a second
/// attempt. Only a plain refusal stays here, inline, so the name can be fixed
/// and tried again. One POST per click, never retried automatically.
/// </summary>
public partial class NewShareDialog : Window
{
    /// <summary>The rules line shown in grey whenever there is no specific
    /// problem to point at. Same wording the problems below elaborate on.</summary>
    private const string RulesText =
        "1-27 characters. Start with a letter or digit; after that letters, "
        + "digits, spaces, underscores and hyphens.";

    /// <summary>The longest name the agent accepts. RulesText, the "at most"
    /// message below and NameBox's MaxLength in NewShareDialog.xaml all say
    /// 27 too, so a change here means changing all four.</summary>
    private const int MaxShareNameLength = 27;

    private static readonly Brush HintMutedBrush = new SolidColorBrush(Color.FromRgb(0x6B, 0x76, 0x83));
    private static readonly Brush HintProblemBrush = new SolidColorBrush(Color.FromRgb(0xE2, 0x56, 0x4B));

    private readonly IReadOnlyCollection<string> _existingNames;
    private readonly Func<string, Task<(WriteOutcome outcome, string message)>> _createShare;

    /// <summary>The agent's message, verbatim (read-back suffix included).
    /// Only meaningful when ShowDialog returned true.</summary>
    public string ResultMessage { get; private set; } = "";

    /// <summary>Which outcome ResultMessage describes, so the Shares tab paints
    /// it green or amber rather than guessing. Only meaningful when ShowDialog
    /// returned true.</summary>
    public WriteOutcome ResultOutcome { get; private set; } = WriteOutcome.Confirmed;

    /// <param name="existingNames">Share names currently listed, for the
    /// duplicate check. The agent re-checks against the device's own list, so
    /// a stale copy here only costs one round trip, never a wrong write.</param>
    /// <param name="createShare">POSTs /api/shares/create for the given name and
    /// returns (outcome, message) -- message is the agent's own wording
    /// whichever way it went.</param>
    public NewShareDialog(IReadOnlyCollection<string> existingNames,
        Func<string, Task<(WriteOutcome outcome, string message)>> createShare)
    {
        InitializeComponent();
        _existingNames = existingNames;
        _createShare = createShare;
        UpdateValidationState();
        Loaded += (_, _) => NameBox.Focus();
    }

    /// <summary>
    /// Client-side mirror of the agent's pinned name validation (1-27 chars,
    /// first alphanumeric, rest alphanumeric/space/underscore/hyphen, no
    /// case-insensitive duplicate), so obvious rejects never leave the app.
    /// The agent's own check remains the authority; this must only ever be
    /// as strict or stricter, never looser. ASCII on purpose -- that is the
    /// alphabet the agent's rule means. Returns null when the name is fine.
    /// </summary>
    internal static string? FindShareNameProblem(string name, IEnumerable<string> existingNames)
    {
        if (name.Length == 0) return "Enter a name.";
        if (name.Length > MaxShareNameLength) return "The name can be at most 27 characters.";
        if (!char.IsAsciiLetterOrDigit(name[0]))
            return "The name must start with a letter or digit.";
        foreach (var character in name)
            if (!char.IsAsciiLetterOrDigit(character) && character != ' ' && character != '_' && character != '-')
                return $"'{character}' is not allowed -- letters, digits, spaces, underscores and hyphens only.";
        foreach (var existing in existingNames)
            if (string.Equals(existing, name, StringComparison.OrdinalIgnoreCase))
                return $"There is already a share called {existing} -- names that differ only "
                     + "in upper/lower case count as taken.";
        return null;
    }

    /// <summary>Re-checks the name on every keystroke, so Create is never
    /// enabled for a name the agent would refuse.</summary>
    private void NameBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        // Whatever the agent said about the previous attempt no longer
        // describes what is in the box.
        ErrorText.Visibility = Visibility.Collapsed;
        UpdateValidationState();
    }

    /// <summary>Sets the hint line and the Create button from the current
    /// text. An empty box shows the rules in grey rather than shouting
    /// "Enter a name." at someone who has not typed yet.</summary>
    private void UpdateValidationState()
    {
        var name = NameBox.Text;
        var problem = FindShareNameProblem(name, _existingNames);
        bool showProblem = problem is not null && name.Length != 0;
        NameHint.Text = showProblem ? problem : RulesText;
        NameHint.Foreground = showProblem ? HintProblemBrush : HintMutedBrush;
        CreateBtn.IsEnabled = problem is null;
    }

    /// <summary>
    /// Sends the one POST for this click. A plain refusal (Failed) keeps the
    /// dialog open with the agent's message, so the name can be fixed and
    /// tried again. Every other outcome closes it, an unsettled one included:
    /// the share may already exist, and the refreshed list on the Shares tab
    /// is what shows whether it does -- a Create button under a warning
    /// would only invite a second, duplicate attempt.
    /// </summary>
    private async void Create_Click(object sender, RoutedEventArgs e)
    {
        var name = NameBox.Text;
        if (FindShareNameProblem(name, _existingNames) is not null) return;

        SetBusy(true);
        try
        {
            var (outcome, message) = await _createShare(name);
            if (outcome == WriteOutcome.Failed)
            {
                ShowError(message);
                return;
            }
            ResultOutcome = outcome;
            ResultMessage = message;
            DialogResult = true;
            Close();
        }
        finally
        {
            SetBusy(false);
        }
    }

    /// <summary>Locks the dialog while the POST is in flight, so the name
    /// cannot change under a request already sent and Create cannot be
    /// clicked twice.</summary>
    private void SetBusy(bool busy)
    {
        // Leaving busy re-runs UpdateValidationState rather than flatly
        // re-enabling Create, so the button's state always comes from the
        // rules.
        if (busy) CreateBtn.IsEnabled = false; else UpdateValidationState();
        CancelBtn.IsEnabled = !busy;
        NameBox.IsEnabled = !busy;
        CreateBtn.Content = busy ? "Creating…" : "Create";
        if (busy) ErrorText.Visibility = Visibility.Collapsed;
    }

    private void ShowError(string message)
    {
        ErrorText.Text = message;
        ErrorText.Visibility = Visibility.Visible;
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }
}
