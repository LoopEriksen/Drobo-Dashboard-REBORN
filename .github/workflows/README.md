# Automatic checks

GitHub runs these checks every time changes are pushed to it. Nothing
here needs opening or editing day to day. A red cross next to a commit on
GitHub means one of them found a problem; click it to see which.

| File | What it checks |
|---|---|
| [`guard.yml`](guard.yml) | No password or key files are tracked, no secret keys appear in any file (apart from `scripts/` and `guard.yml` itself, which have to spell out the patterns they look for), and no personal data does either (it runs [`scripts/scan_for_pii.py`](../../scripts/scan_for_pii.py), without the private list of names) |
| [`lint.yml`](lint.yml) | Every Python file still compiles |
| [`style.yml`](style.yml) | Every Python file still follows PEP 8: lines no longer than 79 columns (72 for comments and docstrings), names in PEP 8's style, and nothing else flake8 flags, such as an unused import. It runs flake8 with the settings in [`.flake8`](../../.flake8), and every version is pinned, so a new release of flake8 cannot turn it red on its own |
| [`tests.yml`](tests.yml) | The tests actually run. All thirty-one Python suites, each standalone from its own folder, on Windows with Python 3.12; and a second job that builds the Windows app and runs its own test program. A suite that exits without checking anything, that prints a failed check and returns success anyway, or that moves out of the three folders the job reads from `git ls-files`, fails the run instead of quietly dropping out of the count |
| [`installer.yml`](installer.yml) | The installer works. On a Windows runner, which runs as an administrator, it builds the `.msi` the way a release is built, builds a throwaway one numbered one higher, and runs [`windows/test-installer.ps1`](../../windows/test-installer.ps1) with both: install, launch against the simulated Drobo, uninstall, the no-desktop-shortcut install, and an upgrade that must keep the user's settings. It uploads the `.msi`, its `SHA256SUMS.txt` and the test's log. It runs when the installer, its two scripts, the Windows app or this workflow change, and can be started by hand from the Actions tab. On a `v*` tag that matches the app's version it also publishes that tested `.msi` as the GitHub Release, with the notes in [`../release-notes.md`](../release-notes.md). Nothing is signed |

This README sits here, not directly in `.github/`, on purpose: GitHub would
show a README placed in `.github/` as the repository's front page, instead of
the main one.
