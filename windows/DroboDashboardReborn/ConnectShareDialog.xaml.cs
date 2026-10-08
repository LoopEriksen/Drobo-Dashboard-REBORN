using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;

namespace DroboDashboardReborn;

/// <summary>
/// "Connect as..." -- asks for a Drobo username and password and hands them
/// straight to Windows via SmbConnections.Connect.
///
/// The password lives only in PasswordInput.Password (the PasswordBox's own
/// property) and a single local variable for the moment it takes to make the
/// P/Invoke call. It is never bound to a view-model string, never logged, and
/// never written to config.json or sent to the agent -- see the
/// P/Invoke-not-`net use` note on SmbConnections for why that matters.
///
/// Changed 2026-09-08: with "Save this login on this PC" ticked, the password
/// is additionally handed to Windows' Credential Manager through
/// <see cref="CredentialStore"/>, which is the one place it is persisted and
/// the only thing this dialog writes anywhere. Untick the box and the older
/// behaviour is unchanged: nothing outlives the session. This class still
/// writes no file of its own, and the password still never reaches a log, a
/// command line, this app's settings.json, or the agent.
/// </summary>
public partial class ConnectShareDialog : Window
{
    private readonly string _uncPath;
    private readonly string? _guestAdviceDetail;

    /// <summary>The server the credential is keyed to: one Drobo, not one
    /// share. Null when the path is not a UNC path, in which case saving is
    /// simply not offered rather than half-working.</summary>
    private readonly string? _host;

    /// <param name="shareName">Shown in the dialog header.</param>
    /// <param name="uncPath">The share to connect to, e.g. \\10.0.0.5\Documents.</param>
    /// <param name="suggestedDriveLetter">Pre-selected in the drive-letter list; null if
    /// none are free.</param>
    /// <param name="guestAdviceDetail">The agent's explanation of the guest-logon
    /// problem for this device, reused if Windows comes back with access denied.</param>
    /// <summary>Set when the agent runs the simulator: the login is checked by
    /// the simulated Drobo instead of Windows. Null for real hardware.</summary>
    private readonly Func<string?, string, string, Task<int>>? _simulatedSignIn;

    public ConnectShareDialog(string shareName, string uncPath, string? suggestedDriveLetter, string? guestAdviceDetail,
                              Func<string?, string, string, Task<int>>? simulatedSignIn = null)
    {
        InitializeComponent();
        _uncPath = uncPath;
        _guestAdviceDetail = guestAdviceDetail;
        _simulatedSignIn = simulatedSignIn;

        Header.Text = "Connect to " + shareName;
        Sub.Text = uncPath;

        // Free letters only, Z downward. "Mount or not" is the checkbox now,
        // so the list no longer carries a "(none)" entry pretending to be a
        // letter. It stays disabled until the user opts into mounting.
        var used = new HashSet<char>(
            DriveInfo.GetDrives().Select(d => char.ToUpperInvariant(d.Name[0])));
        for (char c = 'Z'; c >= 'D'; c--)
            if (!used.Contains(c))
                DriveLetterBox.Items.Add(c + ":");

        if (DriveLetterBox.Items.Count == 0)
        {
            // No free letter at all -- mounting is impossible, say so instead
            // of offering an empty list.
            MountCheck.IsEnabled = false;
            MountCheck.Content = "Mount as a drive (no free drive letters on this PC)";
        }
        else
        {
            DriveLetterBox.SelectedItem = !string.IsNullOrEmpty(suggestedDriveLetter)
                && DriveLetterBox.Items.Contains(suggestedDriveLetter)
                    ? suggestedDriveLetter
                    : DriveLetterBox.Items[0];
        }

        // A login already saved for this Drobo: prefill the username, say so,
        // and offer a way out. Windows never returns the password, so the
        // password box stays empty -- and an empty box with a saved login is
        // meaningful, see Connect_Click.
        _host = CredentialStore.HostOf(uncPath);
        // Never consult Credential Manager for the simulator: its address is
        // made up, and could match a real machine that has a real saved login.
        var savedUser = _host is null || _simulatedSignIn is not null
            ? null : CredentialStore.SavedUserName(_host);
        if (savedUser is not null)
        {
            if (savedUser.Length > 0) UsernameBox.Text = savedUser;
            Remember.IsChecked = true;
            RememberNote.Text = savedUser.Length > 0
                ? $"A login for {_host} is already saved on this PC, as {savedUser}. "
                  + "Leave the password blank to use it, or type one to replace it."
                : $"A login for {_host} is already saved on this PC. "
                  + "Leave the password blank to use it, or type one to replace it.";
            RememberNote.Visibility = Visibility.Visible;
            ForgetLink.Visibility = Visibility.Visible;
        }
        else if (_host is null)
        {
            // Nothing to key a credential to. Say so rather than showing a
            // box that would quietly do nothing.
            Remember.IsEnabled = false;
            Remember.IsChecked = false;
            RememberNote.Text = "This share is not a \\\\server\\share path, so there is no "
                              + "server name to save a login against.";
            RememberNote.Visibility = Visibility.Visible;
        }

        if (_simulatedSignIn is not null)
        {
            Remember.IsEnabled = false;
            Remember.IsChecked = false;
            ForgetLink.Visibility = Visibility.Collapsed;
            RememberNote.Text = "This is the simulated Drobo. The simulator checks the login and "
                              + "nothing is saved to Windows. Its account is user admin, password admin.";
            RememberNote.Visibility = Visibility.Visible;
        }

        Loaded += (_, _) =>
        {
            if (UsernameBox.Text.Length > 0) PasswordInput.Focus();
            else UsernameBox.Focus();
        };
    }

    /// <summary>
    /// Removes the saved login for this Drobo, at the user's request. The
    /// share stays connected if it already is: forgetting a saved password is
    /// about what happens NEXT time, and disconnecting someone's open share
    /// as a side effect of a "forget" link would be a surprise.
    /// </summary>
    private void Forget_Click(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (_host is null) return;
        if (CredentialStore.Forget(_host))
        {
            RememberNote.Text = $"The saved login for {_host} has been removed from this PC.";
            RememberNote.Visibility = Visibility.Visible;
            ForgetLink.Visibility = Visibility.Collapsed;
            Remember.IsChecked = false;
            PasswordInput.Clear();
        }
        else
        {
            ShowError("Windows would not remove the saved login. You can remove it by hand "
                    + "in Control Panel, under Credential Manager.");
        }
    }

    /// <summary>The drive-letter list is only meaningful when mounting is wanted.</summary>
    private void MountCheck_Changed(object sender, RoutedEventArgs e)
    {
        // This used to also gate the "Save this login" box, because the only
        // way to persist anything was a flag Windows applies to mapped drives.
        // CredentialStore removed that dependency, so mounting and saving are
        // now genuinely independent choices and this handler minds only the
        // drive letter.
        DriveLetterBox.IsEnabled = MountCheck.IsChecked == true;
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        var username = UsernameBox.Text.Trim();
        var password = PasswordInput.Password;

        if (string.IsNullOrEmpty(username))
        {
            ShowError("Enter the username for your Drobo account.");
            return;
        }

        // An empty password with a login already saved is not a mistake: it
        // means "use the one you have". Windows will not give the password
        // back to us, so we hand it null for both fields and let Windows
        // supply the saved credential itself -- the same path File Explorer
        // takes. Typing a password instead replaces the saved one below.
        var usingSavedLogin = password.Length == 0
                              && _simulatedSignIn is null
                              && _host is not null
                              && CredentialStore.HasSavedLogin(_host);

        if (password.Length == 0 && !usingSavedLogin)
        {
            ShowError("Enter the password for your Drobo account.");
            return;
        }

        // A drive letter only if the user asked to mount. Unchecked, the
        // login opens a session with no local name -- Explorer and the in-app
        // browser can both use the UNC path once Windows holds that session.
        string? driveLetter = null;
        if (MountCheck.IsChecked == true && DriveLetterBox.SelectedItem is string letter
            && letter.Length == 2 && letter[1] == ':')
        {
            driveLetter = letter;
        }
        bool remember = Remember.IsChecked == true;

        SetBusy(true);
        try
        {
            // Off the UI thread: WNetAddConnection2 is a blocking network call and
            // can take several seconds against a slow or unreachable device.
            //
            // CONNECT_UPDATE_PROFILE still rides along when a drive letter is
            // being mapped and the user asked to remember: that is what makes
            // the DRIVE come back at sign-in, which is a separate thing from
            // the credential being stored.
            int code = _simulatedSignIn is not null
                ? await _simulatedSignIn(driveLetter, username, password)
                : await Task.Run(
                    () => usingSavedLogin
                        ? SmbConnections.Connect(_uncPath, driveLetter, null, null, remember)
                        : SmbConnections.Connect(_uncPath, driveLetter, username, password, remember));

            if (code == 0)
            {
                // Only after the credential is known to WORK. Saving a
                // password that was just rejected would leave the user with a
                // stored login that fails every time and no clue why.
                if (remember && !usingSavedLogin && _host is not null && _simulatedSignIn is null)
                {
                    var problem = CredentialStore.Save(_host, username, password);
                    if (problem is not null)
                    {
                        // The share IS connected; only the saving failed. Say
                        // exactly that and stay open, rather than reporting a
                        // failure that did not happen.
                        ShowError(problem);
                        SavedWithoutStoring = true;
                        return;
                    }
                }

                DialogResult = true;
                Close();
                return;
            }
            // The guest-logon advice describes Windows refusing a real Drobo;
            // it would be wrong about a simulated access-denied.
            ShowError(SmbConnections.DescribeError(code, _simulatedSignIn is null ? _guestAdviceDetail : null));
        }
        finally
        {
            SetBusy(false);
        }
    }

    /// <summary>True when the share connected but the login could not be
    /// saved. The dialog stays open showing why; the caller can still treat
    /// the share as connected.</summary>
    public bool SavedWithoutStoring { get; private set; }

    private void SetBusy(bool busy)
    {
        ConnectBtn.IsEnabled = !busy;
        CancelBtn.IsEnabled = !busy;
        ConnectBtn.Content = busy ? "Connecting…" : "Connect";
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
