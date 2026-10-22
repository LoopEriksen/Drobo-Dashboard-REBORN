"""
End-to-end test of POST /api/shares/create and POST /api/shares/delete,
against the simulated Drobo.

    py test_shares_api.py

Separate from test_agent.py for the same reason test_droboapps_api.py is:
these endpoints' interesting branch is the simulated one, and turning
`simulate_config` on in that suite would change how every config read it
makes behaves. So this spins up its own agent on its own port.

WHAT IS ACTUALLY BEING PROVEN

Not that a Drobo grows or loses a share -- nothing here can know that. What
this proves is the contract the web page and the Windows app are built
against: that a created share appears in /api/shares IMMEDIATELY (the write
drops the 60-second shares cache -- a stale cache once made a verified write
look failed), that a deleted one disappears the same way, that confirm_name
must repeat the name exactly before anything reaches any device, that every
refusal is a 400 with the reason in plain English, and that the simulator is
no more permissive than the SDK write it stands in for.

And, since 2026-08-26, the four ways a write can END. A real 5N took a share
create, closed the socket without answering, and had created the share anyway
-- so "the device said yes" and "the change is there" are separate questions,
with four combinations between them. The mock's injected write endings drive
all four through the simulated fork; a stand-in for sharewrite.create_share
drives the same four through the SDK fork. Both are checked on both halves:
what the agent answers, AND what the device actually holds afterwards. The two
middles matter most -- a write that applied without being acknowledged must
never read as failed, and one nobody can settle must never read as done.
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk")))

from drobo_agent import api, config, notify  # noqa: E402
from drobo_agent.drivers import MockDriver  # noqa: E402
from drobo_agent.monitor import Monitor  # noqa: E402
from drobo_agent.photos import PhotoStore  # noqa: E402
from drobo_nasd import config_cmd, sharewrite  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + ("  " + str(failure_detail)
             if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


temp_dir = tempfile.mkdtemp(prefix="shares-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, temp_dir, ignore_errors=True)
config_path = os.path.join(temp_dir, "config.json")
PORT = 7439
BASE_URL = f"http://127.0.0.1:{PORT}"

with open(config_path, "w") as config_file:
    json.dump({
        "agent": {"bind": "127.0.0.1", "port": PORT, "poll_seconds": 1},
        "drobo": {"driver": "mock", "simulate_config": True},
        "photos": {"enabled": False},
    }, config_file)

agent_config = config.load(config_path)
TOKEN = agent_config["agent"]["token"]

monitor = Monitor(agent_config, notify.build(agent_config["notify"]))
photo_store = PhotoStore(agent_config["photos"])
photo_store.load()
monitor.start()
http_server = api.serve(agent_config, monitor, photo_store)
threading.Thread(target=http_server.serve_forever, daemon=True).start()
time.sleep(1.0)


def call(path, body=None, method=None, token=TOKEN, raw=False):
    headers = {"X-Agent-Token": token} if token else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers,
        method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            return response.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as http_error:
        return http_error.code, json.loads(http_error.read())


def listed_names():
    status, reply = call("/api/shares")
    return status, [share["name"] for share in reply.get("shares", [])], reply


def version_basis():
    """The simulated device's own config version -- bumped by every simulated
    write, so an unchanged value is proof nothing touched the (mock) device."""
    return monitor.driver._shares["DRINASConfig"]["DRIShareConfig"]["ConfigVersionBasis"]


driver = monitor.driver
check("the simulated driver is the one under test",
      getattr(driver, "simulate_config", False) is True, type(driver).__name__)

# /api/shares lists shares sorted by name, so expectations are sorted too.
BASELINE_SHARE_NAMES = ["Documents", "Photo Library", "Private", "TimeMachine"]

try:
    print("\n== baseline: /api/shares lists the simulated shares (and fills the cache) ==")
    status, names, reply = listed_names()
    check("shares 200", status == 200, (status, reply))
    check("the four simulated shares are listed",
          names == BASELINE_SHARE_NAMES, names)

    print("\n== the simulated Drobo has one account: admin / admin ==")
    advice = reply.get("advice", {})
    check("the advice names the account", advice.get("accounts") == ["admin"], advice)
    check("so signing in is offered", advice.get("can_sign_in") is True, advice)

    def sign_in(share, username, password, token=TOKEN):
        return call("/api/mock/sign-in",
                    body={"share": share, "username": username, "password": password},
                    token=token)

    status, sign_in_result = sign_in("Documents", "admin", "admin")
    check("admin / admin opens Documents",
          status == 200 and sign_in_result.get("ok") is True
          and sign_in_result.get("win32_error") == 0, (status, sign_in_result))
    status, sign_in_result = sign_in("documents", "ADMIN", "admin")
    check("share and user names are case-insensitive, like SMB", sign_in_result.get("ok") is True, sign_in_result)
    status, sign_in_result = sign_in("Documents", "admin", "Admin")
    check("the password is case-sensitive", sign_in_result.get("ok") is False
          and sign_in_result.get("win32_error") == 1326, sign_in_result)
    status, sign_in_result = sign_in("Documents", "someone", "admin")
    check("an unknown user is a logon failure", sign_in_result.get("win32_error") == 1326, sign_in_result)
    status, sign_in_result = sign_in("Documents", "admin", "hunter2-probe")
    check("a wrong password never comes back in the answer",
          "hunter2-probe" not in json.dumps(sign_in_result), sign_in_result)
    status, sign_in_result = sign_in("Private", "admin", "admin")
    check("admin reaches Private, where Everyone has no access", sign_in_result.get("ok") is True, sign_in_result)
    status, sign_in_result = sign_in("Nowhere", "admin", "admin")
    check("an unknown share is 'network name not found'", sign_in_result.get("win32_error") == 67, sign_in_result)
    status, sign_in_result = call("/api/mock/sign-in", body={"share": "Documents", "password": "admin"})
    check("a missing username is a 400", status == 400,
          (status, sign_in_result))
    status, sign_in_result = call("/api/mock/sign-in", body=b"{not json")
    check("bad JSON is a 400", status == 400, (status, sign_in_result))
    status, sign_in_result = sign_in("Documents", "admin", "admin",
                                     token="wrong-token")
    check("without the agent token it is refused", status == 401,
          (status, sign_in_result))

    print("\n== create: the new share appears across the cache invalidation ==")
    status, reply = call("/api/shares/create", body={"name": "Backups"})
    check("create 200 with ok", status == 200 and reply.get("ok") is True,
          (status, reply))
    check("reported as simulated", reply.get("simulated") is True, reply)
    check("verified true (the pinned contract's read-back claim)",
          reply.get("verified") is True, reply)
    check("the message says the share starts with no access",
          "no access" in reply.get("message", ""), reply.get("message"))

    # This read happens well inside the 60s shares TTL, and the cache was
    # filled by the baseline read above -- so a listing that carries the new
    # share is proof the write invalidated the cache rather than leaving a
    # stale copy that makes a verified write look failed.
    status, names, reply = listed_names()
    check("the new share is listed immediately, not after the TTL",
          names == sorted(BASELINE_SHARE_NAMES + ["Backups"]), names)
    backups_shares = [share for share in reply.get("shares", [])
                      if share["name"] == "Backups"]
    check("the new share has no users -- no access for anyone yet",
          backups_shares and backups_shares[0].get("users") == [],
          backups_shares)

    print("\n== the flow the create message promises: grant access afterwards ==")
    # A new share is stored with an EMPTY <ShareUsers/> -- None once parsed --
    # and the create message tells the user to grant access next. That is the
    # one share on the device whose users container is empty, so it is also the
    # one the access write must handle.
    status, reply = call("/api/config/share-access",
                         body={"share": "Backups", "user": "Everyone",
                               "access": "1"})
    check("granting access on a brand-new share works", status == 200 and reply.get("ok"), (status, reply))
    status, names, reply = listed_names()
    backups_shares = [share for share in reply.get("shares", [])
                      if share["name"] == "Backups"]
    check("-- and the user now reads back on it",
          backups_shares and [user["name"] for user in backups_shares[0].get("users", [])] == ["Everyone"],
          backups_shares)

    print("\n== delete: the share disappears the same way ==")
    status, reply = call("/api/shares/delete",
                         body={"name": "Backups", "confirm_name": "Backups"})
    check("delete 200 with ok", status == 200 and reply.get("ok") is True,
          (status, reply))
    check("reported as simulated", reply.get("simulated") is True, reply)
    check("verified true", reply.get("verified") is True, reply)
    check("the message owns the data loss",
          "everything it held" in reply.get("message", ""),
          reply.get("message"))
    status, names, reply = listed_names()
    check("the deleted share is gone immediately",
          names == BASELINE_SHARE_NAMES, names)

    print("\n== create refusals: 400, before anything changes ==")
    version_basis_before = version_basis()
    status, reply = call("/api/shares/create", body={"name": "documents"})
    check("a case-fold duplicate is refused with 400", status == 400,
          (status, reply))
    check("-- naming the existing share",
          "already exists" in reply.get("error", ""), reply.get("error"))

    status, reply = call("/api/shares/create", body={"name": "bad*name"})
    check("a name outside the charset is refused with 400", status == 400,
          (status, reply))
    check("-- stating the rule", "1-27" in reply.get("error", ""),
          reply.get("error"))

    status, reply = call("/api/shares/create", body={"name": " Backups"})
    check("surrounding whitespace is refused, not stripped", status == 400,
          (status, reply))
    check("-- and says why", "whitespace" in reply.get("error", ""), reply.get("error"))

    status, reply = call("/api/shares/create",
                         body={"name": "-leading-hyphen"})
    check("a non-alphanumeric first character is refused with 400", status == 400, (status, reply))

    status, reply = call("/api/shares/create", body={"name": "x" * 28})
    check("a 28-character name is refused with 400", status == 400,
          (status, reply))

    status, reply = call("/api/shares/create", body={"name": ""})
    check("an empty name is refused with 400", status == 400, (status, reply))

    status, reply = call("/api/shares/create", body=b"{not json")
    check("malformed JSON is refused with 400", status == 400, (status, reply))

    check("no refused create touched the simulated device",
          version_basis() == version_basis_before,
          (version_basis_before, version_basis()))

    print("\n== delete refusals: confirm_name gates the device ==")
    status, reply = call("/api/shares/delete",
                         body={"name": "Documents",
                               "confirm_name": "documents"})
    check("a confirm_name differing only by case is refused with 400", status == 400, (status, reply))
    check("-- saying nothing was sent",
          "nothing was sent" in reply.get("error", ""), reply.get("error"))

    status, reply = call("/api/shares/delete", body={"name": "Documents"})
    check("a missing confirm_name is refused with 400", status == 400,
          (status, reply))
    check("-- explaining the retype rule",
          "typed twice" in reply.get("error", ""), reply.get("error"))

    # The mismatch 400 comes from the parse step, before the simulate fork or
    # any device lookup -- so even an unknown share fails on the CONFIRMATION,
    # proving the gate runs first.
    status, reply = call("/api/shares/delete",
                         body={"name": "Nope", "confirm_name": "nope"})
    check("the confirmation is checked before the share is even looked up",
          status == 400 and "confirm_name" in reply.get("error", ""),
          (status, reply))

    status, reply = call("/api/shares/delete", body={"name": "Nope", "confirm_name": "Nope"})
    check("a confirmed delete of an unknown share is refused with 400", status == 400, (status, reply))
    check("-- listing what the device has",
          "no share named" in reply.get("error", ""), reply.get("error"))

    check("no refused delete touched the simulated device",
          version_basis() == version_basis_before,
          (version_basis_before, version_basis()))
    status, names, reply = listed_names()
    check("every share is still there", names == BASELINE_SHARE_NAMES, names)

    print("\n== auth: 401s are clean even with a request body ==")
    status, reply = call("/api/shares/create", body={"name": "Sneaky"},
                         token="")
    check("unauthenticated create answers a clean 401", status == 401,
          (status, reply))
    check("-- with the reason in the body", "X-Agent-Token" in reply.get("error", ""), reply)
    status, reply = call("/api/shares/delete",
                         body={"name": "Documents",
                               "confirm_name": "Documents"}, token="wrong")
    check("wrong-token delete answers a clean 401", status == 401,
          (status, reply))
    check("...and neither reached the driver",
          version_basis() == version_basis_before,
          (version_basis_before, version_basis()))
    status, names, reply = listed_names()
    check("the share list is unchanged", names == BASELINE_SHARE_NAMES, names)

    print("\n== the simulator mutates per-instance state only ==")
    call("/api/shares/create", body={"name": "Scratch"})
    fresh_driver = MockDriver({"simulate_config": True})
    fresh_names = [share["ShareName"] for share in
                   fresh_driver._shares["DRINASConfig"]["DRIShareConfig"]["Shares"]["Share"]]
    check("a fresh MockDriver has only the pristine shares",
          sorted(fresh_names) == BASELINE_SHARE_NAMES, fresh_names)
    class_names = [share["ShareName"] for share in
                   MockDriver._MOCK_SHARES["DRINASConfig"]["DRIShareConfig"]["Shares"]["Share"]]
    check("the class-level pristine table is untouched",
          sorted(class_names) == BASELINE_SHARE_NAMES, class_names)

    print("\n== all four write outcomes, through the mock's injected endings ==")
    # The point of this whole section is the two middles. On 2026-08-25 a real
    # 5N was sent a share create, closed the socket without answering, and had
    # created the share -- and every layer above reported a failure. So each
    # ending below is checked on BOTH halves: what the agent says, and what the
    # simulated device actually holds afterwards.

    def arm_write_ending(mode):
        status, reply = call("/api/mock/write/" + mode, method="POST")
        return status == 200, reply

    accepted, reply = arm_write_ending("hangs_up")
    check("the mock accepts an injected write ending", accepted, reply)

    status, reply = call("/api/shares/create", body={"name": "Unanswered"})
    check("a write the Drobo never acknowledged is a SUCCESS, not a failure",
          status == 200 and reply.get("ok") is True, (status, reply))
    check("-- labelled as its own outcome",
          reply.get("outcome") == "verified_unacknowledged", reply)
    check("-- with acknowledged false, the field a UI reads",
          reply.get("acknowledged") is False, reply)
    check("-- still verified: the read-back is what settled it",
          reply.get("verified") is True, reply)
    check("-- and the message says the Drobo did not answer",
          "did not answer" in reply.get("message", ""), reply.get("message"))
    check("-- and says the read-back is why we know it worked",
          "reading the setting back" in reply.get("message", ""),
          reply.get("message"))
    status, names, _ = listed_names()
    check("-- and the share really is there, which is the whole point",
          "Unanswered" in names, names)

    accepted, reply = arm_write_ending("unconfirmable")
    check("the mock accepts the unsettleable ending", accepted, reply)
    version_basis_before = version_basis()
    status, reply = call("/api/shares/create", body={"name": "Unsettled"})
    check("a write nobody can settle is 502 -- never a 200", status == 502,
          (status, reply))
    check("-- labelled unknown, so a UI paints it amber not red",
          reply.get("outcome") == "unknown", reply)
    check("-- and NEVER claims it worked", reply.get("ok") is not True, reply)
    check("-- the wording says it may have been applied",
          "may well have been" in reply.get("error", ""), reply.get("error"))
    check("-- and says to re-read before sending it again",
          "before sending this again" in reply.get("error", ""),
          reply.get("error"))
    # The trap this outcome exists for: the change IS on the device while the
    # answer says nothing of the sort. A UI that renders this as "failed"
    # invites a retry of a write that already happened.
    check("-- and the write had in fact applied",
          version_basis() != version_basis_before,
          (version_basis_before, version_basis()))
    status, names, _ = listed_names()
    check("-- the share is listed, exactly as the hardware case behaved",
          "Unsettled" in names, names)

    accepted, reply = arm_write_ending("refused")
    check("the mock accepts the refusing ending", accepted, reply)
    version_basis_before = version_basis()
    status, reply = call("/api/shares/create", body={"name": "Refused"})
    check("a refused write is 502", status == 502, (status, reply))
    check("-- labelled failed", reply.get("outcome") == "failed", reply)
    check("-- saying nothing is confirmed changed",
          "Nothing is confirmed changed" in reply.get("error", ""),
          reply.get("error"))
    check("-- and it left the simulated device untouched",
          version_basis() == version_basis_before,
          (version_basis_before, version_basis()))
    status, names, _ = listed_names()
    check("-- no share appeared", "Refused" not in names, names)

    # A refusal is the device's answer; a bad name never reaches it. The
    # injected ending must not turn the caller's own mistake into a 502.
    status, reply = call("/api/shares/create", body={"name": "bad*name"})
    check("a parameter refusal is still 400 while a write fault is armed",
          status == 400, (status, reply))

    accepted, reply = arm_write_ending("none")
    check("the mock accepts being put back to healthy", accepted, reply)
    status, reply = call("/api/shares/create", body={"name": "Healthy"})
    check("a healthy write is 200 with ok", status == 200 and reply.get("ok") is True, (status, reply))
    check("-- labelled verified", reply.get("outcome") == "verified", reply)
    check("-- acknowledged true", reply.get("acknowledged") is True, reply)

    status, reply = call("/api/shares/delete",
                         body={"name": "Healthy", "confirm_name": "Healthy"})
    check("delete reports the same vocabulary", reply.get("outcome") == "verified", reply)

    # Delete is destructive, so its unacknowledged case matters most of all:
    # reporting a completed delete as a failure is how a share gets deleted
    # twice, and the second attempt would fail on a share that is already gone.
    arm_write_ending("hangs_up")
    status, reply = call("/api/shares/delete",
                         body={"name": "Unanswered",
                               "confirm_name": "Unanswered"})
    check("an unacknowledged DELETE is a success too",
          status == 200 and reply.get("outcome") == "verified_unacknowledged",
          (status, reply))
    status, names, _ = listed_names()
    check("-- and the share really is gone", "Unanswered" not in names, names)

    status, reply = call("/api/mock/write/nonsense", method="POST")
    check("an unknown write ending is refused with 400", status == 400,
          (status, reply))
    check("-- listing the ones that exist", "hangs_up" in reply.get("error", ""), reply)

    status, reply = call("/api/mock/reset", method="POST")
    check("reset clears the injected write ending too", status == 200,
          (status, reply))
    check("-- proven by the next write being a plain success",
          call("/api/shares/create", body={"name": "AfterReset"})[1].get("outcome")
          == "verified")

    print("\n== the SAME four outcomes off the real SDK, without hardware ==")
    # Everything above drives the simulated fork. This drives the HARDWARE fork
    # -- the one that calls the SDK -- with the SDK function replaced by
    # something that answers the way the SDK documents. No socket is opened:
    # the fake stands where sharewrite.create_share would, and what is being
    # checked is purely how the agent files each SDK answer.

    original_create_share = sharewrite.create_share
    driver.simulate_config = False
    driver._resolved_host = "10.0.0.5"
    driver._known_esa_id = "drb000000TESTONLY"
    try:
        def sdk_answers(answer):
            def fake_create_share(*args, **kwargs):
                if isinstance(answer, Exception):
                    raise answer
                return answer
            sharewrite.create_share = fake_create_share

        # (a) the device answered and the read-back agrees.
        sdk_answers({"section": "shares", "result_code": 8589934592,
                     "acknowledged": True, "verified": True})
        status, reply = call("/api/shares/create",
                             body={"name": "SdkVerified"})
        check("SDK: acknowledged and verified is a plain success",
              status == 200 and reply.get("outcome") == "verified",
              (status, reply))

        # (b) the shape config_cmd returns when the device hung up and the
        # arbitrating read-back found the change anyway.
        sdk_answers({"section": "shares", "result_code": None,
                     "acknowledged": False, "verified": True,
                     "note": "the device never acknowledged this write"})
        status, reply = call("/api/shares/create",
                             body={"name": "SdkUnanswered"})
        check("SDK: unacknowledged but verified is STILL a success",
              status == 200 and reply.get("ok") is True, (status, reply))
        check("-- labelled verified_unacknowledged",
              reply.get("outcome") == "verified_unacknowledged", reply)

        # (d) via config_cmd's own type. It subclasses ConfigWriteError, so a
        # handler that caught that first would file this as a flat failure --
        # this check is what stops that regression.
        sdk_answers(config_cmd.ConfigWriteOutcomeUnknownError(
            "the write to section 'shares' WAS sent, then the device closed "
            "the connection without answering."))
        status, reply = call("/api/shares/create", body={"name": "SdkUnknown"})
        check("SDK: ConfigWriteOutcomeUnknownError is 502 UNKNOWN, not 502 failed",
              status == 502 and reply.get("outcome") == "unknown",
              (status, reply))
        check("-- and keeps the SDK's own words", "WAS sent" in reply.get("error", ""), reply)

        # (d) via sharewrite's translation of the same condition.
        sdk_answers(sharewrite.ShareWriteOutcomeUnknownError(
            "the write creating share 'X' WAS sent, but neither the device's "
            "acknowledgement nor a read-back could settle whether it applied."))
        status, reply = call("/api/shares/create",
                             body={"name": "SdkUnverifiable"})
        check("SDK: ShareWriteOutcomeUnknownError is 502 UNKNOWN too",
              status == 502 and reply.get("outcome") == "unknown",
              (status, reply))

        # (c) the genuine failure.
        sdk_answers(config_cmd.ConfigWriteError(
            "the device did not accept the write (Result None, expected "
            "8589934592). Nothing is confirmed changed."))
        status, reply = call("/api/shares/create", body={"name": "SdkRefused"})
        check("SDK: a refused write is 502 failed",
              status == 502 and reply.get("outcome") == "failed",
              (status, reply))

        # A device that is simply off. Nothing was sent, so this must NOT be
        # dressed up as "may have applied".
        sdk_answers(OSError("[WinError 10061] connection refused"))
        status, reply = call("/api/shares/create", body={"name": "SdkOffline"})
        check("SDK: an unreachable command port is 502 failed, not 'unknown'",
              status == 502 and reply.get("outcome") == "failed",
              (status, reply))
    finally:
        sharewrite.create_share = original_create_share
        driver.simulate_config = True
        driver._resolved_host = ""
        driver._known_esa_id = ""

    print("\n== the outcome vocabulary is pinned, not re-derived per caller ==")
    check("config_cmd's unknown-outcome error maps to 'unknown', by TYPE",
          config_cmd.ConfigWriteOutcomeUnknownError in api._UNCONFIRMED_WRITE_ERRORS,
          api._UNCONFIRMED_WRITE_ERRORS)
    check("the SDK's accepted-but-unverifiable error maps to 'unknown', by TYPE",
          sharewrite.ShareWriteOutcomeUnknownError in api._UNCONFIRMED_WRITE_ERRORS,
          api._UNCONFIRMED_WRITE_ERRORS)
    check("a plain verified result is 'verified'",
          api._write_outcome({"verified": True}) == (api.WRITE_VERIFIED, True))
    check("verified without an acknowledgement is its own outcome",
          api._write_outcome({"verified": True, "acknowledged": False})
          == (api.WRITE_UNACKNOWLEDGED, False))
    check("an empty <Result> is read as 'the device said nothing'",
          api._write_outcome({"verified": True, "result_code": None})[0]
          == api.WRITE_UNACKNOWLEDGED)
    check("a read-back that says unchanged is 'failed'",
          api._write_outcome({"verified": False})[0] == api.WRITE_FAILED)
    # The defensive half: an SDK shape nobody here has seen must land on
    # "unknown", never on "verified". One costs a re-read; the other lies.
    check("a shape this code does not recognise is 'unknown', never a success",
          api._write_outcome({"applied": True})[0] == api.WRITE_UNKNOWN)
    check("a non-dict result is 'unknown' too",
          api._write_outcome(None)[0] == api.WRITE_UNKNOWN)

    print("\n== the web dashboard carries the create/delete controls ==")
    status, page = call("/", raw=True)
    check("dashboard served", status == 200, status)
    check("it wires up both endpoints",
          b"/api/shares/create" in page and b"/api/shares/delete" in page)
    check("the delete gate sends what was typed as confirm_name",
          b"confirm_name" in page)
    check("the page owns the data loss in the same words as the Windows dialog",
          b"expected to destroy everything stored in" in page)
    check("it has a third colour for a write nobody could settle",
          b".set-result.warn" in page)
    check("-- chosen from the agent's outcome flag, not from the wording",
          b'j.outcome === "unknown" ? "warn" : false' in page)
finally:
    http_server.shutdown()
    monitor.stop()
    http_server.server_close()

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
