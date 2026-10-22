"""
Offline tests for updates.py -- the dormant update check.

The most important test in here is that a default install contacts NOTHING.
That is a security property, not a convenience: this runs unattended on a
machine holding somebody's only copy of their data, and it must not phone
anywhere its owner did not ask it to. So the network is not merely faked, it is
BOOBY-TRAPPED -- urllib is replaced with something that fails the test if it is
called at all.

    py test_updates.py
"""
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk")))

from drobo_agent import updates

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(label)


CURRENT = "0.1.0"


def manifest(**overrides):
    base = {
        "version": "0.2.0",
        "download_url": "https://example.invalid/DroboDashboardReborn-0.2.0.msi",
        "sha256": "a" * 64,
        "notes_url": "https://example.invalid/notes",
        "released": "2026-08-01",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
print("\n== DORMANT: a default install contacts nothing at all ==")
# Not "checks a default endpoint that 404s" -- does not run. Proven by making
# any network call an outright failure rather than by trusting a mock.
_real_urlopen = urllib.request.urlopen


def _explode(*a, **kw):
    raise AssertionError("the update check opened a network connection while disabled")


urllib.request.urlopen = _explode
try:
    for cfg in ({}, None, {"enabled": False},
                {"enabled": False, "manifest_url": "https://example.invalid/m.json"}):
        status = updates.check(cfg, CURRENT)
        check(f"{cfg} -> disabled, no network", status.state == updates.STATE_DISABLED, status)
        check(f"{cfg} -> not offering an update", status.update_available is False)
    off = updates.check({}, CURRENT)
    check("it says plainly that nothing is contacted", "Nothing is contacted" in off.reason, off.reason)
    check("-- and still reports the version we are on", off.current_version == CURRENT, off)
finally:
    urllib.request.urlopen = _real_urlopen

print("\n== enabled but misconfigured is surfaced, not silently ignored ==")
# Somebody turned this on and expects it to work; a silent no-op would hide
# their mistake indefinitely.
no_url = updates.check({"enabled": True, "manifest_url": ""}, CURRENT)
check("enabled with no URL is disabled with a reason",
      no_url.state == updates.STATE_DISABLED and "no manifest URL" in no_url.reason, no_url)

plain = updates.check({"enabled": True, "manifest_url": "http://example.invalid/m.json"}, CURRENT)
check("a plain-http manifest URL is refused", plain.state == updates.STATE_DISABLED, plain)
check("-- and says why it matters",
      "tampered" in plain.reason or "plaintext" in plain.reason, plain.reason)

print("\n== version comparison, including the one string compare gets wrong ==")
check("0.2.0 is newer than 0.1.0", updates.is_newer("0.2.0", "0.1.0"))
check("0.1.0 is not newer than 0.1.0", not updates.is_newer("0.1.0", "0.1.0"))
check("0.1.0 is not newer than 0.2.0", not updates.is_newer("0.1.0", "0.2.0"))
# The classic: "0.10.0" < "0.9.0" as strings, and that ships a downgrade.
check("0.10.0 IS newer than 0.9.0 (string compare says otherwise)",
      updates.is_newer("0.10.0", "0.9.0"))
check("1.2 and 1.2.0 are the same version", not updates.is_newer("1.2", "1.2.0"))
check("1.2.1 is newer than 1.2", updates.is_newer("1.2.1", "1.2"))
check("a build suffix does not change the release", not updates.is_newer("0.1.0-beta", "0.1.0"))
# "Cannot tell" must never present as "an update is available" -- that is the
# direction that gets somebody to install something.
check("an unparseable candidate is never 'newer'", not updates.is_newer("banana", "0.1.0"))
check("an unparseable current is never beaten", not updates.is_newer("0.2.0", "banana"))
check("empty strings are never newer", not updates.is_newer("", ""))

print("\n== a good manifest, offering a real update ==")
ON = {"enabled": True, "manifest_url": "https://example.invalid/m.json"}
found = updates.check(ON, CURRENT, fetch=lambda url, timeout: manifest())
check("an update is offered", found.state == updates.STATE_AVAILABLE, found)
check("update_available is the single field a UI needs", found.update_available is True)
check("the version is carried", found.latest.version == "0.2.0", found.latest)
check("the checksum is carried so a human can verify", found.latest.sha256 == "a" * 64)
# This is a background service. It fetches a small JSON file and stops.
check("it states outright that it will not install anything",
      "will not download" in found.reason, found.reason)
check("-- and tells you to check the download against the published SHA-256",
      "check the file you download against it" in found.reason, found.reason)

print("\n== up to date ==")
same = updates.check(ON, CURRENT, fetch=lambda url, timeout: manifest(version=CURRENT))
check("same version reports current", same.state == updates.STATE_CURRENT, same)
check("-- and offers nothing", same.update_available is False)
older = updates.check(ON, "9.9.9", fetch=lambda url, timeout: manifest())
check("an older manifest never downgrades you", older.state == updates.STATE_CURRENT, older)

print("\n== a release with no checksum is announced, but flagged as such ==")
# This RULE CHANGED on 2026-07-28 and the reasoning is worth keeping. It used to
# refuse outright: telling someone to run an unverifiable binary is the whole
# attack. That is right for a file we point at directly and wrong as a blanket
# rule -- the GitHub Releases API publishes no checksum at all, so the rule
# would have silently suppressed every update from the likeliest feed anyone
# actually uses. Suppressing the news is unhelpful; implying it is verified
# would be a lie. So: announce, and say which it is.
no_hash = updates.check(ON, CURRENT, fetch=lambda url, timeout: manifest(sha256=""))
check("an unverifiable release is still announced",
      no_hash.state == updates.STATE_AVAILABLE, no_hash)
check("-- and says plainly there is nothing to check it against",
      "No checksum is published" in no_hash.reason, no_hash.reason)
check("-- and still refuses to install it",
      "will not download" in no_hash.reason, no_hash.reason)

with_hash = updates.check(ON, CURRENT, fetch=lambda url, timeout: manifest())
check("a verifiable release says a SHA-256 IS published",
      "SHA-256 is published" in with_hash.reason, with_hash.reason)
check("the two are distinguishable, which is the whole point",
      no_hash.reason != with_hash.reason)

for bad, label in (("abc123", "too short"), ("z" * 64, "not hex")):
    got = updates.check(ON, CURRENT, fetch=lambda url, timeout, v=bad: manifest(sha256=v))
    check(f"a {label} checksum reads as unverified, never as verified",
          "No checksum is published" in got.reason, got.reason)

# http:// IS still refused outright. A link that can be rewritten in transit is
# a different problem from an unverified one, and stays a refusal.
plain_dl = updates.check(ON, CURRENT, fetch=lambda url, timeout: manifest(
    download_url="http://example.invalid/x.msi"))
check("a plain-http download link is still REFUSED, not annotated",
      plain_dl.state == updates.STATE_ERROR, plain_dl)
check("-- and the message names the download, not the manifest",
      "download link" in plain_dl.reason, plain_dl.reason)

print("\n== the GitHub Releases shape, read by the same engine ==")
# Two checkers used to exist: this one reading a hand-written manifest, and a
# PowerShell script in the installer reading GitHub Releases. One engine now
# reads both, detected by CONTENT -- a URL cannot tell you what it will return.
GH = {"tag_name": "v0.3.0", "html_url": "https://example.invalid/releases/v0.3.0",
      "published_at": "2026-08-01T10:00:00Z"}
gh = updates.check(ON, CURRENT, fetch=lambda url, timeout: GH)
check("a GitHub release is recognised", gh.state == updates.STATE_AVAILABLE, gh)
check("the v prefix is stripped from tag_name", gh.latest.version == "0.3.0", gh.latest)
check("html_url becomes the notes link", gh.latest.notes_url.endswith("v0.3.0"), gh.latest)
check("GitHub publishes no checksum, so it is flagged unverifiable",
      "No checksum is published" in gh.reason, gh.reason)
check("an older GitHub tag does not downgrade you",
      updates.check(ON, "9.9.9", fetch=lambda url, timeout: GH).state == updates.STATE_CURRENT)
check("an unprefixed tag works too",
      updates.check(ON, CURRENT, fetch=lambda url, timeout: {
          "tag_name": "0.3.0", "html_url": "https://x.invalid"}).latest.version == "0.3.0")

print("\n== channels ==")
CHANNELED = {"channels": {"stable": manifest(version="0.2.0"),
                          "beta": manifest(version="0.3.0")}}
stable = updates.check({**ON, "channel": "stable"}, CURRENT,
                       fetch=lambda url, timeout: CHANNELED)
check("the stable channel is read", stable.latest.version == "0.2.0", stable.latest)
beta = updates.check({**ON, "channel": "beta"}, CURRENT,
                     fetch=lambda url, timeout: CHANNELED)
check("the beta channel is read", beta.latest.version == "0.3.0", beta.latest)
missing = updates.check({**ON, "channel": "nightly"}, CURRENT,
                        fetch=lambda url, timeout: CHANNELED)
check("an absent channel is an error, not a silent fallback to another one",
      missing.state == updates.STATE_ERROR, missing)

print("\n== a broken or hostile endpoint cannot take the agent down ==")
def _boom(url, timeout):
    raise urllib.error.URLError("simulated: no route to host")


down = updates.check(ON, CURRENT, fetch=_boom)
check("an unreachable endpoint is an error, not an exception", down.state == updates.STATE_ERROR, down)
check("-- and never claims an update exists", down.update_available is False)

for bad, label in (("not a dict", "a JSON array"), (None, "null"), (42, "a number")):
    got = updates.check(ON, CURRENT, fetch=lambda url, timeout, v=bad: v)
    check(f"{label} instead of an object is an error", got.state == updates.STATE_ERROR, got)

no_version = updates.check(ON, CURRENT, fetch=lambda url, timeout: {"download_url": "https://x"})
check("a manifest with no version is an error", no_version.state == updates.STATE_ERROR, no_version)


def _huge(url, timeout):
    raise ValueError(f"manifest larger than {updates.MAX_MANIFEST_BYTES} bytes; refusing it")


big = updates.check(ON, CURRENT, fetch=_huge)
check("an oversized body is refused rather than parsed", big.state == updates.STATE_ERROR, big)


# ---------------------------------------------------------------------------
print("\n== the Check for Updates shortcut's script runs on an isolated Python ==")
# The installer's shortcut runs agent/check_updates.py on the bundled
# embeddable Python, whose python314._pth runs it isolated with safe_path on:
# nothing -- not the working directory, not the script's own folder -- is on
# sys.path unless the script puts it there. Until 2026-09-26 the shortcut ran
# `python -m drobo_agent.updates` from inside agent/, which that runtime cannot
# import, so it failed in every installed copy and nothing here noticed. `-I`
# gives an ordinary interpreter the same isolation (-E -s -P), so this runs in
# every suite sweep without building an installer.
import json
import secrets
import shutil
import subprocess
import tempfile

_agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_script = os.path.join(_agent_dir, "check_updates.py")
check("agent/check_updates.py is present", os.path.exists(_script), _script)


def _isolated(args, cwd):
    return subprocess.run([sys.executable, "-I", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


with tempfile.TemporaryDirectory() as _tmp:
    _cfg = os.path.join(_tmp, "config.json")
    with open(_cfg, "w", encoding="utf-8") as fh:
        # A token already present, so the run does not stop to generate one.
        json.dump({"agent": {"token": secrets.token_urlsafe(24)}}, fh)

    # The old way first, so this section is known to reproduce the failure
    # rather than passing on an interpreter that never had it. -P arrived in
    # Python 3.11; on an older one -I leaves the working directory on the path.
    if hasattr(sys.flags, "safe_path"):
        old = _isolated(["-m", "drobo_agent.updates", "--config", _cfg], cwd=_agent_dir)
        check("the old `-m drobo_agent.updates` from inside agent/ fails when isolated",
              old.returncode != 0 and "No module named" in old.stderr,
              (old.returncode, old.stderr))
    else:
        print("  SKIP  the old invocation's failure needs -P (Python 3.11+) to reproduce")

    # From an unrelated folder: where it is started from must not matter.
    ok = _isolated([_script, "--config", _cfg], cwd=_tmp)
    check("check_updates.py, run by path from an unrelated folder, exits 0",
          ok.returncode == 0, (ok.returncode, ok.stdout, ok.stderr))
    check("-- answering that update checking is switched off",
          "switched off" in ok.stdout, ok.stdout)
    check("-- with no traceback", "Traceback" not in ok.stderr, ok.stderr)

    # A failure gets an exit status of its own. Python's 1 for a crash is the
    # same number as "an update is available", and the .bat reads the number.
    with open(_cfg, "w", encoding="utf-8") as fh:
        fh.write('{ "updates": { "enabled": true, ')
    bad = _isolated([_script, "--config", _cfg], cwd=_tmp)
    check("a config.json that is not valid JSON exits 3, not 1",
          bad.returncode == 3, (bad.returncode, bad.stdout, bad.stderr))
    check("-- and says what is wrong with it", "not valid JSON" in bad.stderr, bad.stderr)

    # Windows PowerShell 5.1's `>` and Out-File write UTF-16, the other
    # way a hand-edited config.json goes wrong. No URL is set, so
    # nothing can be contacted even if the file were read.
    with open(_cfg, "w", encoding="utf-16") as fh:
        json.dump({"agent": {"token": secrets.token_urlsafe(24)},
                   "updates": {"enabled": True}}, fh)
    wide = _isolated([_script, "--config", _cfg], cwd=_tmp)
    check("a UTF-16 config.json exits 3, not 0 as 'switched off'",
          wide.returncode == 3, (wide.returncode, wide.stdout, wide.stderr))
    check("-- and says it is not UTF-8", "not UTF-8" in wide.stderr,
          wide.stderr)

    _lone = os.path.join(_tmp, "lone")
    os.makedirs(_lone)
    shutil.copy(_script, _lone)
    gone = _isolated([os.path.join(_lone, "check_updates.py")], cwd=_tmp)
    check("with the agent's package missing, it exits 3, not 1",
          gone.returncode == 3, (gone.returncode, gone.stderr))
    check("-- and the traceback names the missing package",
          "No module named 'drobo_agent'" in gone.stderr, gone.stderr)


# ---------------------------------------------------------------------------
print("\n== the release version agrees with itself, everywhere it is written ==")
# This suite is where the check belongs: updates.check() compares the running
# version against a published one, so a tree whose own copies of that number
# disagree makes the comparison meaningless before the network is ever reached.
#
# Seven places across three languages, and nothing compared them until now.
# windows/README.md's "Versioning" section says "if you bump one, bump them
# all", which is advice, not a gate. It has already been wrong in public:
# release 1.0.0 shipped sdk/pyproject.toml still saying 0.1.0, so the metadata a
# reader installs disagreed with the release it came from.
#
# scripts/check_versions.py holds the rule, so a human can run it on its own
# before a release; this runs it on every suite sweep.
import importlib.util

_repo = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_checker_path = os.path.join(_repo, "scripts", "check_versions.py")
check("scripts/check_versions.py is present", os.path.exists(_checker_path), _checker_path)

if os.path.exists(_checker_path):
    _spec = importlib.util.spec_from_file_location("_check_versions", _checker_path)
    _checker = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_checker)

    _found, _unreadable = _checker.collect_versions()
    check("every file that states the version could be read", _unreadable == [], _unreadable)
    check("-- and every one of them actually states it",
          all(v is not None for v in _found.values()),
          [k for k, v in _found.items() if v is None])
    check("all seven places are checked, not a subset", len(_found) == 7, sorted(_found))
    check("every place agrees on one number", _checker.find_problems() == [], _checker.find_problems())

    # The agent's own two copies are the pair most easily forgotten, because
    # neither is derived from the other: api.VERSION is what /api/ping and the
    # update check report, __version__ is what a Python caller reads.
    from drobo_agent import api as _api
    from drobo_agent import __version__ as _pkg_version
    check("the VERSION the update check compares is the package's own",
          _api.VERSION == _pkg_version, (_api.VERSION, _pkg_version))
    check("-- and it is the number the files on disk declare",
          _api.VERSION == _found.get("csproj <Version>"),
          (_api.VERSION, _found.get("csproj <Version>")))

    # A disagreement must FAIL, not merely be listed. Proven by pointing one
    # entry of the rule at a place that says something else on purpose -- this
    # file's own CURRENT, which is 0.1.0 -- so the real tree is never edited to
    # test the thing that guards it.
    _original = _checker.VERSION_PLACES[:]
    try:
        _checker.VERSION_PLACES = _original[:-1] + [(
            "a deliberately different place",
            os.path.join("agent", "tests", "test_updates.py"),
            '^CURRENT = "([^"]+)"',
        )]
        _seeded = _checker.find_problems()
    finally:
        _checker.VERSION_PLACES = _original
    check("a place that disagrees is reported", len(_seeded) == 1, _seeded)
    check("-- naming what it found and what it expected",
          _seeded and CURRENT in _seeded[0] and _found["csproj <Version>"] in _seeded[0],
          _seeded)
    check("-- and the real tree is unaffected by that probe",
          _checker.find_problems() == [], _checker.find_problems())

    # A four-part AssemblyVersion is checked against the three-part number, not
    # merely against the other four-part one: 1.0.0 with 1.0.1.0 twice is still
    # a release that lies about itself.
    _original = _checker.VERSION_PLACES[:]
    try:
        _checker.VERSION_PLACES = [
            (label, path, pattern) for (label, path, pattern) in _original
            if label != "csproj <AssemblyVersion>"
        ] + [("csproj <AssemblyVersion>",
              os.path.join("agent", "tests", "test_updates.py"),
              '^CURRENT = "([^"]+)"')]
        _four = _checker.find_problems()
    finally:
        _checker.VERSION_PLACES = _original
    check("a four-part number that does not extend the three-part one is caught",
          len(_four) == 1 and "fourth field" in _four[0], _four)


print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
