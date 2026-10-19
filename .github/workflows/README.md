# Automatic checks

GitHub runs these checks every time changes are pushed to it. Nothing
here needs opening or editing day to day. A red cross next to a commit on
GitHub means one of them found a problem; click it to see which.

| File | What it checks |
|---|---|
| [`guard.yml`](guard.yml) | No password or key files are tracked, no secret keys appear in any file (apart from `scripts/` and `guard.yml` itself, which have to spell out the patterns they look for), and no personal data does either (it runs [`scripts/scan_for_pii.py`](../../scripts/scan_for_pii.py), without the private list of names) |
| [`lint.yml`](lint.yml) | Every Python file still compiles |
| [`tests.yml`](tests.yml) | The tests actually run. All thirty-one Python suites, each standalone from its own folder, on Windows with Python 3.12; and a second job that builds the Windows app and runs its own test program. A suite that exits without checking anything, that prints a failed check and returns success anyway, or that moves out of the three folders the job reads from `git ls-files`, fails the run instead of quietly dropping out of the count |

This README sits here, not directly in `.github/`, on purpose: GitHub would
show a README placed in `.github/` as the repository's front page, instead of
the main one.
