using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace DroboDashboardReborn;

/// <summary>
/// The type-the-name confirmation for deleting a share. The Delete button
/// only comes alive when the box matches the share's name exactly --
/// case-sensitive, no trimming -- and what was typed is what gets sent as
/// confirm_name, so the agent's own exact-match check sees the same string
/// the user proved they could type.
///
/// Same shape as NewShareDialog: the POST goes through a delegate back to
/// MainWindow's PostJson helper, success closes the dialog with the agent's
/// read-back in ResultMessage, a plain refusal shows the agent's error here
/// inline. A delete nobody could settle also closes -- the share may already
/// be gone, and a live Delete button under that warning is an invitation to
/// destroy something twice. One POST per click, never retried automatically.
/// </summary>
public partial class DeleteShareDialog : Window
{
    private readonly string _shareName;
    private readonly Func<string, Task<(WriteOutcome outcome, string message)>> _delete;

    /// <summary>The agent's message, verbatim (read-back suffix included).
    /// Only meaningful when ShowDialog returned true.</summary>
    public string ResultMessage { get; private set; } = "";

    /// <summary>Which outcome ResultMessage describes, so the Shares tab paints
    /// it green or amber rather than guessing. Only meaningful when ShowDialog
    /// returned true.</summary>
    public WriteOutcome ResultOutcome { get; private set; } = WriteOutcome.Confirmed;

    /// <param name="shareName">The share to delete, exactly as the device lists it.</param>
    /// <param name="delete">POSTs /api/shares/delete with the typed confirmation
    /// and returns (outcome, message) -- message is the agent's own wording
    /// whichever way it went.</param>
    public DeleteShareDialog(string shareName,
        Func<string, Task<(WriteOutcome outcome, string message)>> delete)
    {
        InitializeComponent();
        _shareName = shareName;
        _delete = delete;

        Header.Text = "Delete " + shareName;
        Sub.Text = "This tells the Drobo to remove the share itself, not just this app's view of it.";
        WarningText.Text = "Deleting " + shareName + " is expected to destroy everything stored "
            + "in it. It cannot be undone from this app.";
        ConfirmHint.Text = "Delete stays off until the box reads exactly " + shareName
            + " -- upper and lower case count.";

        Loaded += (_, _) => ConfirmBox.Focus();
    }

    private void ConfirmBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        ErrorText.Visibility = Visibility.Collapsed;
        DeleteBtn.IsEnabled = Matches();
    }

    /// <summary>Ordinal, case-sensitive -- the same comparison the agent's
    /// confirm_name check is pinned to.</summary>
    private bool Matches() => string.Equals(ConfirmBox.Text, _shareName, StringComparison.Ordinal);

    private async void Delete_Click(object sender, RoutedEventArgs e)
    {
        if (!Matches()) return;

        SetBusy(true);
        try
        {
            var (outcome, message) = await _delete(ConfirmBox.Text);
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

    private void SetBusy(bool busy)
    {
        // Leaving busy re-checks the match rather than flatly re-enabling
        // Delete, so the button's state always comes from the typed name.
        DeleteBtn.IsEnabled = !busy && Matches();
        CancelBtn.IsEnabled = !busy;
        ConfirmBox.IsEnabled = !busy;
        DeleteBtn.Content = busy ? "Deleting…" : "Delete share";
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
