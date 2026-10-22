#!/usr/bin/env python3
"""
check_versions.py -- one release, one number, in every file that states it.

Run it any time from the repo root:

    py scripts/check_versions.py

Exits 0 when every place agrees and 1 when they do not. It reads the literals
out of the files rather than importing anything, so it needs no packages on
sys.path and reports what a release would actually SHIP rather than what an
import happens to resolve to.

WHY THIS EXISTS. The number lives in seven places across three languages, and
nothing had ever compared them. It has already been wrong in public: the 1.0.0
release shipped sdk/pyproject.toml still saying 0.1.0, so the package metadata
a reader installs disagreed with the release it came from. windows/README.md's
"Versioning" section lists the places and says "if you bump one, bump them
all" -- which is advice, not a gate. This is the gate.

It deliberately does NOT know what the number should be. Picking the next
version is the maintainer's call; all this asks is that the files agree with each
other once it is picked.

The four-part places (AssemblyVersion, FileVersion) are checked as the
three-part number plus a fourth field, which is how MSBuild and Windows
express the same release.
"""
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSPROJ = os.path.join("windows", "DroboDashboardReborn", "DroboDashboardReborn.csproj")

# (label, path relative to the repo root, regex with one capturing group).
# Order is the order they are printed in: the csproj first, because it is the
# number a release is named by and the one a human bumps first.
VERSION_PLACES = [
    ("csproj <Version>",        CSPROJ, r"<Version>([^<]+)</Version>"),
    ("csproj <AssemblyVersion>", CSPROJ, r"<AssemblyVersion>([^<]+)</AssemblyVersion>"),
    ("csproj <FileVersion>",    CSPROJ, r"<FileVersion>([^<]+)</FileVersion>"),
    ("agent api.VERSION",       os.path.join("agent", "drobo_agent", "api.py"),
     r"^VERSION\s*=\s*[\"']([^\"']+)[\"']"),
    ("agent __version__",       os.path.join("agent", "drobo_agent", "__init__.py"),
     r"^__version__\s*=\s*[\"']([^\"']+)[\"']"),
    ("sdk __version__",         os.path.join("sdk", "drobo_nasd", "__init__.py"),
     r"^__version__\s*=\s*[\"']([^\"']+)[\"']"),
    ("sdk pyproject version",   os.path.join("sdk", "pyproject.toml"),
     r"^version\s*=\s*[\"']([^\"']+)[\"']"),
]

# The two that carry a fourth field. Checked against the three-part number
# rather than against each other, so "1.0.0 vs 1.0.1.0" is caught too.
FOUR_PART = {"csproj <AssemblyVersion>", "csproj <FileVersion>"}

REFERENCE = "csproj <Version>"


def read(path):
    with io.open(os.path.join(REPO, path), encoding="utf-8-sig") as fh:
        return fh.read()


def collect_versions():
    """{label: version-or-None}, plus a list of files that could not be read."""
    found, missing = {}, []
    cache = {}
    for label, path, pattern in VERSION_PLACES:
        if path not in cache:
            try:
                cache[path] = read(path)
            except OSError as exc:
                cache[path] = None
                missing.append(f"{path}: {exc.strerror or exc}")
        text = cache[path]
        if text is None:
            found[label] = None
            continue
        match = re.search(pattern, text, re.MULTILINE)
        found[label] = match.group(1).strip() if match else None
    return found, missing


def find_problems():
    """Every disagreement, as plain sentences. Empty means the tree is consistent."""
    found, missing = collect_versions()
    problems = list(missing)

    for label, path, _ in VERSION_PLACES:
        if found.get(label) is None and not any(path in m for m in missing):
            problems.append(f"{label}: no version found in {path}")

    reference = found.get(REFERENCE)
    if reference is None:
        problems.append(
            f"{REFERENCE} is the number everything else is compared against, "
            f"and it could not be read -- nothing could be checked")
        return problems

    for label, path, _ in VERSION_PLACES:
        got = found.get(label)
        if got is None or label == REFERENCE:
            continue
        if label in FOUR_PART:
            parts = got.split(".")
            if len(parts) != 4 or ".".join(parts[:3]) != reference:
                problems.append(
                    f"{label} is {got} in {path}; expected {reference} "
                    f"plus a fourth field (for example {reference}.0)")
        elif got != reference:
            problems.append(
                f"{label} is {got} in {path}; expected {reference}")
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    quiet = "--quiet" in argv

    found, _ = collect_versions()
    bad = find_problems()

    if not quiet:
        width = max(len(label) for label, _, _ in VERSION_PLACES)
        for label, _, _ in VERSION_PLACES:
            got = found.get(label)
            print(f"  {label.ljust(width)}  {got if got is not None else '(not found)'}")
        print("")

    if bad:
        print("FAIL: the release version does not agree with itself.")
        for line in bad:
            print(f"  - {line}")
        print("")
        print("Fix every place listed under \"Versioning\" in windows/README.md,")
        print("then re-run. Do not guess which one is right.")
        return 1

    if not quiet:
        print(f"PASS: every place says {found[REFERENCE]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
