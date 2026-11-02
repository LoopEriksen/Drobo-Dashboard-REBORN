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
import argparse
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
    """The text of one repo file, decoded as UTF-8 without its BOM.

    utf-8-sig rather than utf-8 because Windows tools save files with a
    byte order mark (the csproj has one today): left in, it would sit
    in front of the first line, where a ^-anchored pattern such as the
    Python and TOML ones could then no longer match.
    """
    with io.open(os.path.join(REPO_ROOT, path),
                 encoding="utf-8-sig") as text_file:
        return text_file.read()


def _read_once(path, text_by_path, unreadable_messages):
    """Read path into text_by_path unless an earlier place already did.

    Three places live in the csproj, so caching keeps every place in
    one file reading the same text. A file that cannot be read is
    stored as None and described in unreadable_messages instead of
    raising, so one missing file is reported alongside every other
    problem rather than hiding them.
    """
    if path in text_by_path:
        return
    try:
        text_by_path[path] = read_repo_file(path)
    except OSError as exc:
        text_by_path[path] = None
        unreadable_messages.append(f"{path}: {exc.strerror or exc}")


def collect_versions():
    """{label: version-or-None},
    plus a list of files that could not be read.

    None stands for "not found" whether the file was unreadable or the
    pattern did not match; the second list says which files were
    unreadable, so find_problems can avoid reporting them twice.
    VERSION_PLACES is read here at call time, not captured earlier, so
    a caller (the tests) can swap it for a seeded list.
    """
    version_by_label, unreadable_messages = {}, []
    text_by_path = {}
    for label, path, pattern in VERSION_PLACES:
        _read_once(path, text_by_path, unreadable_messages)
        text = text_by_path[path]
        if text is None:
            version_by_label[label] = None
            continue
        match = re.search(pattern, text, re.MULTILINE)
        version_by_label[label] = match.group(1).strip() if match else None
    return version_by_label, unreadable_messages


def _four_part_problem(label, path, stated_version, reference_version):
    """The problem sentence for a four-part place, or None if it agrees.

    A four-part place agrees when it has exactly four fields and its
    first three are the reference number; the fourth field is free,
    because MSBuild and Windows use it for builds of the same release.
    """
    version_fields = stated_version.split(".")
    if (len(version_fields) == 4
            and ".".join(version_fields[:3]) == reference_version):
        return None
    return (f"{label} is {stated_version} in {path}; expected "
            f"{reference_version} plus a fourth field (for example "
            f"{reference_version}.0)")


def find_problems():
    """Every disagreement, as plain sentences.
    Empty means the tree is consistent.

    A missing reference number stops the check early: with nothing to
    compare against, every other place would either be skipped or
    reported as wrong, and neither says anything true.
    """
    version_by_label, unreadable_messages = collect_versions()
    problems = list(unreadable_messages)

    for label, path, _ in VERSION_PLACES:
        if version_by_label.get(label) is not None:
            continue
        if any(path in unreadable_message
               for unreadable_message in unreadable_messages):
            continue
        problems.append(f"{label}: no version found in {path}")

    reference_version = version_by_label.get(REFERENCE_LABEL)
    if reference_version is None:
        problems.append(
            f"{REFERENCE_LABEL} is the number everything else is compared "
            f"against, and it could not be read -- nothing could be checked")
        return problems

    for label, path, _ in VERSION_PLACES:
        stated_version = version_by_label.get(label)
        if stated_version is None or label == REFERENCE_LABEL:
            continue
        if label in FOUR_PART_LABELS:
            problem = _four_part_problem(
                label, path, stated_version, reference_version)
            if problem is not None:
                problems.append(problem)
        elif stated_version != reference_version:
            problems.append(
                f"{label} is {stated_version} in {path}; expected "
                f"{reference_version}")
    return problems


def _print_version_table(version_by_label):
    """Every place and the number it states, one aligned line each."""
    label_width = max(len(label) for label, _, _ in VERSION_PLACES)
    for label, _, _ in VERSION_PLACES:
        stated_version = version_by_label.get(label)
        shown_version = (stated_version if stated_version is not None
                         else "(not found)")
        print(f"  {label.ljust(label_width)}  {shown_version}")
    print("")


def _parse_arguments(argv):
    """The command line, parsed; the module docstring is the --help.

    It used to look only for "--quiet" among the arguments, so --help
    ran the check and printed the table, and a misspelt flag (--quite)
    was ignored without a word. argparse prints help for --help and
    refuses anything else it does not know (exit 2).
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--quiet", action="store_true",
        help="print nothing when every place agrees; a disagreement is "
             "always printed")
    return parser.parse_args(argv)


def main(argv=None):
    """Print the table and the verdict; 0 when every place agrees.

    Returns 1 on any disagreement. --quiet drops the table and the PASS
    line so a hook or CI step shows output only when something is
    wrong; the FAIL report is always printed, because a silent failure
    would leave nobody knowing what to fix.
    """
    quiet = _parse_arguments(argv).quiet

    version_by_label, _ = collect_versions()
    problems = find_problems()

    if not quiet:
        _print_version_table(version_by_label)

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
        print(f"PASS: every place says {version_by_label[REFERENCE_LABEL]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
