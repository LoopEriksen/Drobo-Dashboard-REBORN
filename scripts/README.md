# Repository guards

Small checks that stop a known mistake from getting out. Nothing here needs
opening day to day.

## Privacy

These files stop personal information (real names, Windows user-folder paths,
real email addresses) and the Drobo's own identifiers (the serial number and
MAC address printed on the unit) from getting into a commit.

| File | What it does |
|---|---|
| [`scan_for_pii.py`](scan_for_pii.py) | The scanner. GitHub runs it on every push. To run it yourself, from the repository's top folder: `py scripts/scan_for_pii.py --tracked`. Without the private list below it cannot look for names and says so; add `--generic-only` to check everything else, as GitHub does |
| [`hooks/pre-commit`](hooks/pre-commit) | Runs by itself before every commit. Blocks a commit that adds personal data, a database or environment file, or that carries a real email address as its author |
| [`install-hooks.sh`](install-hooks.sh) | Run it once in each new copy of the repository, from Git Bash: `sh scripts/install-hooks.sh`. Until you do, the hook above never runs |
| [`pii_allow.txt`](pii_allow.txt) | Exceptions someone has looked at and accepted. None at the moment |
| `pii_patterns.local` | A private list of names to look for. It exists only on the maintainer's PC and git ignores it: never commit, copy, move or rename it |

**Do not move or rename this folder or anything in it.** Each copy of the
repository is set to run its hook from `scripts/hooks`, and a hook that has
moved simply stops running, with no warning.

This repository's own check for secret keys and password files, which GitHub
runs on every push, is described in
[`../.github/workflows/README.md`](../.github/workflows/README.md).

## Release

| File | What it does |
|---|---|
| [`check_versions.py`](check_versions.py) | Compares the release version number in all seven places that state it -- the csproj's three properties, the agent's two, and the SDK's two -- and fails if any disagrees. Run it before a release, from the repository's top folder: `py scripts/check_versions.py`. It also runs inside `agent/tests/test_updates.py`, so an ordinary test sweep catches a half-finished bump |

It does not know what the number should be; choosing it is the maintainer's call.
All it asks is that the files agree with each other once it is chosen. The
number itself, and what has to move with it, is in
[`../windows/README.md`](../windows/README.md) under "Versioning".
