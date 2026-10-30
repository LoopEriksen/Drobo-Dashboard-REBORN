using System.Windows;

namespace DroboDashboardReborn;

/// <summary>
/// One file share, ready for the Shares tab: what the Drobo calls it, the
/// Windows path to it, and whether Windows currently has it connected.
///
/// The connection fields (IsConnected/DriveLetter/ConnectionText) are read
/// fresh from Windows itself each time the tab refreshes -- the agent has
/// no way to know this, since it is entirely local to this PC. See
/// SmbConnections.GetActiveConnections.
/// </summary>
public sealed class ShareRow
{
    /// <summary>The share's name as the Drobo reports it.</summary>
    public string Name { get; set; } = "";

    /// <summary>The Windows path to the share (\\host\name), the one every
    /// connect, open and disconnect uses. Bound in MainWindow.xaml as
    /// <c>Unc</c>, so the name stays: a renamed binding fails silently.</summary>
    public string Unc { get; set; } = "";

    /// <summary>Whether the Drobo marks this share as a Time Machine target.
    /// The badge binds to <see cref="TimeMachineVisibility"/>, not to
    /// this.</summary>
    public bool IsTimeMachine { get; set; }
    public Visibility TimeMachineVisibility { get; set; } = Visibility.Collapsed;

    /// <summary>Whether Windows has a session to this share right now --
    /// read from Windows, not from the agent.</summary>
    public bool IsConnected { get; set; }

    /// <summary>The drive letter this share is mapped to (e.g. "Z:"), or null if it's
    /// connected without one, or not connected at all.</summary>
    public string? DriveLetter { get; set; }

    /// <summary>The connection line under the share's name, e.g. "Connected
    /// as Z:".</summary>
    public string ConnectionText { get; set; } = "Not connected";

    /// <summary>The per-user access list from /api/shares, one line of
    /// "name: access" pairs in the agent's own wording for each level --
    /// see MainWindow.RenderShares for why that wording is never reworded
    /// here. Empty (and collapsed) when the device listed no users.</summary>
    public string UsersText { get; set; } = "";
    public Visibility UsersVisibility { get; set; } = Visibility.Collapsed;

    /// <summary>
    /// Whether "Connect as…" can possibly succeed. False only when the agent
    /// has positively established that the Drobo has NO user accounts -- in
    /// which case the sign-in dialog is a door with nothing behind it, and
    /// offering it invites somebody to blame their own typing.
    ///
    /// Defaults to true, and stays true when the account list is merely
    /// unknown. A control disabled on a guess is worse than one that fails
    /// honestly: the failure at least says why.
    /// </summary>
    public bool CanSignIn { get; set; } = true;

    /// <summary>Tooltip for the disabled Connect-as button, so hovering explains
    /// the greying rather than leaving it a mystery. Null when enabled.</summary>
    public string? SignInBlockedReason { get; set; }
}
