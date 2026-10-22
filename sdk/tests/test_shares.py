"""
Tests for the share list -- the "open my files" path.

Runs entirely offline. The fixtures are the exact shape a live Drobo 5N
returned on 2026-07-26, with the share names and addresses changed.

The interesting cases here are the awkward ones: a share with one user versus
several (the XML->dict conversion gives a dict for one and a list for many), a
share with no users at all, and an access code the device emits that we have
never seen before -- which must be reported honestly rather than guessed at.

    py test_shares.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import shares  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


HOST = "10.0.0.5"

# Two shares, each with a single ShareUser -> the conversion yields a dict.
TWO_SHARES = {"DRINASConfig": {"DRIShareConfig": {
    "ConfigVersionBasis": "14",
    "Shares": {"Share": [
        {"ShareName": "Alpha", "ShareState": "0", "TimeMachineEnabled": "0",
         "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                      "ShareUserAccess": "1"}}},
        {"ShareName": "Beta", "ShareState": "0", "TimeMachineEnabled": "1",
         "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                      "ShareUserAccess": "0"}}},
    ]},
}}}

print("== the ordinary case ==")
share_summaries = shares.summarize(TWO_SHARES, HOST)
check("both shares found",
      [s["name"] for s in share_summaries] == ["Alpha", "Beta"],
      [s["name"] for s in share_summaries])
check("UNC path is built for Windows",
      share_summaries[0]["unc"] == r"\\10.0.0.5\Alpha",
      share_summaries[0]["unc"])
check("Time Machine flag read", share_summaries[0]["time_machine"] is False
      and share_summaries[1]["time_machine"] is True,
      [s["time_machine"] for s in share_summaries])
# Code 1 is CONFIRMED as read/write: a capture of Drobo Dashboard 3.5.0 on
# 2026-08-05 showed a share the maintainer states is set to Read/Write carrying
# ShareUserAccess=1. Code 0 stays labelled by its number -- see ACCESS_LEVELS
# for why an intention ("supposed to be read") was not treated as a measurement.
check("Everyone access surfaced, using the confirmed meaning of code 1",
      share_summaries[0]["everyone"] == "read/write (code 1)",
      share_summaries[0]["everyone"])
check("only the codes we actually confirmed are marked as confirmed",
      shares.CONFIRMED_ACCESS_CODES == frozenset({"1"}),
      shares.CONFIRMED_ACCESS_CODES)
check("an unconfirmed code still says so in its description",
      "unconfirmed" in shares.ACCESS_LEVELS["0"], shares.ACCESS_LEVELS["0"])
check("access code kept alongside the description",
      share_summaries[0]["users"][0]["access_code"] == "1",
      share_summaries[0]["users"])

print("\n== shapes that break naive parsers ==")

ONE_SHARE = {"DRINASConfig": {"DRIShareConfig": {
    "Shares": {"Share": {"ShareName": "Solo", "ShareUsers": {
        "ShareUser": [{"ShareUsername": "Everyone", "ShareUserAccess": "1"},
                      {"ShareUsername": "casey", "ShareUserAccess": "2"}]}}},
}}}
solo_share_summaries = shares.summarize(ONE_SHARE, HOST)
check("a single share (dict, not list) is still found",
      len(solo_share_summaries) == 1, solo_share_summaries)
check("several users (list, not dict) all parsed",
      [user["name"] for user in solo_share_summaries[0]["users"]]
      == ["Everyone", "casey"],
      solo_share_summaries[0]["users"])

NO_USERS = {"DRINASConfig": {"DRIShareConfig": {
    "Shares": {"Share": {"ShareName": "Bare"}}}}}
bare_share_summaries = shares.summarize(NO_USERS, HOST)
check("a share with no users doesn't crash", len(bare_share_summaries) == 1 and bare_share_summaries[0]["users"] == [],
      bare_share_summaries)
check("no Everyone means no guest problem to report",
      bare_share_summaries[0]["everyone"] is None,
      bare_share_summaries[0]["everyone"])

check("an unwrapped DRIShareConfig also parses",
      len(shares.summarize({"DRIShareConfig": TWO_SHARES["DRINASConfig"]["DRIShareConfig"]},
                           HOST)) == 2)
check("junk input gives an empty list, not an exception",
      shares.summarize({}, HOST) == [] and shares.summarize(None, HOST) == []
      and shares.summarize({"DRINASConfig": None}, HOST) == [])
check("a nameless share is skipped rather than shown blank",
      shares.summarize({"DRIShareConfig": {"Shares": {"Share": {"ShareName": "  "}}}},
                       HOST) == [])

print("\n== honesty about codes we haven't confirmed ==")
UNKNOWN_ACCESS_CODE_CONFIG = {"DRIShareConfig": {"Shares": {"Share": {
    "ShareName": "Odd",
    "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                 "ShareUserAccess": "97"}}}}}}
odd_share_summary = shares.summarize(UNKNOWN_ACCESS_CODE_CONFIG, HOST)[0]
check("an unseen access code is reported, not guessed",
      "97" in odd_share_summary["everyone"] and "unrecognised" in odd_share_summary["everyone"], odd_share_summary["everyone"])

print("\n== the guest-logon advice ==")
advice_without_account_info = shares.access_advice(share_summaries)
check("both Everyone shares are named", advice_without_account_info["guest_shares"] == ["Alpha", "Beta"],
      advice_without_account_info["guest_shares"])
check("the advice names the actual Windows error",
      "0xC05D0003" in advice_without_account_info["detail"],
      advice_without_account_info["detail"])
check("the advice does not blame the Drobo",
      "Nothing is wrong with the Drobo"
      in advice_without_account_info["detail"])
check("no Everyone shares means no advice", shares.access_advice(bare_share_summaries)["headline"] == "")
check("with no account information, the wording is unchanged",
      "Connect with your Drobo user name" in advice_without_account_info["detail"], advice_without_account_info["detail"])
check("...and a UI is not told to disable anything",
      advice_without_account_info["can_sign_in"] is True,
      advice_without_account_info)


print("\n== reading the device's user accounts ==")
# The empty case is CONFIRMED -- it is what this project's own 5N returns.
# The populated case is inference: a UserList with users in it has never been
# observed, so user_accounts() is written to say "I don't know" rather than
# guess wrong. These tests pin that distinction down.
SELF_CLOSING_USER_LIST = {"DRIShareConfig": {"UserList": None, "Shares": {}}}
EMPTY_STRING_USER_LIST = {"DRIShareConfig": {"UserList": "", "Shares": {}}}
check("an empty UserList is definitely no accounts",
      shares.user_accounts(SELF_CLOSING_USER_LIST) == [], shares.user_accounts(SELF_CLOSING_USER_LIST))
check("...whichever empty form the device sent",
      shares.user_accounts(EMPTY_STRING_USER_LIST) == [], shares.user_accounts(EMPTY_STRING_USER_LIST))
check("a missing UserList is 'unknown', NOT 'none'",
      shares.user_accounts({"DRIShareConfig": {"Shares": {}}}) is None)
check("junk input is unknown rather than an empty answer",
      shares.user_accounts({}) is None and shares.user_accounts(None) is None)

ONE_USER = {"DRIShareConfig": {"UserList": {"User": {"UserName": "jordan"}}}}
TWO_USERS = {"DRIShareConfig": {"UserList": {"User": [
    {"UserName": "jordan"}, {"UserName": "ada"}]}}}
check("a single user record is read", shares.user_accounts(ONE_USER) == ["jordan"],
      shares.user_accounts(ONE_USER))
check("several are read and sorted", shares.user_accounts(TWO_USERS) == ["ada", "jordan"],
      shares.user_accounts(TWO_USERS))
check("a UserList we cannot parse is unknown, not empty",
      shares.user_accounts({"DRIShareConfig": {"UserList": {"Mystery": {"X": "1"}}}}) is None)


print("\n== advice when the Drobo has no accounts to sign in with ==")
# The bug this fixes: the old text told everyone to "connect with your Drobo
# user name", which on a device with zero accounts is a door with nothing
# behind it.
no_accounts_advice = shares.access_advice(share_summaries, [])
check("the headline stops promising a sign-in will work",
      no_accounts_advice["headline"] == "These shares cannot be opened from Windows yet.", no_accounts_advice["headline"])
check("it says there is no name to use",
      "no user accounts" in no_accounts_advice["detail"],
      no_accounts_advice["detail"])
check("it says what to do instead", "Create one in Drobo Dashboard" in no_accounts_advice["detail"],
      no_accounts_advice["detail"])
check("it does NOT tell them to connect with a user name that isn't there",
      "Connect with your Drobo user name" not in no_accounts_advice["detail"], no_accounts_advice["detail"])
check("a UI is told to disable its sign-in control",
      no_accounts_advice["can_sign_in"] is False, no_accounts_advice)

named_accounts_advice = shares.access_advice(share_summaries,
                                             ["ada", "jordan"])
check("when accounts exist they are named, so there is no guessing",
      "ada, jordan" in named_accounts_advice["detail"],
      named_accounts_advice["detail"])
check("...and signing in is still offered",
      named_accounts_advice["can_sign_in"] is True, named_accounts_advice)
check("the accounts come back for the UI", named_accounts_advice["accounts"] == ["ada", "jordan"], named_accounts_advice)

check("no Everyone shares means no advice even with no accounts",
      shares.access_advice(bare_share_summaries, [])["headline"] == "")
check("...and does not disable a control for no reason",
      shares.access_advice(bare_share_summaries, [])["can_sign_in"] is True)


print("\n== and the second, separate problem: unconfirmed access codes ==")
# Alpha is code 1 (confirmed read/write); Beta is code 0 (not confirmed). Only
# Beta should be called out, or the warning becomes noise.
check("only the unconfirmed share is named",
      "Beta is currently set to" in no_accounts_advice["detail"],
      no_accounts_advice["detail"])
check("the confirmed one is not dragged in",
      "Alpha is currently set to" not in no_accounts_advice["detail"],
      no_accounts_advice["detail"])
check("it warns that signing in may not be the fix",
      "signing in will not solve it" in no_accounts_advice["detail"],
      no_accounts_advice["detail"])

BOTH_UNCONFIRMED = shares.summarize({"DRIShareConfig": {"Shares": {"Share": [
    {"ShareName": "One", "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                                      "ShareUserAccess": "0"}}},
    {"ShareName": "Two", "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                                      "ShareUserAccess": "0"}}},
]}}}, HOST)
both_unconfirmed_advice = shares.access_advice(BOTH_UNCONFIRMED, [])
check("two shares read as a list, not 'One is, Two is'",
      "One and Two are currently set to" in both_unconfirmed_advice["detail"],
      both_unconfirmed_advice["detail"])

ALL_CONFIRMED = shares.summarize({"DRIShareConfig": {"Shares": {"Share": {
    "ShareName": "Fine", "ShareUsers": {"ShareUser": {"ShareUsername": "Everyone",
                                                      "ShareUserAccess": "1"}}}}}}, HOST)
check("nothing is said when every code is confirmed",
      "not confirmed the meaning of" not in shares.access_advice(ALL_CONFIRMED, [])["detail"],
      shares.access_advice(ALL_CONFIRMED, [])["detail"])

print("\n== the link-speed check ==")
GIGABIT_NETWORK_CONFIG = {"DRINASConfig": {"DRINasNetworkConfig": {
    "PortSpeed": "1000", "PortDuplex": "full"}}}
SLOW_NETWORK_CONFIG = {"DRINASConfig": {"DRINasNetworkConfig": {
    "PortSpeed": "100", "PortDuplex": "full"}}}

gigabit_link_health = shares.link_health(GIGABIT_NETWORK_CONFIG)
check("a gigabit link is not flagged", gigabit_link_health["degraded"] is False and gigabit_link_health["note"] == "",
      gigabit_link_health)
check("gigabit speed reported", gigabit_link_health["speed_mbps"] == 1000,
      gigabit_link_health)

slow_link_health = shares.link_health(SLOW_NETWORK_CONFIG)
check("a 100 Mbit link IS flagged", slow_link_health["degraded"] is True,
      slow_link_health)
check("the note says how much slower", "10x slower" in slow_link_health["note"], slow_link_health["note"])
check("the note suggests the cable first",
      "cable" in slow_link_health["note"].lower())

half_duplex_link_health = shares.link_health(
    {"DRINasNetworkConfig": {"PortSpeed": "1000",
                             "PortDuplex": "half"}})
check("half duplex is flagged even at full speed", half_duplex_link_health["degraded"] is True, half_duplex_link_health)

check("a missing PortSpeed is not reported as a fault",
      shares.link_health({}) == {"speed_mbps": None, "duplex": "",
                                 "degraded": False, "note": ""},
      shares.link_health({}))
check("a non-numeric PortSpeed doesn't crash",
      shares.link_health({"DRINasNetworkConfig": {"PortSpeed": "auto"}})["degraded"]
      is False)
check("PortSpeed 0 (link down) is not called degraded",
      shares.link_health({"DRINasNetworkConfig": {"PortSpeed": "0"}})["degraded"]
      is False)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
