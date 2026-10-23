#!/usr/bin/env python3
"""
check_versions.py --
one release, one number, in every file that states it.

Run it any time from the repo root:

    py scripts/check_versions.py

Exits 0 when every place agrees and 1 when they do not. It reads the
literals out of the files rather than importing anything, so it needs no
packages on sys.path and reports what a release would actually SHIP
rather than what an import happens to resolve to.

WHY THIS EXISTS. The number lives in seven places across three
languages, and nothing had ever compared them. It has already been wrong
in public: the 1.0.0 release shipped sdk/pyproject.toml still saying
0.1.0, so the package metadata a reader installs disagreed with the
release it came from. windows/README.md's "Versioning" section lists the
places and says "if you bump one, bump them all" -- which is advice, not
a gate. This is the gate.

It deliberately does NOT know what the number should be. Picking the
next version is the maintainer's call; all this asks is that the files
agree with each other once it is picked.

The four-part places (AssemblyVersion, FileVersion) are checked as the
three-part number plus a fourth field, which is how MSBuild and Windows
express the same release.
"""
import io
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_CSPROJ_PATH = os.path.join(
    "windows", "DroboDashboardReborn", "DroboDashboardReborn.csproj")

# (label, path relative to the repo root, regex with one capturing
# group). Order is the order they are printed in: the csproj first,
# because it is the number a release is named by and the one a human
# bumps first.
VERSION_PLACES = [
    ("csproj <Version>",
     APP_CSPROJ_PATH,
     r"<Version>([^<]+)</Version>"),
    ("csproj <AssemblyVersion>",
     APP_CSPROJ_PATH,
     r"<AssemblyVersion>([^<]+)</AssemblyVersion>"),
    ("csproj <FileVersion>",
     APP_CSPROJ_PATH,
     r"<FileVersion>([^<]+)</FileVersion>"),
    ("agent api.VERSION",
     os.path.join("agent", "drobo_agent", "api.py"),
     r"^VERSION\s*=\s*[\"']([^\"']+)[\"']"),
    ("agent __version__",
     os.path.join("agent", "drobo_agent", "__init__.py"),
     r"^__version__\s*=\s*[\"']([^\"']+)[\"']"),
    ("sdk __version__",
     os.path.join("sdk", "drobo_nasd", "__init__.py"),
     r"^__version__\s*=\s*[\"']([^\"']+)[\"']"),
    ("sdk pyproject version",
     os.path.join("sdk", "pyproject.toml"),
     r"^version\s*=\s*[\"']([^\"']+)[\"']"),
]

# The two that carry a fourth field. Checked against the three-part
# number rather than against each other, so "1.0.0 vs 1.0.1.0" is caught
# too.
FOUR_PART_LABELS = {"csproj <AssemblyVersion>", "csproj <FileVersion>"}

REFERENCE_LABEL = "csproj <Version>"


def read_repo_file(path):
    with io.open(os.path.join(REPO_ROOT, path),
                 encoding="utf-8-sig") as text_file:
        return text_file.read()


def collect_versions():
    """{label: version-or-None},
    plus a list of files that could not be read.
    """
    found, unreadable = {}, []
    text_by_path = {}
    for label, path, pattern in VERSION_PLACES:
        if path not in text_by_path:
            try:
                text_by_path[path] = read_repo_file(path)
            except OSError as exc:
                text_by_path[path] = None
                unreadable.append(f"{path}: {exc.strerror or exc}")
        text = text_by_path[path]
        if text is None:
            found[label] = None
            continue
        match = re.search(pattern, text, re.MULTILINE)
        found[label] = match.group(1).strip() if match else None
    return found, unreadable


def find_problems():
    """Every disagreement, as plain sentences.
    Empty means the tree is consistent.
    """
    found, unreadable = collect_versions()
    problems = list(unreadable)

    for label, path, _ in VERSION_PLACES:
        if found.get(label) is None and not any(path in m for m in unreadable):
            problems.append(f"{label}: no version found in {path}")

    reference_version = found.get(REFERENCE_LABEL)
    if reference_version is None:
        problems.append(
            f"{REFERENCE_LABEL} is the number everything else is compared "
            f"against, and it could not be read -- nothing could be checked")
        return problems

    for label, path, _ in VERSION_PLACES:
        stated_version = found.get(label)
        if stated_version is None or label == REFERENCE_LABEL:
            continue
        if label in FOUR_PART_LABELS:
            version_fields = stated_version.split(".")
            if (len(version_fields) != 4
                    or ".".join(version_fields[:3]) != reference_version):
                problems.append(
                    f"{label} is {stated_version} in {path}; expected "
                    f"{reference_version} plus a fourth field (for example "
                    f"{reference_version}.0)")
        elif stated_version != reference_version:
            problems.append(
                f"{label} is {stated_version} in {path}; expected "
                f"{reference_version}")
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    quiet = "--quiet" in argv

    found, _ = collect_versions()
    problems = find_problems()

    if not quiet:
        label_width = max(len(label) for label, _, _ in VERSION_PLACES)
        for label, _, _ in VERSION_PLACES:
            stated_version = found.get(label)
            shown_version = (stated_version if stated_version is not None
                             else "(not found)")
            print(f"  {label.ljust(label_width)}  {shown_version}")
        print("")

    if problems:
        print("FAIL: the release version does not agree with itself.")
        for problem in problems:
            print(f"  - {problem}")
        print("")
        print("Fix every place listed under \"Versioning\" "
              "in windows/README.md,")
        print("then re-run. Do not guess which one is right.")
        return 1

    if not quiet:
        print(f"PASS: every place says {found[REFERENCE_LABEL]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
