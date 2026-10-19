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

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ("  " + str(extra) if extra and not cond else ""))
    if not cond:
        fails.append(label)


tmp = tempfile.mkdtemp(prefix="shares-api-")
# Removed when the script exits, however it exits: a failed check or an
# exception as well as a clean pass. Until 2026-09-16 every run left this
# folder behind in TEMP.
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
cfg_path = os.path.join(tmp, "config.json")
PORT = 7439
BASE = f"http://127.0.0.1:{PORT}"

with open(cfg_path, "w") as fh:
    json.dump({
        "agent": {"bind": "127.0.0.1", "port": PORT, "poll_seconds": 1},
        "drobo": {"driver": "mock", "simulate_config": True},
        "photos": {"enabled": False},
    }, fh)

cfg = config.load(cfg_path)
TOKEN = cfg["agent"]["token"]

mon = Monitor(cfg, notify.build(cfg["notify"]))
photos = PhotoStore(cfg["photos"])
photos.load()
mon.start()
httpd = api.serve(cfg, mon, photos)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(1.0)


def call(path, body=None, method=None, token=TOKEN, raw=False):
    headers = {"X-Agent-Token": token} if token else {}
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def listed_names():
    s, b = call("/api/shares")
    return s, [sh["name"] for sh in b.get("shares", [])], b


def version_basis():
    """The simulated device's own config version -- bumped by every simulated
    write, so an unchanged value is proof nothing touched the (mock) device."""
    return mon.driver._shares["DRINASConfig"]["DRIShareConfig"]["ConfigVersionBasis"]


driver = mon.driver
check("the simulated driver is the one under test",
      getattr(driver, "simulate_config", False) is True, type(driver).__name__)

# /api/shares lists shares sorted by name, so expectations are sorted too.
BASELINE = ["Documents", "Photo Library", "Private", "TimeMachine"]

try:
    print("\n== baseline: /api/shares lists the simulated shares (and fills the cache) ==")
    s, names, b = listed_names()
    check("shares 200", s == 200, (s, b))
    check("the four simulated shares are listed", names == BASELINE, names)

    print("\n== the simulated Drobo has one account: admin / admin ==")
    advice = b.get("advice", {})
    check("the advice names the account", advice.get("accounts") == ["admin"], advice)
    check("so signing in is offered", advice.get("can_sign_in") is True, advice)

    def sign_in(share, username, password, token=TOKEN):
        return call("/api/mock/sign-in",
                    body={"share": share, "username": username, "password": password},
                    token=token)

    s, r = sign_in("Documents", "admin", "admin")
    check("admin / admin opens Documents", s == 200 and r.get("ok") is True
          and r.get("win32_error") == 0, (s, r))
    s, r = sign_in("documents", "ADMIN", "admin")
    check("share and user names are case-insensitive, like SMB", r.get("ok") is True, r)
    s, r = sign_in("Documents", "admin", "Admin")
    check("the password is case-sensitive", r.get("ok") is False
          and r.get("win32_error") == 1326, r)
    s, r = sign_in("Documents", "someone", "admin")
    check("an unknown user is a logon failure", r.get("win32_error") == 1326, r)
    s, r = sign_in("Documents", "admin", "hunter2-probe")
    check("a wrong password never comes back in the answer",
          "hunter2-probe" not in json.dumps(r), r)
    s, r = sign_in("Private", "admin", "admin")
    check("admin reaches Private, where Everyone has no access", r.get("ok") is True, r)
    s, r = sign_in("Nowhere", "admin", "admin")
    check("an unknown share is 'network name not found'", r.get("win32_error") == 67, r)
    s, r = call("/api/mock/sign-in", body={"share": "Documents", "password": "admin"})
    check("a missing username is a 400", s == 400, (s, r))
    s, r = call("/api/mock/sign-in", body=b"{not json")
    check("bad JSON is a 400", s == 400, (s, r))
    s, r = sign_in("Documents", "admin", "admin", token="wrong-token")
    check("without the agent token it is refused", s == 401, (s, r))

    print("\n== create: the new share appears across the cache invalidation ==")
    s, b = call("/api/shares/create", body={"name": "Backups"})
    check("create 200 with ok", s == 200 and b.get("ok") is True, (s, b))
    check("reported as simulated", b.get("simulated") is True, b)
    check("verified true (the pinned contract's read-back claim)",
          b.get("verified") is True, b)
    check("the message says the share starts with no access",
          "no access" in b.get("message", ""), b.get("message"))

    # This read happens well inside the 60s shares TTL, and the cache was
    # filled by the baseline read above -- so a listing that carries the new
    # share is proof the write invalidated the cache rather than leaving a
    # stale copy that makes a verified write look failed.
    s, names, b = listed_names()
    check("the new share is listed immediately, not after the TTL",
          names == sorted(BASELINE + ["Backups"]), names)
    backups = [sh for sh in b.get("shares", []) if sh["name"] == "Backups"]
    check("the new share has no users -- no access for anyone yet",
          backups and backups[0].get("users") == [], backups)

    print("\n== the flow the create message promises: grant access afterwards ==")
    # A new share is stored with an EMPTY <ShareUsers/> -- None once parsed --
    # and the create message tells the user to grant access next. That is the
    # one share on the device whose users container is empty, so it is also the
    # one the access write must handle.
    s, b = call("/api/config/share-access",
                body={"share": "Backups", "user": "Everyone", "access": "1"})
    check("granting access on a brand-new share works", s == 200 and b.get("ok"), (s, b))
    s, names, b = listed_names()
    backups = [sh for sh in b.get("shares", []) if sh["name"] == "Backups"]
    check("-- and the user now reads back on it",
          backups and [u["name"] for u in backups[0].get("users", [])] == ["Everyone"],
          backups)

    print("\n== delete: the share disappears the same way ==")
    s, b = call("/api/shares/delete",
                body={"name": "Backups", "confirm_name": "Backups"})
    check("delete 200 with ok", s == 200 and b.get("ok") is True, (s, b))
    check("reported as simulated", b.get("simulated") is True, b)
    check("verified true", b.get("verified") is True, b)
    check("the message owns the data loss",
          "everything it held" in b.get("message", ""), b.get("message"))
    s, names, b = listed_names()
    check("the deleted share is gone immediately", names == BASELINE, names)

    print("\n== create refusals: 400, before anything changes ==")
    basis = version_basis()
    s, b = call("/api/shares/create", body={"name": "documents"})
    check("a case-fold duplicate is refused with 400", s == 400, (s, b))
    check("-- naming the existing share",
          "already exists" in b.get("error", ""), b.get("error"))

    s, b = call("/api/shares/create", body={"name": "bad*name"})
    check("a name outside the charset is refused with 400", s == 400, (s, b))
    check("-- stating the rule", "1-27" in b.get("error", ""), b.get("error"))

    s, b = call("/api/shares/create", body={"name": " Backups"})
    check("surrounding whitespace is refused, not stripped", s == 400, (s, b))
    check("-- and says why", "whitespace" in b.get("error", ""), b.get("error"))

    s, b = call("/api/shares/create", body={"name": "-leading-hyphen"})
    check("a non-alphanumeric first character is refused with 400", s == 400, (s, b))

    s, b = call("/api/shares/create", body={"name": "x" * 28})
    check("a 28-character name is refused with 400", s == 400, (s, b))

    s, b = call("/api/shares/create", body={"name": ""})
    check("an empty name is refused with 400", s == 400, (s, b))

    s, b = call("/api/shares/create", body=b"{not json")
    check("malformed JSON is refused with 400", s == 400, (s, b))

    check("no refused create touched the simulated device",
          version_basis() == basis, (basis, version_basis()))

    print("\n== delete refusals: confirm_name gates the device ==")
    s, b = call("/api/shares/delete",
                body={"name": "Documents", "confirm_name": "documents"})
    check("a confirm_name differing only by case is refused with 400", s == 400, (s, b))
    check("-- saying nothing was sent",
          "nothing was sent" in b.get("error", ""), b.get("error"))

    s, b = call("/api/shares/delete", body={"name": "Documents"})
    check("a missing confirm_name is refused with 400", s == 400, (s, b))
    check("-- explaining the retype rule",
          "typed twice" in b.get("error", ""), b.get("error"))

    # The mismatch 400 comes from the parse step, before the simulate fork or
    # any device lookup -- so even an unknown share fails on the CONFIRMATION,
    # proving the gate runs first.
    s, b = call("/api/shares/delete",
                body={"name": "Nope", "confirm_name": "nope"})
    check("the confirmation is checked before the share is even looked up",
          s == 400 and "confirm_name" in b.get("error", ""), (s, b))

    s, b = call("/api/shares/delete", body={"name": "Nope", "confirm_name": "Nope"})
    check("a confirmed delete of an unknown share is refused with 400", s == 400, (s, b))
    check("-- listing what the device has",
          "no share named" in b.get("error", ""), b.get("error"))

    check("no refused delete touched the simulated device",
          version_basis() == basis, (basis, version_basis()))
    s, names, b = listed_names()
    check("every share is still there", names == BASELINE, names)

    print("\n== auth: 401s are clean even with a request body ==")
    s, b = call("/api/shares/create", body={"name": "Sneaky"}, token="")
    check("unauthenticated create answers a clean 401", s == 401, (s, b))
    check("-- with the reason in the body", "X-Agent-Token" in b.get("error", ""), b)
    s, b = call("/api/shares/delete",
                body={"name": "Documents", "confirm_name": "Documents"}, token="wrong")
    check("wrong-token delete answers a clean 401", s == 401, (s, b))
    check("...and neither reached the driver", version_basis() == basis,
          (basis, version_basis()))
    s, names, b = listed_names()
    check("the share list is unchanged", names == BASELINE, names)

    print("\n== the simulator mutates per-instance state only ==")
    call("/api/shares/create", body={"name": "Scratch"})
    fresh = MockDriver({"simulate_config": True})
    fresh_names = [sh["ShareName"] for sh in
                   fresh._shares["DRINASConfig"]["DRIShareConfig"]["Shares"]["Share"]]
    check("a fresh MockDriver has only the pristine shares",
          sorted(fresh_names) == BASELINE, fresh_names)
    class_names = [sh["ShareName"] for sh in
                   MockDriver._MOCK_SHARES["DRINASConfig"]["DRIShareConfig"]["Shares"]["Share"]]
    check("the class-level pristine table is untouched",
          sorted(class_names) == BASELINE, class_names)

    print("\n== all four write outcomes, through the mock's injected endings ==")
    # The point of this whole section is the two middles. On 2026-08-25 a real
    # 5N was sent a share create, closed the socket without answering, and had
    # created the share -- and every layer above reported a failure. So each
    # ending below is checked on BOTH halves: what the agent says, and what the
    # simulated device actually holds afterwards.

    def arm(mode):
        s, b = call("/api/mock/write/" + mode, method="POST")
        return s == 200, b

    ok, b = arm("hangs_up")
    check("the mock accepts an injected write ending", ok, b)

    s, b = call("/api/shares/create", body={"name": "Unanswered"})
    check("a write the Drobo never acknowledged is a SUCCESS, not a failure",
          s == 200 and b.get("ok") is True, (s, b))
    check("-- labelled as its own outcome",
          b.get("outcome") == "verified_unacknowledged", b)
    check("-- with acknowledged false, the field a UI reads",
          b.get("acknowledged") is False, b)
    check("-- still verified: the read-back is what settled it",
          b.get("verified") is True, b)
    check("-- and the message says the Drobo did not answer",
          "did not answer" in b.get("message", ""), b.get("message"))
    check("-- and says the read-back is why we know it worked",
          "reading the setting back" in b.get("message", ""), b.get("message"))
    s, names, _ = listed_names()
    check("-- and the share really is there, which is the whole point",
          "Unanswered" in names, names)

    ok, b = arm("unconfirmable")
    check("the mock accepts the unsettleable ending", ok, b)
    basis = version_basis()
    s, b = call("/api/shares/create", body={"name": "Unsettled"})
    check("a write nobody can settle is 502 -- never a 200", s == 502, (s, b))
    check("-- labelled unknown, so a UI paints it amber not red",
          b.get("outcome") == "unknown", b)
    check("-- and NEVER claims it worked", b.get("ok") is not True, b)
    check("-- the wording says it may have been applied",
          "may well have been" in b.get("error", ""), b.get("error"))
    check("-- and says to re-read before sending it again",
          "before sending this again" in b.get("error", ""), b.get("error"))
    # The trap this outcome exists for: the change IS on the device while the
    # answer says nothing of the sort. A UI that renders this as "failed"
    # invites a retry of a write that already happened.
    check("-- and the write had in fact applied", version_basis() != basis,
          (basis, version_basis()))
    s, names, _ = listed_names()
    check("-- the share is listed, exactly as the hardware case behaved",
          "Unsettled" in names, names)

    ok, b = arm("refused")
    check("the mock accepts the refusing ending", ok, b)
    basis = version_basis()
    s, b = call("/api/shares/create", body={"name": "Refused"})
    check("a refused write is 502", s == 502, (s, b))
    check("-- labelled failed", b.get("outcome") == "failed", b)
    check("-- saying nothing is confirmed changed",
          "Nothing is confirmed changed" in b.get("error", ""), b.get("error"))
    check("-- and it left the simulated device untouched",
          version_basis() == basis, (basis, version_basis()))
    s, names, _ = listed_names()
    check("-- no share appeared", "Refused" not in names, names)

    # A refusal is the device's answer; a bad name never reaches it. The
    # injected ending must not turn the caller's own mistake into a 502.
    s, b = call("/api/shares/create", body={"name": "bad*name"})
    check("a parameter refusal is still 400 while a write fault is armed",
          s == 400, (s, b))

    ok, b = arm("none")
    check("the mock accepts being put back to healthy", ok, b)
    s, b = call("/api/shares/create", body={"name": "Healthy"})
    check("a healthy write is 200 with ok", s == 200 and b.get("ok") is True, (s, b))
    check("-- labelled verified", b.get("outcome") == "verified", b)
    check("-- acknowledged true", b.get("acknowledged") is True, b)

    s, b = call("/api/shares/delete",
                body={"name": "Healthy", "confirm_name": "Healthy"})
    check("delete reports the same vocabulary", b.get("outcome") == "verified", b)

    # Delete is destructive, so its unacknowledged case matters most of all:
    # reporting a completed delete as a failure is how a share gets deleted
    # twice, and the second attempt would fail on a share that is already gone.
    arm("hangs_up")
    s, b = call("/api/shares/delete",
                body={"name": "Unanswered", "confirm_name": "Unanswered"})
    check("an unacknowledged DELETE is a success too",
          s == 200 and b.get("outcome") == "verified_unacknowledged", (s, b))
    s, names, _ = listed_names()
    check("-- and the share really is gone", "Unanswered" not in names, names)

    s, b = call("/api/mock/write/nonsense", method="POST")
    check("an unknown write ending is refused with 400", s == 400, (s, b))
    check("-- listing the ones that exist", "hangs_up" in b.get("error", ""), b)

    s, b = call("/api/mock/reset", method="POST")
    check("reset clears the injected write ending too", s == 200, (s, b))
    check("-- proven by the next write being a plain success",
          call("/api/shares/create", body={"name": "AfterReset"})[1].get("outcome")
          == "verified")

    print("\n== the SAME four outcomes off the real SDK, without hardware ==")
    # Everything above drives the simulated fork. This drives the HARDWARE fork
    # -- the one that calls the SDK -- with the SDK function replaced by
    # something that answers the way the SDK documents. No socket is opened:
    # the fake stands where sharewrite.create_share would, and what is being
    # checked is purely how the agent files each SDK answer.
    from drobo_agent import api as api_mod                          # noqa: E402
    from drobo_nasd import config_cmd, sharewrite                   # noqa: E402

    real_create = sharewrite.create_share
    driver.simulate_config = False
    driver._resolved_host = "10.0.0.5"
    driver._known_esa_id = "drb000000TESTONLY"
    try:
        def sdk_answers(answer):
            def fake(*a, **kw):
                if isinstance(answer, Exception):
                    raise answer
                return answer
            sharewrite.create_share = fake

        # (a) the device answered and the read-back agrees.
        sdk_answers({"section": "shares", "result_code": 8589934592,
                     "acknowledged": True, "verified": True})
        s, b = call("/api/shares/create", body={"name": "SdkVerified"})
        check("SDK: acknowledged and verified is a plain success",
              s == 200 and b.get("outcome") == "verified", (s, b))

        # (b) the shape config_cmd returns when the device hung up and the
        # arbitrating read-back found the change anyway.
        sdk_answers({"section": "shares", "result_code": None,
                     "acknowledged": False, "verified": True,
                     "note": "the device never acknowledged this write"})
        s, b = call("/api/shares/create", body={"name": "SdkUnanswered"})
        check("SDK: unacknowledged but verified is STILL a success",
              s == 200 and b.get("ok") is True, (s, b))
        check("-- labelled verified_unacknowledged",
              b.get("outcome") == "verified_unacknowledged", b)

        # (d) via config_cmd's own type. It subclasses ConfigWriteError, so a
        # handler that caught that first would file this as a flat failure --
        # this check is what stops that regression.
        sdk_answers(config_cmd.ConfigWriteOutcomeUnknownError(
            "the write to section 'shares' WAS sent, then the device closed "
            "the connection without answering."))
        s, b = call("/api/shares/create", body={"name": "SdkUnknown"})
        check("SDK: ConfigWriteOutcomeUnknownError is 502 UNKNOWN, not 502 failed",
              s == 502 and b.get("outcome") == "unknown", (s, b))
        check("-- and keeps the SDK's own words", "WAS sent" in b.get("error", ""), b)

        # (d) via sharewrite's translation of the same condition.
        sdk_answers(sharewrite.ShareWriteOutcomeUnknownError(
            "the write creating share 'X' WAS sent, but neither the device's "
            "acknowledgement nor a read-back could settle whether it applied."))
        s, b = call("/api/shares/create", body={"name": "SdkUnverifiable"})
        check("SDK: ShareWriteOutcomeUnknownError is 502 UNKNOWN too",
              s == 502 and b.get("outcome") == "unknown", (s, b))

        # (c) the genuine failure.
        sdk_answers(config_cmd.ConfigWriteError(
            "the device did not accept the write (Result None, expected "
            "8589934592). Nothing is confirmed changed."))
        s, b = call("/api/shares/create", body={"name": "SdkRefused"})
        check("SDK: a refused write is 502 failed",
              s == 502 and b.get("outcome") == "failed", (s, b))

        # A device that is simply off. Nothing was sent, so this must NOT be
        # dressed up as "may have applied".
        sdk_answers(OSError("[WinError 10061] connection refused"))
        s, b = call("/api/shares/create", body={"name": "SdkOffline"})
        check("SDK: an unreachable command port is 502 failed, not 'unknown'",
              s == 502 and b.get("outcome") == "failed", (s, b))
    finally:
        sharewrite.create_share = real_create
        driver.simulate_config = True
        driver._resolved_host = ""
        driver._known_esa_id = ""

    print("\n== the outcome vocabulary is pinned, not re-derived per caller ==")
    check("config_cmd's unknown-outcome error maps to 'unknown', by TYPE",
          config_cmd.ConfigWriteOutcomeUnknownError in api_mod._UNCONFIRMED_WRITE_ERRORS,
          api_mod._UNCONFIRMED_WRITE_ERRORS)
    check("the SDK's accepted-but-unverifiable error maps to 'unknown', by TYPE",
          sharewrite.ShareWriteOutcomeUnknownError in api_mod._UNCONFIRMED_WRITE_ERRORS,
          api_mod._UNCONFIRMED_WRITE_ERRORS)
    check("a plain verified result is 'verified'",
          api_mod._write_outcome({"verified": True}) == (api_mod.WRITE_VERIFIED, True))
    check("verified without an acknowledgement is its own outcome",
          api_mod._write_outcome({"verified": True, "acknowledged": False})
          == (api_mod.WRITE_UNACKNOWLEDGED, False))
    check("an empty <Result> is read as 'the device said nothing'",
          api_mod._write_outcome({"verified": True, "result_code": None})[0]
          == api_mod.WRITE_UNACKNOWLEDGED)
    check("a read-back that says unchanged is 'failed'",
          api_mod._write_outcome({"verified": False})[0] == api_mod.WRITE_FAILED)
    # The defensive half: an SDK shape nobody here has seen must land on
    # "unknown", never on "verified". One costs a re-read; the other lies.
    check("a shape this code does not recognise is 'unknown', never a success",
          api_mod._write_outcome({"applied": True})[0] == api_mod.WRITE_UNKNOWN)
    check("a non-dict result is 'unknown' too",
          api_mod._write_outcome(None)[0] == api_mod.WRITE_UNKNOWN)

    print("\n== the web dashboard carries the create/delete controls ==")
    s, page = call("/", raw=True)
    check("dashboard served", s == 200, s)
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
    httpd.shutdown()
    mon.stop()
    httpd.server_close()

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
