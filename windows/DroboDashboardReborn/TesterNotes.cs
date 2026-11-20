namespace DroboDashboardReborn;

/// <summary>
/// The sentences that tell a tester which writes are not yet proven on a
/// real Drobo, kept in one place so they can be reworded once the
/// maintainer's hardware session has settled them.
///
/// Why they exist: share create was applied once in testing, before its
/// acknowledgement fix, and has not been re-run since; share delete and
/// DroboApps Start/Stop have never been sent to a device; share access is
/// proven for the Everyone row only, on a unit with no user accounts; and
/// Identify moved no light on the one Drobo 5N tested. Until 2026-11-20 no
/// screen said so, so a tester could have been the first person ever to
/// send a delete, on a device with no recovery tool, without knowing to
/// use a throwaway share. These are labels, not gates: the maintainer
/// decided to say so where the buttons are rather than hide them, and
/// nothing here changes what is sent.
///
/// The browser dashboard and the agent's replies carry the same words from
/// agent/drobo_agent/tester_notes.py; change both together. The XAML
/// reads these through x:Static, so every window shows the same text.
/// </summary>
public static class TesterNotes
{
    /// <summary>Beside the new-share controls.</summary>
    public const string ShareCreateUnproven =
        "Creating a share is not yet proven on a real Drobo: it was applied "
        + "once in testing, before a fix. Try it with a throwaway name first.";

    /// <summary>In the delete confirmation.</summary>
    public const string ShareDeleteUnproven =
        "This app's delete has never been sent to a real Drobo. Try it "
        + "first on an empty share you made for the purpose.";

    /// <summary>On the DroboApps card, where Start and Stop sit.</summary>
    public const string DroboAppsActionUnproven =
        "Start and Stop have not yet been tried on a real Drobo. If one "
        + "does not behave, say so in a Tester report.";

    /// <summary>
    /// Beside the share-access editor. The tested unit has no user
    /// accounts, so only the guest row has ever been written. It carries the
    /// user-accounts warning within its own sentence rather than having
    /// ShareWritesWithUserAccounts appended: two sentences in a row both
    /// ending "have not been tried yet" read as a stutter.
    /// </summary>
    public const string ShareAccessUnproven =
        "Only the Everyone row has been proven on a real Drobo. Rows for "
        + "named accounts, and share changes on a Drobo that has user "
        + "accounts, have not been tried yet.";

    /// <summary>
    /// Every share write resends the Drobo's user-account list as it was
    /// read, and a list with accounts in it has never been seen. A warning
    /// only, by the maintainer's decision: refusing would also switch off
    /// share access, which is proven, on most testers' units. Shown after
    /// the create and delete sentences; ShareAccessUnproven says the same
    /// within its own sentence.
    /// </summary>
    public const string ShareWritesWithUserAccounts =
        "Share changes on a Drobo that has user accounts have not been "
        + "tried yet.";

    /// <summary>
    /// Identify at the default setting: sent on 2026-08-07 to the one 5N
    /// tested, with the maintainer watching, accepted, and no light moved.
    /// The card used to promise blinking, which a tester would see
    /// contradicted and then doubt every other message in the app. "The
    /// setting this app sends", not "this setting": the card is read before
    /// any click, and the app shows no setting to point at.
    /// </summary>
    public const string IdentifyNotSeen =
        "On the one Drobo 5N tested so far, the lights did not visibly "
        + "change at the setting this app sends. Please say in your report "
        + "whether yours blinked.";

    // What each place shows: the sentences above, combined. Built from
    // them rather than retyped, so a reworded sentence reaches every
    // place it appears.

    /// <summary>The new-share dialog's note.</summary>
    public const string ShareCreateNote = ShareCreateUnproven + " " + ShareWritesWithUserAccounts;

    /// <summary>The delete dialog's note.</summary>
    public const string ShareDeleteNote = ShareDeleteUnproven + " " + ShareWritesWithUserAccounts;

    /// <summary>The share-access card's note. The user-accounts warning is
    /// already inside ShareAccessUnproven, so nothing is appended.</summary>
    public const string ShareAccessNote = ShareAccessUnproven;

    /// <summary>The Identify card's explanation, shown before any click.</summary>
    public const string IdentifyCardText =
        "Asks the Drobo to blink its front lights, so you can tell which box "
        + "is which. " + IdentifyNotSeen + " The original Dashboard's own "
        + "warning says they blink for 15 minutes, so press Stop when you "
        + "are done. Nothing else on the device changes.";
}
