"""
Offline tests for updates.py -- the dormant update check.

The most important test in here is that a default install contacts
NOTHING. That is a security property, not a convenience: this runs
unattended on a machine holding somebody's only copy of their data, and
it must not phone anywhere its owner did not ask it to. So the network
is not merely faked, it is BOOBY-TRAPPED -- urllib is replaced with
something that fails the test if it is called at all.

    py test_updates.py
"""
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "sdk"
        )
    ),
)

from drobo_agent import updates  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + label
        + (f"  {failure_detail}" if failure_detail and not condition else "")
    )
    if not condition:
        failures.append(label)


CURRENT = "0.1.0"


def manifest(**overrides):
    manifest_fields = {
        "version": "0.2.0",
        "download_url": (
            "https://example.invalid/DroboDashboardReborn-0.2.0.msi"
        ),
        "sha256": "a" * 64,
        "notes_url": "https://example.invalid/notes",
        "released": "2026-08-01",
    }
    manifest_fields.update(overrides)
    return manifest_fields


# ---------------------------------------------------------------------------
print("\n== DORMANT: a default install contacts nothing at all ==")
# Not "checks a default endpoint that 404s" -- does not run. Proven by
# making any network call an outright failure rather than by trusting a
# mock.
_real_urlopen = urllib.request.urlopen


def _urlopen_forbidden(*args, **kwargs):
    raise AssertionError(
        "the update check opened a network connection while disabled"
    )


urllib.request.urlopen = _urlopen_forbidden
try:
    for updates_config in ({}, None, {"enabled": False},
                           {"enabled": False,
                            "manifest_url": "https://example.invalid/m.json"}):
        status = updates.check(updates_config, CURRENT)
        check(
            f"{updates_config} -> disabled, no network",
            status.state == updates.STATE_DISABLED,
            status,
        )
        check(
            f"{updates_config} -> not offering an update",
            status.update_available is False,
        )
    disabled_status = updates.check({}, CURRENT)
    check(
        "it says plainly that nothing is contacted",
        "Nothing is contacted" in disabled_status.reason,
        disabled_status.reason,
    )
    check(
        "-- and still reports the version we are on",
        disabled_status.current_version == CURRENT,
        disabled_status,
    )
finally:
    urllib.request.urlopen = _real_urlopen

print("\n== enabled but misconfigured is surfaced, not silently ignored ==")
# Somebody turned this on and expects it to work; a silent no-op would
# hide their mistake indefinitely.
no_url = updates.check({"enabled": True, "manifest_url": ""}, CURRENT)
check(
    "enabled with no URL is disabled with a reason",
    no_url.state == updates.STATE_DISABLED
    and "no manifest URL" in no_url.reason,
    no_url,
)

plain_http_manifest_status = updates.check(
    {"enabled": True, "manifest_url": "http://example.invalid/m.json"}, CURRENT
)
check(
    "a plain-http manifest URL is refused",
    plain_http_manifest_status.state == updates.STATE_DISABLED,
    plain_http_manifest_status,
)
check("-- and says why it matters",
      "tampered" in plain_http_manifest_status.reason
      or "plaintext" in plain_http_manifest_status.reason,
      plain_http_manifest_status.reason)

print(
    "\n== version comparison, including the one string compare gets wrong =="
)
check("0.2.0 is newer than 0.1.0", updates.is_newer("0.2.0", "0.1.0"))
check("0.1.0 is not newer than 0.1.0", not updates.is_newer("0.1.0", "0.1.0"))
check("0.1.0 is not newer than 0.2.0", not updates.is_newer("0.1.0", "0.2.0"))
# The classic: "0.10.0" < "0.9.0" as strings, and that ships a
# downgrade.
check("0.10.0 IS newer than 0.9.0 (string compare says otherwise)",
      updates.is_newer("0.10.0", "0.9.0"))
check(
    "1.2 and 1.2.0 are the same version", not updates.is_newer("1.2", "1.2.0")
)
check("1.2.1 is newer than 1.2", updates.is_newer("1.2.1", "1.2"))
check(
    "a build suffix does not change the release",
    not updates.is_newer("0.1.0-beta", "0.1.0"),
)
# "Cannot tell" must never present as "an update is available" -- that
# is the direction that gets somebody to install something.
check(
    "an unparseable candidate is never 'newer'",
    not updates.is_newer("banana", "0.1.0"),
)
check(
    "an unparseable current is never beaten",
    not updates.is_newer("0.2.0", "banana"),
)
check("empty strings are never newer", not updates.is_newer("", ""))

print("\n== a good manifest, offering a real update ==")
ENABLED_CONFIG = {"enabled": True,
                  "manifest_url": "https://example.invalid/m.json"}
found = updates.check(ENABLED_CONFIG, CURRENT,
                      fetch=lambda url, timeout: manifest())
check("an update is offered", found.state == updates.STATE_AVAILABLE, found)
check(
    "update_available is the single field a UI needs",
    found.update_available is True,
)
check("the version is carried", found.latest.version == "0.2.0", found.latest)
check(
    "the checksum is carried so a human can verify",
    found.latest.sha256 == "a" * 64,
)
# This is a background service. It fetches a small JSON file and stops.
check("it states outright that it will not install anything",
      "will not download" in found.reason, found.reason)
check("-- and tells you to check the download against the published SHA-256",
      "check the file you download against it" in found.reason, found.reason)

print("\n== up to date ==")
same = updates.check(
    ENABLED_CONFIG,
    CURRENT,
    fetch=lambda url, timeout: manifest(version=CURRENT),
)
check(
    "same version reports current", same.state == updates.STATE_CURRENT, same
)
check("-- and offers nothing", same.update_available is False)
older = updates.check(ENABLED_CONFIG, "9.9.9",
                      fetch=lambda url, timeout: manifest())
check(
    "an older manifest never downgrades you",
    older.state == updates.STATE_CURRENT,
    older,
)

print("\n== a release with no checksum is announced, but flagged as such ==")
# This RULE CHANGED on 2026-07-28 and the reasoning is worth keeping. It
# used to refuse outright: telling someone to run an unverifiable binary
# is the whole attack. That is right for a file we point at directly and
# wrong as a blanket rule -- the GitHub Releases API publishes no
# checksum at all, so the rule would have silently suppressed every
# update from the likeliest feed anyone actually uses. Suppressing the
# news is unhelpful; implying it is verified would be a lie. So:
# announce, and say which it is.
no_hash = updates.check(
    ENABLED_CONFIG, CURRENT, fetch=lambda url, timeout: manifest(sha256="")
)
check("an unverifiable release is still announced",
      no_hash.state == updates.STATE_AVAILABLE, no_hash)
check("-- and says plainly there is nothing to check it against",
      "No checksum is published" in no_hash.reason, no_hash.reason)
check("-- and still refuses to install it",
      "will not download" in no_hash.reason, no_hash.reason)

with_hash = updates.check(ENABLED_CONFIG, CURRENT,
                          fetch=lambda url, timeout: manifest())
check("a verifiable release says a SHA-256 IS published",
      "SHA-256 is published" in with_hash.reason, with_hash.reason)
check("the two are distinguishable, which is the whole point",
      no_hash.reason != with_hash.reason)

for bad_checksum, label in (("abc123", "too short"), ("z" * 64, "not hex")):
    bad_checksum_status = updates.check(
        ENABLED_CONFIG, CURRENT,
        fetch=lambda url, timeout, value=bad_checksum: manifest(sha256=value))
    check(f"a {label} checksum reads as unverified, never as verified",
          "No checksum is published" in bad_checksum_status.reason,
          bad_checksum_status.reason)

# http:// IS still refused outright. A link that can be rewritten in
# transit is a different problem from an unverified one, and stays a
# refusal.
plain_http_download_status = updates.check(
    ENABLED_CONFIG, CURRENT, fetch=lambda url, timeout: manifest(
        download_url="http://example.invalid/x.msi"))
check("a plain-http download link is still REFUSED, not annotated",
      plain_http_download_status.state == updates.STATE_ERROR,
      plain_http_download_status)
check("-- and the message names the download, not the manifest",
      "download link" in plain_http_download_status.reason,
      plain_http_download_status.reason)

print("\n== the GitHub Releases shape, read by the same engine ==")
# Two checkers used to exist: this one reading a hand-written manifest,
# and a PowerShell script in the installer reading GitHub Releases. One
# engine now reads both, detected by CONTENT -- a URL cannot tell you
# what it will return.
GITHUB_RELEASE = {
    "tag_name": "v0.3.0",
    "html_url": "https://example.invalid/releases/v0.3.0",
    "published_at": "2026-08-01T10:00:00Z",
}
github_status = updates.check(ENABLED_CONFIG, CURRENT,
                              fetch=lambda url, timeout: GITHUB_RELEASE)
check(
    "a GitHub release is recognised",
    github_status.state == updates.STATE_AVAILABLE,
    github_status,
)
check(
    "the v prefix is stripped from tag_name",
    github_status.latest.version == "0.3.0",
    github_status.latest,
)
check(
    "html_url becomes the notes link",
    github_status.latest.notes_url.endswith("v0.3.0"),
    github_status.latest,
)
check("GitHub publishes no checksum, so it is flagged unverifiable",
      "No checksum is published" in github_status.reason, github_status.reason)
check(
    "an older GitHub tag does not downgrade you",
    updates.check(
        ENABLED_CONFIG, "9.9.9", fetch=lambda url, timeout: GITHUB_RELEASE
    ).state
    == updates.STATE_CURRENT,
)
check(
    "an unprefixed tag works too",
    updates.check(
        ENABLED_CONFIG,
        CURRENT,
        fetch=lambda url, timeout: {
            "tag_name": "0.3.0",
            "html_url": "https://x.invalid",
        },
    ).latest.version
    == "0.3.0",
)

print("\n== channels ==")
CHANNELED_MANIFEST = {"channels": {"stable": manifest(version="0.2.0"),
                                   "beta": manifest(version="0.3.0")}}
stable = updates.check({**ENABLED_CONFIG, "channel": "stable"}, CURRENT,
                       fetch=lambda url, timeout: CHANNELED_MANIFEST)
check(
    "the stable channel is read",
    stable.latest.version == "0.2.0",
    stable.latest,
)
beta = updates.check({**ENABLED_CONFIG, "channel": "beta"}, CURRENT,
                     fetch=lambda url, timeout: CHANNELED_MANIFEST)
check("the beta channel is read", beta.latest.version == "0.3.0", beta.latest)
absent_channel_status = updates.check(
    {**ENABLED_CONFIG, "channel": "nightly"}, CURRENT,
    fetch=lambda url, timeout: CHANNELED_MANIFEST)
check("an absent channel is an error, not a silent fallback to another one",
      absent_channel_status.state == updates.STATE_ERROR,
      absent_channel_status)


print("\n== a broken or hostile endpoint cannot take the agent down ==")


def _fetch_unreachable(url, timeout):
    raise urllib.error.URLError("simulated: no route to host")


unreachable_status = updates.check(ENABLED_CONFIG, CURRENT,
                                   fetch=_fetch_unreachable)
check(
    "an unreachable endpoint is an error, not an exception",
    unreachable_status.state == updates.STATE_ERROR,
    unreachable_status,
)
check("-- and never claims an update exists",
      unreachable_status.update_available is False)

for bad_body, label in (
    ("not a dict", "a JSON array"),
    (None, "null"),
    (42, "a number"),
):
    non_object_status = updates.check(
        ENABLED_CONFIG, CURRENT,
        fetch=lambda url, timeout, value=bad_body: value)
    check(
        f"{label} instead of an object is an error",
        non_object_status.state == updates.STATE_ERROR,
        non_object_status,
    )

no_version = updates.check(
    ENABLED_CONFIG,
    CURRENT,
    fetch=lambda url, timeout: {"download_url": "https://x"},
)
check(
    "a manifest with no version is an error",
    no_version.state == updates.STATE_ERROR,
    no_version,
)


def _fetch_oversized(url, timeout):
    raise ValueError(
        f"manifest larger than {updates.MAX_MANIFEST_BYTES} bytes; refusing it"
    )


oversized_status = updates.check(ENABLED_CONFIG, CURRENT,
                                 fetch=_fetch_oversized)
check(
    "an oversized body is refused rather than parsed",
    oversized_status.state == updates.STATE_ERROR,
    oversized_status,
)


# ---------------------------------------------------------------------------
print(
    "\n== the Check for Updates shortcut's script runs on an isolated Python "
    "=="
)
# The installer's shortcut runs agent/check_updates.py on the bundled
# embeddable Python, whose python314._pth runs it isolated with
# safe_path on: nothing -- not the working directory, not the script's
# own folder -- is on sys.path unless the script puts it there. Until
# 2026-09-26 the shortcut ran `python -m drobo_agent.updates` from
# inside agent/, which that runtime cannot import, so it failed in every
# installed copy and nothing here noticed. `-I` gives an ordinary
# interpreter the same isolation (-E -s -P), so this runs in every suite
# sweep without building an installer.

_agent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_check_updates_script = os.path.join(_agent_dir, "check_updates.py")
check("agent/check_updates.py is present",
      os.path.exists(_check_updates_script), _check_updates_script)


def _run_isolated(args, cwd):
    return subprocess.run([sys.executable, "-I", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


with tempfile.TemporaryDirectory() as _temp_dir:
    _config_path = os.path.join(_temp_dir, "config.json")
    with open(_config_path, "w", encoding="utf-8") as config_file:
        # A token already present, so the run does not stop to generate
        # one.
        json.dump({"agent": {"token": secrets.token_urlsafe(24)}}, config_file)

    # The old way first, so this section is known to reproduce the
    # failure rather than passing on an interpreter that never had it.
    # -P arrived in Python 3.11; on an older one -I leaves the working
    # directory on the path.
    if hasattr(sys.flags, "safe_path"):
        old_invocation = _run_isolated(
            ["-m", "drobo_agent.updates", "--config", _config_path],
            cwd=_agent_dir,
        )
        check(
            "the old `-m drobo_agent.updates` from inside agent/ fails when "
            "isolated",
            old_invocation.returncode != 0
            and "No module named" in old_invocation.stderr,
            (old_invocation.returncode, old_invocation.stderr),
        )
    else:
        print(
            "  SKIP  the old invocation's failure needs -P (Python 3.11+) to "
            "reproduce"
        )

    # From an unrelated folder: where it is started from must not
    # matter.
    run_by_path = _run_isolated(
        [_check_updates_script, "--config", _config_path], cwd=_temp_dir)
    check("check_updates.py, run by path from an unrelated folder, exits 0",
          run_by_path.returncode == 0,
          (run_by_path.returncode, run_by_path.stdout, run_by_path.stderr))
    check("-- answering that update checking is switched off",
          "switched off" in run_by_path.stdout, run_by_path.stdout)
    check("-- with no traceback", "Traceback" not in run_by_path.stderr,
          run_by_path.stderr)

    # A failure gets an exit status of its own. Python's 1 for a crash
    # is the same number as "an update is available", and the .bat reads
    # the number.
    with open(_config_path, "w", encoding="utf-8") as config_file:
        config_file.write('{ "updates": { "enabled": true, ')
    invalid_json_run = _run_isolated(
        [_check_updates_script, "--config", _config_path], cwd=_temp_dir)
    check("a config.json that is not valid JSON exits 3, not 1",
          invalid_json_run.returncode == 3,
          (invalid_json_run.returncode, invalid_json_run.stdout,
           invalid_json_run.stderr))
    check(
        "-- and says what is wrong with it",
        "not valid JSON" in invalid_json_run.stderr,
        invalid_json_run.stderr,
    )

    # Windows PowerShell 5.1's `>` and Out-File write UTF-16, the other
    # way a hand-edited config.json goes wrong. No URL is set, so
    # nothing can be contacted even if the file were read.
    with open(_config_path, "w", encoding="utf-16") as config_file:
        json.dump({"agent": {"token": secrets.token_urlsafe(24)},
                   "updates": {"enabled": True}}, config_file)
    wide = _run_isolated([_check_updates_script, "--config", _config_path],
                         cwd=_temp_dir)
    check("a UTF-16 config.json exits 3, not 0 as 'switched off'",
          wide.returncode == 3, (wide.returncode, wide.stdout, wide.stderr))
    check("-- and says it is not UTF-8", "not UTF-8" in wide.stderr,
          wide.stderr)

    _lone_script_dir = os.path.join(_temp_dir, "lone")
    os.makedirs(_lone_script_dir)
    shutil.copy(_check_updates_script, _lone_script_dir)
    missing_package_run = _run_isolated(
        [os.path.join(_lone_script_dir, "check_updates.py")], cwd=_temp_dir)
    check("with the agent's package missing, it exits 3, not 1",
          missing_package_run.returncode == 3,
          (missing_package_run.returncode, missing_package_run.stderr))
    check("-- and the traceback names the missing package",
          "No module named 'drobo_agent'" in missing_package_run.stderr,
          missing_package_run.stderr)


# ---------------------------------------------------------------------------
print(
    "\n== the release version agrees with itself, everywhere it is written =="
)
# This suite is where the check belongs: updates.check() compares the
# running version against a published one, so a tree whose own copies of
# that number disagree makes the comparison meaningless before the
# network is ever reached.
#
# Seven places across three languages, and nothing compared them until
# now. windows/README.md's "Versioning" section says "if you bump one,
# bump them all", which is advice, not a gate. It has already been wrong
# in public: release 1.0.0 shipped sdk/pyproject.toml still saying
# 0.1.0, so the metadata a reader installs disagreed with the release it
# came from.
#
# scripts/check_versions.py holds the rule, so a human can run it on its
# own before a release; this runs it on every suite sweep.

_repo_root = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
_checker_path = os.path.join(_repo_root, "scripts", "check_versions.py")
check(
    "scripts/check_versions.py is present",
    os.path.exists(_checker_path),
    _checker_path,
)

if os.path.exists(_checker_path):
    _checker_spec = importlib.util.spec_from_file_location(
        "_check_versions", _checker_path
    )
    _version_checker = importlib.util.module_from_spec(_checker_spec)
    _checker_spec.loader.exec_module(_version_checker)

    _found_versions, _unreadable = _version_checker.collect_versions()
    check(
        "every file that states the version could be read",
        _unreadable == [],
        _unreadable,
    )
    check("-- and every one of them actually states it",
          all(v is not None for v in _found_versions.values()),
          [k for k, v in _found_versions.items() if v is None])
    check(
        "all seven places are checked, not a subset",
        len(_found_versions) == 7,
        sorted(_found_versions),
    )
    check(
        "every place agrees on one number",
        _version_checker.find_problems() == [],
        _version_checker.find_problems(),
    )

    # The agent's own two copies are the pair most easily forgotten,
    # because neither is derived from the other: api.VERSION is what
    # /api/ping and the update check report, __version__ is what a
    # Python caller reads.
    from drobo_agent import api
    from drobo_agent import __version__ as _package_version
    check("the VERSION the update check compares is the package's own",
          api.VERSION == _package_version, (api.VERSION, _package_version))
    check("-- and it is the number the files on disk declare",
          api.VERSION == _found_versions.get("csproj <Version>"),
          (api.VERSION, _found_versions.get("csproj <Version>")))

    # A disagreement must FAIL, not merely be listed. Proven by pointing
    # one entry of the rule at a place that says something else on
    # purpose -- this file's own CURRENT, which is 0.1.0 -- so the real
    # tree is never edited to test the thing that guards it.
    _original_places = _version_checker.VERSION_PLACES[:]
    try:
        _version_checker.VERSION_PLACES = _original_places[:-1] + [(
            "a deliberately different place",
            os.path.join("agent", "tests", "test_updates.py"),
            '^CURRENT = "([^"]+)"',
        )]
        _seeded_problems = _version_checker.find_problems()
    finally:
        _version_checker.VERSION_PLACES = _original_places
    check("a place that disagrees is reported", len(_seeded_problems) == 1,
          _seeded_problems)
    check(
        "-- naming what it found and what it expected",
        _seeded_problems
        and CURRENT in _seeded_problems[0]
        and _found_versions["csproj <Version>"] in _seeded_problems[0],
        _seeded_problems,
    )
    check("-- and the real tree is unaffected by that probe",
          _version_checker.find_problems() == [],
          _version_checker.find_problems())

    # A four-part AssemblyVersion is checked against the three-part
    # number, not merely against the other four-part one: 1.0.0 with
    # 1.0.1.0 twice is still a release that lies about itself.
    _original_places = _version_checker.VERSION_PLACES[:]
    try:
        _version_checker.VERSION_PLACES = [
            (label, path, pattern)
            for (label, path, pattern) in _original_places
            if label != "csproj <AssemblyVersion>"
        ] + [("csproj <AssemblyVersion>",
              os.path.join("agent", "tests", "test_updates.py"),
              '^CURRENT = "([^"]+)"')]
        _four_part_problems = _version_checker.find_problems()
    finally:
        _version_checker.VERSION_PLACES = _original_places
    check(
        "a four-part number that does not extend the three-part one is caught",
        len(_four_part_problems) == 1
        and "fourth field" in _four_part_problems[0],
        _four_part_problems,
    )


print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
