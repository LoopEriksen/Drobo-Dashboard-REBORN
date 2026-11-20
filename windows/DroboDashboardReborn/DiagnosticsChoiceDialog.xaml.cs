using System.Windows;

namespace DroboDashboardReborn;

/// <summary>
/// "Copy diagnostics" -> asks once whether the result should go to the
/// clipboard or to a file, with an optional "remember my choice" that
/// CopyDiagnostics_Click persists via DroboSettings so the user isn't
/// asked again every time -- see the maintainer's note on MainWindow.xaml.cs
/// about not wanting to be nagged for something this small.
/// </summary>
public partial class DiagnosticsChoiceDialog : Window
{
    /// <summary>Where the user wants the report. Cancelled is a value of its
    /// own so a closed window can never be read as a destination.</summary>
    public enum Choice { Cancelled, Clipboard, File }

    /// <summary>The user's answer. Starts as Cancelled, so closing the window
    /// any other way than a button still means "do nothing".</summary>
    public Choice Result { get; private set; } = Choice.Cancelled;

    /// <summary>Whether "Remember my choice" was ticked. Read live from the
    /// checkbox, and only acted on by the caller when a destination was
    /// chosen.</summary>
    public bool RememberChoice => RememberCheck.IsChecked == true;

    /// <summary>The buttons and checkbox are all declared in the XAML. The
    /// note under the buttons says which copy this is: the masked one is safe
    /// to post, the unmasked one is not, and the destination question is the
    /// moment the tester decides where it goes.</summary>
    public DiagnosticsChoiceDialog(bool masked = true)
    {
        InitializeComponent();
        if (masked) return;
        Title = "Unmasked copy";
        PrivacyNote.Text = "This is the UNMASKED copy: it includes the Drobo's serial number, name and "
                           + "address and your drive serial numbers. Keep it for your own use; to post "
                           + "diagnostics, use \"Copy diagnostics\" instead.";
    }

    /// <summary>Answers "clipboard" and closes with DialogResult true.</summary>
    private void Clipboard_Click(object sender, RoutedEventArgs e)
    {
        Result = Choice.Clipboard;
        DialogResult = true;
        Close();
    }

    /// <summary>Answers "file" and closes with DialogResult true; the caller
    /// then asks where to save it.</summary>
    private void File_Click(object sender, RoutedEventArgs e)
    {
        Result = Choice.File;
        DialogResult = true;
        Close();
    }

    /// <summary>Closes with no destination; the caller does nothing.</summary>
    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        Result = Choice.Cancelled;
        DialogResult = false;
        Close();
    }
}
