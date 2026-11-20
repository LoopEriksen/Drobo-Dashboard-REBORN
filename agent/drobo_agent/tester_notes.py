"""
The sentences that tell a tester which writes are not yet proven.

Share create, share delete and DroboApps Start/Stop are built, wired
and offered, but none of them has been confirmed on a real Drobo:
create was applied once, on 2026-08-25/26, before the acknowledgement
fix (config_cmd.decided_by_readback), and has not been re-run since;
delete and DroboApps Start/Stop have never been sent to a device at
all. Share access is proven for the Everyone row only, on a unit with
no user accounts. And Identify was accepted by the one 5N tested, at
this app's default setting, without the lights visibly changing.

Until 2026-11-20 nothing a tester could see said any of that, so a
stranger could have been the first person ever to send a delete, on a
device with no recovery tool, without knowing to use a throwaway share
or to report what happened. These are labels, not gates: the
maintainer decided (2026-11-20) to say so at the point of use rather
than hide the controls, and nothing here changes what is sent.

They live in this one module so that the browser dashboard (webui.py)
and the agent's own replies (api.py) say the same thing, and so they
can be reworded in one place once the maintainer's hardware session has
settled them. The Windows app keeps its copy, word for word, in
windows/DroboDashboardReborn/TesterNotes.cs; change both together.
"""

from __future__ import annotations

#: Shown beside every "create a share" control.
SHARE_CREATE_UNPROVEN = (
    "Creating a share is not yet proven on a real Drobo: it was applied "
    "once in testing, before a fix. Try it with a throwaway name first.")

#: Shown in every delete confirmation, before the share is named.
SHARE_DELETE_UNPROVEN = (
    "This app's delete has never been sent to a real Drobo. Try it "
    "first on an empty share you made for the purpose.")

#: Shown wherever DroboApps Start and Stop are offered.
DROBOAPPS_ACTION_UNPROVEN = (
    "Start and Stop have not yet been tried on a real Drobo. If one "
    "does not behave, say so in a Tester report.")

#: Shown beside the share-access editor. The tested unit has no user
#: accounts, so only the guest row has ever been written. It carries
#: the user-accounts warning itself, folded into one sentence, rather
#: than having SHARE_WRITES_WITH_USER_ACCOUNTS appended: two sentences
#: in a row both ending "have not been tried yet" read as a stutter.
SHARE_ACCESS_UNPROVEN = (
    "Only the Everyone row has been proven on a real Drobo. Rows for "
    "named accounts, and share changes on a Drobo that has user "
    "accounts, have not been tried yet.")

#: Every share write resends the Drobo's user-account list as it was
#: read, and a list with accounts in it has never been seen. A warning
#: only, by the maintainer's decision: refusing would also switch off
#: share access, which is proven, on most testers' units. Shown after
#: the create and delete sentences; SHARE_ACCESS_UNPROVEN says the
#: same within its own sentence.
SHARE_WRITES_WITH_USER_ACCOUNTS = (
    "Share changes on a Drobo that has user accounts have not been "
    "tried yet.")

#: Identify, at the default setting. Sent 2026-08-07 to the one 5N
#: tested, with the maintainer watching: accepted, and no light moved.
#: The reply used to say the lights "should be blinking now", which a
#: tester would see contradicted and then doubt every other message.
#: "The setting this app sends", not "this setting": the card is read
#: before any click, and neither app shows a setting to point at.
IDENTIFY_NOT_SEEN = (
    "On the one Drobo 5N tested so far, the lights did not visibly "
    "change at the setting this app sends. Please say in your report "
    "whether yours blinked.")

#: Identify with an interval nobody has sent to a real Drobo yet.
IDENTIFY_UNTRIED_INTERVAL = (
    "This setting has not been tried on a real Drobo yet. Please say "
    "in your report whether yours blinked.")
