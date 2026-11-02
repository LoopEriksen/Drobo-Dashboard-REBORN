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
    /// <summary>The share this dialog connects, e.g. \\10.0.0.5\Documents.</summary>
    private readonly string _uncPath;

    /// <summary>The agent's guest-logon explanation for this device, kept to
    /// add to an access-denied message from Windows. Null when the agent has
    /// none.</summary>
    private readonly string? _guestAdviceDetail;

    /// <summary>The server the credential is keyed to: one Drobo, not one
    /// share. Null when the path is not a UNC path, in which case saving is
    /// simply not offered rather than half-working.</summary>
    private readonly string? _credentialHost;

    /// <summary>Set when the agent runs the simulator: the login is checked by
    /// the simulated Drobo instead of Windows. Null for real hardware.</summary>
    private readonly Func<string?, string, string, Task<int>>? _simulatedSignIn;

    /// <summary>
    /// Builds the dialog for one share: the free drive letters, what is
    /// already saved for this Drobo, and the simulator's own note. The
    /// simulator note is applied last on purpose: it overrides whatever the
    /// saved-login step showed, since nothing is saved for a made-up address.
    /// </summary>
    /// <param name="shareName">Shown in the dialog header.</param>
    /// <param name="uncPath">The share to connect to, e.g. \\10.0.0.5\Documents.</param>
    /// <param name="suggestedDriveLetter">Pre-selected in the drive-letter list; null if
    /// none are free.</param>
    /// <param name="guestAdviceDetail">The agent's explanation of the guest-logon
    /// problem for this device, reused if Windows comes back with access denied.</param>
    /// <param name="simulatedSignIn">The simulator's login check, or null for
    /// real hardware.</param>
    public ConnectShareDialog(string shareName, string uncPath, string? suggestedDriveLetter, string? guestAdviceDetail,
                              Func<string?, string, string, Task<int>>? simulatedSignIn = null)
    {
        InitializeComponent();
        _uncPath = uncPath;
        _guestAdviceDetail = guestAdviceDetail;
        _simulatedSignIn = simulatedSignIn;

        Header.Text = "Connect to " + shareName;
        SubtitleText.Text = uncPath;

        FillFreeDriveLetters(suggestedDriveLetter);

        // A login already saved for this Drobo: prefill the username, say so,
        // and offer a way out. Windows never returns the password, so the
        // password box stays empty -- and an empty box with a saved login is
        // meaningful, see Connect_Click.
        _credentialHost = CredentialStore.HostOf(uncPath);
        ShowSavedLoginState();

        if (_simulatedSignIn is not null) ShowSimulatorNote();

        Loaded += (_, _) =>
        {
            if (UsernameBox.Text.Length > 0) PasswordInput.Focus();
            else UsernameBox.Focus();
        };
    }

    /// <summary>
    /// Free letters only, Z downward. "Mount or not" is the checkbox now,
    /// so the list no longer carries a "(none)" entry pretending to be a
    /// letter. It stays disabled until the user opts into mounting.
    /// </summary>
    private void FillFreeDriveLetters(string? suggestedDriveLetter)
    {
        var usedDriveLetters = new HashSet<char>(
            DriveInfo.GetDrives().Select(d => char.ToUpperInvariant(d.Name[0])));
        for (char driveLetter = 'Z'; driveLetter >= 'D'; driveLetter--)
            if (!usedDriveLetters.Contains(driveLetter))
                DriveLetterBox.Items.Add(driveLetter + ":");

        if (DriveLetterBox.Items.Count == 0)
        {
            // No free letter at all -- mounting is impossible, say so instead
            // of offering an empty list.
            MountCheck.IsEnabled = false;
            MountCheck.Content = "Mount as a drive (no free drive letters on this PC)";
            return;
        }

        DriveLetterBox.SelectedItem = !string.IsNullOrEmpty(suggestedDriveLetter)
            && DriveLetterBox.Items.Contains(suggestedDriveLetter)
                ? suggestedDriveLetter
                : DriveLetterBox.Items[0];
    }

    /// <summary>
    /// Shows what Credential Manager already holds for this Drobo, or why
    /// saving is not offered when the path names no server.
    /// </summary>
    private void ShowSavedLoginState()
    {
        // Never consult Credential Manager for the simulator: its address is
        // made up, and could match a real machine that has a real saved login.
        var savedUser = _credentialHost is null || _simulatedSignIn is not null
            ? null : CredentialStore.SavedUserName(_credentialHost);
        if (savedUser is not null)
        {
            if (savedUser.Length > 0) UsernameBox.Text = savedUser;
            RememberCheck.IsChecked = true;
            RememberNote.Text = savedUser.Length > 0
                ? $"A login for {_credentialHost} is already saved on this PC, as {savedUser}. "
                  + "Leave the password blank to use it, or type one to replace it."
                : $"A login for {_credentialHost} is already saved on this PC. "
                  + "Leave the password blank to use it, or type one to replace it.";
            RememberNote.Visibility = Visibility.Visible;
            ForgetLink.Visibility = Visibility.Visible;
            return;
        }

        if (_credentialHost is null)
        {
            // Nothing to key a credential to. Say so rather than showing a
            // box that would quietly do nothing.
            RememberCheck.IsEnabled = false;
            RememberCheck.IsChecked = false;
            RememberNote.Text = "This share is not a \\\\server\\share path, so there is no "
                              + "server name to save a login against.";
            RememberNote.Visibility = Visibility.Visible;
        }
    }

    /// <summary>
    /// The simulator checks the login itself and nothing is saved to
    /// Windows, so the save box is switched off and its account is named.
    /// </summary>
    private void ShowSimulatorNote()
    {
        RememberCheck.IsEnabled = false;
        RememberCheck.IsChecked = false;
        ForgetLink.Visibility = Visibility.Collapsed;
        RememberNote.Text = "This is the simulated Drobo. The simulator checks the login and "
                          + "nothing is saved to Windows. Its account is user admin, password admin.";
        RememberNote.Visibility = Visibility.Visible;
    }

    /// <summary>
    /// Removes the saved login for this Drobo, at the user's request. The
    /// share stays connected if it already is: forgetting a saved password is
    /// about what happens NEXT time, and disconnecting someone's open share
    /// as a side effect of a "forget" link would be a surprise.
    /// </summary>
    private void Forget_Click(object sender, System.Windows.Input.MouseButtonEventArgs e)
    {
        if (_credentialHost is null) return;
        if (CredentialStore.Forget(_credentialHost))
        {
            RememberNote.Text = $"The saved login for {_credentialHost} has been removed from this PC.";
            RememberNote.Visibility = Visibility.Visible;
            ForgetLink.Visibility = Visibility.Collapsed;
            RememberCheck.IsChecked = false;
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

    /// <summary>
    /// Checks the fields, connects, and closes with DialogResult true only
    /// once the share is connected and any requested save has worked. One
    /// attempt per click: the buttons are disabled while it runs.
    ///
    /// Once the share is connected and only the save failed, the button
    /// retries the save alone: connecting again would meet the connection
    /// that already worked, and Windows refuses that (ERROR_ALREADY_ASSIGNED
    /// for a drive letter, or a credential conflict).
    /// </summary>
    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        if (ConnectedButLoginNotSaved)
        {
            RetrySavingLogin();
            return;
        }

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
                              && _credentialHost is not null
                              && CredentialStore.HasSavedLogin(_credentialHost);

        if (password.Length == 0 && !usingSavedLogin)
        {
            ShowError("Enter the password for your Drobo account.");
            return;
        }

        string? driveLetter = SelectedDriveLetterOrNull();
        bool rememberLogin = RememberCheck.IsChecked == true;

        SetBusy(true);
        try
        {
            int win32ErrorCode = await ConnectAsync(driveLetter, username, password, usingSavedLogin, rememberLogin);

            if (win32ErrorCode != 0)
            {
                // The guest-logon advice describes Windows refusing a real Drobo;
                // it would be wrong about a simulated access-denied.
                ShowError(SmbConnections.DescribeError(win32ErrorCode, _simulatedSignIn is null ? _guestAdviceDetail : null));
                return;
            }

            if (!SaveLoginIfAsked(rememberLogin, usingSavedLogin, username, password)) return;

            DialogResult = true;
            Close();
        }
        finally
        {
            SetBusy(false);
        }
    }

    /// <summary>
    /// A drive letter only if the user asked to mount. Unchecked, the
    /// login opens a session with no local name -- Explorer and the in-app
    /// browser can both use the UNC path once Windows holds that session.
    /// </summary>
    private string? SelectedDriveLetterOrNull()
    {
        if (MountCheck.IsChecked == true && DriveLetterBox.SelectedItem is string letter
            && letter.Length == 2 && letter[1] == ':')
        {
            return letter;
        }
        return null;
    }

    /// <summary>
    /// Makes the connection and returns its Win32 code (0 is success).
    ///
    /// Off the UI thread: WNetAddConnection2 is a blocking network call and
    /// can take several seconds against a slow or unreachable device.
    ///
    /// CONNECT_UPDATE_PROFILE still rides along when a drive letter is
    /// being mapped and the user asked to remember: that is what makes
    /// the DRIVE come back at sign-in, which is a separate thing from
    /// the credential being stored.
    /// </summary>
    private Task<int> ConnectAsync(string? driveLetter, string username, string password,
                                   bool usingSavedLogin, bool rememberLogin)
    {
        if (_simulatedSignIn is not null) return _simulatedSignIn(driveLetter, username, password);
        return Task.Run(
            () => usingSavedLogin
                ? SmbConnections.Connect(_uncPath, driveLetter, null, null, rememberLogin)
                : SmbConnections.Connect(_uncPath, driveLetter, username, password, rememberLogin));
    }

    /// <summary>
    /// Saves the login to Credential Manager when the user asked for that
    /// and typed a password for a real Drobo. Returns false, after showing
    /// why, only when saving was asked for and failed.
    ///
    /// Only called after the credential is known to WORK. Saving a
    /// password that was just rejected would leave the user with a
    /// stored login that fails every time and no clue why.
    /// </summary>
    private bool SaveLoginIfAsked(bool rememberLogin, bool usingSavedLogin, string username, string password)
    {
        if (!rememberLogin || usingSavedLogin || _credentialHost is null || _simulatedSignIn is not null) return true;

        var saveFailureMessage = CredentialStore.Save(_credentialHost, username, password);
        if (saveFailureMessage is null) return true;

        // The share IS connected; only the saving failed. Say exactly that
        // and stay open, rather than reporting a failure that did not happen.
        ShowError(saveFailureMessage);
        ShowConnectedButNotSaved();
        return false;
    }

    /// <summary>True when the share connected but the login could not be
    /// saved. The dialog stays open showing why, and closing it reports the
    /// share as connected (see Cancel_Click); the caller checks this too, for
    /// a dialog closed with its title-bar button.</summary>
    public bool ConnectedButLoginNotSaved { get; private set; }

    /// <summary>
    /// The connected-but-not-saved state: the button offers the save again
    /// and Cancel becomes Close. The fields are locked, so a retry can only
    /// save the login that just connected, never one that was edited
    /// afterwards and never tried.
    ///
    /// Until 2026-11-02 nothing changed here: Cancel reported the share as
    /// not connected, so the Shares tab was not refreshed and the Open flow
    /// gave up, and Connect tried to connect again.
    /// </summary>
    private void ShowConnectedButNotSaved()
    {
        ConnectedButLoginNotSaved = true;
        UsernameBox.IsEnabled = false;
        PasswordInput.IsEnabled = false;
        MountCheck.IsEnabled = false;
        DriveLetterBox.IsEnabled = false;
        RememberCheck.IsEnabled = false;
        ForgetLink.Visibility = Visibility.Collapsed;
        CancelBtn.Content = "Close";
    }

    /// <summary>The Connect button in the connected-but-not-saved state:
    /// one more try at saving the login that connected, and no second
    /// connection.</summary>
    private void RetrySavingLogin()
    {
        if (_credentialHost is null) return;
        SetBusy(true);
        var saveFailureMessage = CredentialStore.Save(_credentialHost, UsernameBox.Text.Trim(), PasswordInput.Password);
        SetBusy(false);
        if (saveFailureMessage is not null)
        {
            ShowError(saveFailureMessage);
            return;
        }

        DialogResult = true;
        Close();
    }

    /// <summary>Disables both buttons while a connection is in progress, so a
    /// second click cannot start a second attempt, and hides the last error.</summary>
    private void SetBusy(bool busy)
    {
        ConnectBtn.IsEnabled = !busy;
        CancelBtn.IsEnabled = !busy;
        ConnectBtn.Content = busy
            ? (ConnectedButLoginNotSaved ? "Saving…" : "Connecting…")
            : (ConnectedButLoginNotSaved ? "Try saving again" : "Connect");
        if (busy) ErrorText.Visibility = Visibility.Collapsed;
    }

    /// <summary>Shows a message in the dialog itself rather than a message
    /// box, so the typed username and the chosen options stay in view.</summary>
    private void ShowError(string message)
    {
        ErrorText.Text = message;
        ErrorText.Visibility = Visibility.Visible;
    }

    /// <summary>Closes with DialogResult false, so the caller skips its
    /// share refresh -- unless the share is already connected and only the
    /// save failed, when it closes with true: the share is connected, and
    /// the caller must refresh and treat it so.</summary>
    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = ConnectedButLoginNotSaved;
        Close();
    }
}
