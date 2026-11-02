#!/usr/bin/env python3
"""
scan_for_pii.py — scan the Drobo Dashboard REBORN repository for
personal or identifying information before you push.

Run it any time from the repo root:

    py scripts/scan_for_pii.py

Exits 0 when clean and 1 when it finds something -- or when it could not
check a category, because a scan that quietly skipped names is not a
clean scan. It covers everything the pre-commit hook blocks, plus two
shapes the hook does not know about -- the device serial and its MAC --
across every file git tracks, so problems surface before anything is
even staged.

  --all            walk every file in the tree, build output included
  --tracked        the files git tracks: the default, spelled out for CI
  --generic-only   check only the built-in categories, and say that
                   names were not checked (what CI runs; see
                   load_local_patterns)

It is deliberately GENERIC — it names no real person or username, so
this script carries no identifying data of its own. Names live in
scripts/pii_patterns.local (gitignored, one regex per line). Reviewed
exceptions live in scripts/pii_allow.txt (committed).

It skips .git/, .claude/, virtualenvs, __pycache__, and binary files.
"""
import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Generic patterns — no hardcoded personal identifiers.
# A real user-profile path, but not the placeholders documentation is
# *supposed* to use. Without this exclusion every "C:\Users\you\..."
# example in the docs was reported, and 4 of the scanner's 16 tracked
# hits on 2026-09-08 were exactly that. A scanner whose every hit is a
# false positive does not get read, which is how the real ones get
# through.
# Note the two alternatives are shaped differently on purpose. An
# angle-bracket placeholder needs NO trailing \b: "<you>" is followed by
# a backslash, and two non-word characters in a row give no word
# boundary, so an earlier version with \b on both forms silently failed
# to exclude every "C:\Users\<you>\..." in the docs. Bare words still
# need the boundary, or "name" would also swallow a real user called
# "nameless".
_PLACEHOLDER_EXCLUSION = (
    r"(?!(?:<[^>\\/]{1,24}>"
    r"|(?:you|your-?name|username|name|user|Public|All\sUsers)\b))")

# The account name after Users\ is part of the match, not just the
# prefix. Matching only "C:\Users\" said a path existed but not whose,
# so every hit read alike and scripts/pii_allow.txt could not accept one
# reviewed account without accepting every account. The name is
# required: a bare "C:\Users\" -- a redaction rule, a truncated path --
# identifies nobody. The class is "anything but a separator, whitespace
# or a quote" rather than a list of letters, because account names are
# not ASCII-only, and a letters-first class does not match a name like
# "Ölaf" at all. redact() masks the name before anything is printed.
_ACCOUNT_NAME_PATTERN = r"""[^\\/\s"'`]+"""

BUILTIN_PATTERNS = [
    ("absolute Windows user path",
     re.compile(r"C:[\\/]+Users[\\/]+" + _PLACEHOLDER_EXCLUSION
                + _ACCOUNT_NAME_PATTERN, re.I)),
    ("email address",
     re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # The two shapes that identify the unit itself, and that must never
    # reach a commit: the device serial number and its MAC address.
    # Until now neither was checked here at all -- they were checked
    # only by windows/build-installer.ps1 on the STAGED PAYLOAD, which
    # is the last gate before a shipped artifact and no help at all to a
    # commit. So the scanner that exists to stop identifying data
    # reaching a commit was blind to the two identifiers that matter
    # most about the hardware.
    #
    # Both are SHAPES, deliberately: this file is tracked, so it must be
    # able to describe what it hunts without carrying one real value.
    # They are taken from the staged-payload check in
    # windows/build-installer.ps1 (the $identifierPatterns list in step
    # 5b), but they are not character-for-character the same, and the
    # differences are on purpose: the serial is that check's pattern
    # with word boundaries added, while the MAC is narrowed to the OUI
    # plus three more octets (that check fires on the bare OUI) and
    # widened to accept an address written without separators. So the
    # two gates agree on what a real identifier looks like and differ,
    # knowingly, at the edges -- see the next paragraph for why each
    # edge is where it is.
    #
    # The serial is the format stamped on every unit. The MAC is
    # anchored to Drobo Inc's registered OUI (a public vendor
    # allocation, not anybody's address), which is exactly what
    # separates a real unit's MAC from the invented ones the test
    # fixtures use -- 02:00:..., aa:bb:..., e0:ab:... -- so those stay
    # clean. Separators are optional and may be ':' or '-', because an
    # address arrives written however the tool that printed it writes
    # it: `arp -a` on Windows uses '-', `ip neigh` uses ':', and some
    # device pages print it bare. Requiring all three unit octets means
    # the bare OUI on its own -- vendor trivia, in a comment or a lookup
    # table -- is not reported as if it identified a unit.
    ("Drobo device serial",
     re.compile(r"\bDRB\d{6}[A-Z]\d{5}\b", re.I)),
    ("Drobo MAC address",
     re.compile(r"\b00[:-]?1A[:-]?62(?:[:-]?[0-9A-F]{2}){3}\b", re.I)),
]

# Emails that are fine: demo accounts, and the names the RFCs reserve
# for documentation and testing -- example.com/.net/.org plus the
# .example, .test, .invalid and .localhost names (RFC 2606, RFC 6761).
# The agent's notification tests use .invalid addresses precisely
# because they can never resolve, and every one of them was being
# reported as a leak. The dot before the reserved name is required, so a
# real domain that merely ends in the same letters is not waved through
# with them.
ALLOWED_EMAIL_DOMAIN = re.compile(
    r"@(demo\.com|example\.(com|org|net)"
    r"|[A-Za-z0-9.-]+\.(example|test|invalid|localhost))$", re.I)

# .claude/ holds local machine config (gitignored, never published).
SKIP_DIRS = {".git", ".claude", "__pycache__", ".venv", "venv", "env", "ENV",
             "node_modules", ".pytest_cache", "build", "dist"}
SKIP_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".bmp", ".ico", ".pyc", ".zip", ".mp4", ".mov",
    ".docx", ".doc", ".pdf", ".xlsx"}


def _pattern_lines(path):
    """(line_number, text) for every pattern in a one-regex-per-line
    file, skipping blank lines and # comments.

    Shared by the private list and the allowlist so the two files are
    read by exactly the same rules; the line number is kept so a bad
    entry can be reported where it is.
    """
    with open(path, encoding="utf-8", errors="ignore") as pattern_file:
        for line_number, line in enumerate(pattern_file, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            yield line_number, line


def load_local_patterns():
    """User-private regexes
    from scripts/pii_patterns.local (gitignored).

    This is where names live: the maintainer's real name, their Windows
    username, the real share names. They cannot be listed in a tracked
    file without the tracked file becoming the leak, so the list is
    per-machine and ignored by git.

    Returns (patterns, notes). The notes matter as much as the patterns.
    On 2026-09-08 a privacy audit found this file absent, which meant
    NAME DETECTION WAS ENTIRELY OFF -- and the scanner still printed
    "clean", because a missing list returned an empty list and nothing
    said so. Two real share names had been sitting in tracked test
    fixtures for a fortnight. A guard that cannot tell you it is not
    guarding is worse than no guard, because it is trusted.
    """
    private_patterns, notes = [], []
    path = os.path.join(REPO_ROOT, "scripts", "pii_patterns.local")
    if not os.path.exists(path):
        notes.append(
            "NAME DETECTION IS OFF: scripts/pii_patterns.local is missing, so "
            "nothing here can recognise a real name, username or share name. "
            "Create it (one regex per line; it is gitignored) or this scan "
            "cannot see the category it exists for.")
        return private_patterns, notes

    for line_number, line in _pattern_lines(path):
        try:
            private_patterns.append(("private pattern",
                                     re.compile(line, re.I)))
        except re.error as exc:
            # Silently dropping a bad pattern used to leave a hole
            # shaped exactly like the thing it was meant to catch.
            notes.append(f"pii_patterns.local:{line_number} is not a valid "
                         f"regex and was skipped ({exc}).")
    if not private_patterns:
        notes.append(
            "pii_patterns.local exists but defines no usable patterns.")
    return private_patterns, notes


def load_allowlist():
    """Reviewed exceptions from scripts/pii_allow.txt (committed).

    The mirror image of pii_patterns.local: that file is private and
    ADDS patterns, this one is published and SUBTRACTS hits somebody has
    already looked at and accepted. Without it the scan can never reach
    exit 0 on a tree that contains one intentional address, so it cannot
    serve as a CI gate -- and a gate that is always red is one nobody
    reads. One regex per line, matched against the flagged text; keep
    them tight.

    Returns (patterns, notes). A malformed entry is reported rather than
    dropped quietly, but unlike a bad private pattern it cannot hide a
    leak: losing an exception makes the scan stricter, not blinder.
    """
    allow_patterns, notes = [], []
    path = os.path.join(REPO_ROOT, "scripts", "pii_allow.txt")
    if not os.path.exists(path):
        return allow_patterns, notes
    for line_number, line in _pattern_lines(path):
        try:
            allow_patterns.append(re.compile(line, re.I))
        except re.error as exc:
            notes.append(f"pii_allow.txt:{line_number} is not a valid regex "
                         f"and was skipped ({exc}).")
    return allow_patterns, notes


# Files that legitimately contain the shapes being hunted, because
# hunting is their job. Without this the scanner reports its own
# siblings for ever.
SKIP_FILES = {"scripts/scan_for_pii.py", "scripts/hooks/pre-commit",
              "scripts/pii_patterns.local", "scripts/pii_allow.txt",
              # the payload identifier check
              "windows/build-installer.ps1"}


def tracked_files():
    """The files git TRACKS, repo-relative;
    None if this is not a checkout.

    Tracked is the default because the point of this scanner is to stop
    personal data being committed, and an untracked file cannot be
    committed. Walking the whole tree instead meant build output under
    obj/ and bin/ and the vendor binary supplied 1,884 of 1,900 hits on
    2026-09-08 -- noise deep enough to bury the 16 that were about real
    files, which is the same as having no scanner.
    """
    try:
        git_result = subprocess.run(["git", "-C", REPO_ROOT, "ls-files", "-z"],
                                    capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [relative_path for relative_path in
            git_result.stdout.decode("utf-8", "replace").split("\0")
            if relative_path]


def walked_files():
    """Every file in the working tree, repo-relative.

    The fallback when this is not a git checkout, and what --all asks
    for. It includes build output and anything untracked, which is why
    it is not the default: see tracked_files for how noisy that was.
    SKIP_DIRS is pruned during the walk so os.walk never descends into
    .git/ or a virtualenv at all.
    """
    relative_paths = []
    for directory_path, directory_names, file_names in os.walk(REPO_ROOT):
        directory_names[:] = [directory_name
                              for directory_name in directory_names
                              if directory_name not in SKIP_DIRS]
        for file_name in file_names:
            path = os.path.join(directory_path, file_name)
            relative_paths.append(
                os.path.relpath(path, REPO_ROOT).replace("\\", "/"))
    return relative_paths


# A line whose context makes a PRIVATE-NAME match legitimate: credit
# lines for the drobo-utils author, who is named in three places.
# Without this the scanner reports proper attribution as a leak, every
# run, for ever. Credit is not a privacy problem; being unable to tell
# the two apart is. It exempts the private-name patterns only -- a user
# path, an email address, a serial or a MAC on the same line is still
# reported. scripts/hooks/pre-commit applies the same regex under the
# same name, CREDIT_CONTEXT; keep the two identical, or the hook and the
# scanner disagree about the same line.
CREDIT_CONTEXT = re.compile(r"drobo-utils|petersilva|Silva", re.I)

# Splits a user-path match into the "C:\Users\" prefix, which is shown,
# and the account name after it, which redact() masks.
_USER_PATH_SPLIT_PATTERN = re.compile(r"(C:[\\/]+Users[\\/]+)(.*)",
                                      re.I | re.S)


def _redact_mac(snippet):
    """The MAC with its last three octets masked.

    The first three octets are the vendor OUI -- printing them says "a
    Drobo", not "which Drobo". The three that identify the unit are
    masked, separators kept so the line still reads as an address.
    """
    redacted_characters, digit_count = [], 0
    for character in snippet:
        if character in ":-":
            redacted_characters.append(character)
            continue
        digit_count += 1
        redacted_characters.append(character if digit_count <= 6 else "*")
    return "".join(redacted_characters)


def _redact_user_path(snippet):
    """The user path, its account name masked after the first letter.

    A snippet that does not split into prefix and name is returned as
    it is; the pattern requires a name, so that does not happen today.
    """
    path_match = _USER_PATH_SPLIT_PATTERN.match(snippet)
    if not path_match or not path_match.group(2):
        return snippet
    account_name = path_match.group(2)
    return (path_match.group(1) + account_name[0]
            + "*" * max(len(account_name) - 1, 1))


def _redact_email(snippet):
    """The address with its local part and its domain masked.

    The first characters of the local part, the domain's first letter
    and its last label are kept: enough to find the line, which the
    file and line number already pin down. The domain used to be
    printed in full, and a personal domain is often a surname, so the
    one part of an address that names its owner reached the console
    and the CI log. Reserved and demo domains never get this far.
    """
    local_part, _, domain = snippet.partition("@")
    kept_prefix = local_part[:2] if len(local_part) > 2 else local_part[:1]
    local_mask = "*" * max(len(local_part) - len(kept_prefix), 1)
    domain_name, dot, top_label = domain.rpartition(".")
    if not dot:
        domain_name, top_label = domain, ""
    domain_mask = "*" * max(len(domain_name) - 1, 1)
    return (f"{kept_prefix}{local_mask}@{domain_name[:1]}{domain_mask}"
            f"{dot}{top_label}")


def redact(label, snippet):
    """Never print the identifier itself.

    A tool that finds a name and then writes it to the console has moved
    the name, not removed it -- into scrollback, into CI output, into
    whatever the user pastes when they ask for help. Enough is shown to
    find the line; the file and line number are the actionable part
    anyway. The user-path match now carries the account name, so that
    name is masked too, and so is an email's domain.
    """
    if label == "private pattern":
        return snippet[0] + "*" * (len(snippet) - 1) if snippet else "?"
    if label == "email address" and "@" in snippet:
        return _redact_email(snippet)
    if label == "Drobo device serial":
        # Every unit's serial starts the same way;
        # what follows is the unit.
        return snippet[:3] + "*" * max(len(snippet) - 3, 1)
    if label == "Drobo MAC address":
        return _redact_mac(snippet)
    if label == "absolute Windows user path":
        return _redact_user_path(snippet)
    return snippet


def _is_skipped(relative_path):
    """True for a file this scan never reads.

    git ls-files knows nothing of SKIP_DIRS, so they are applied here,
    to both file lists; otherwise the tracked scan and --all disagree
    about scope.
    """
    if relative_path in SKIP_FILES:
        return True
    if os.path.splitext(relative_path)[1].lower() in SKIP_EXTENSIONS:
        return True
    return any(part in SKIP_DIRS for part in relative_path.split("/"))


def _is_accepted_hit(label, snippet, line, allow_patterns):
    """True when a match is a known-good one rather than a hit.

    Three rules: a reserved or demo email domain, a private-name match
    on a credit line, and a reviewed exception from pii_allow.txt.
    """
    if label == "email address" and ALLOWED_EMAIL_DOMAIN.search(snippet):
        return True
    if label == "private pattern" and CREDIT_CONTEXT.search(line):
        return True
    return any(allow_pattern.search(snippet)
               for allow_pattern in allow_patterns)


def _matches_on_line(line, patterns):
    """(label, snippet) for every match of every pattern on the line.

    Every match, not only the first. Stopping at the first meant an
    accepted match early in a line -- a reserved test address, a
    reviewed exception -- hid a real one later on the same line.
    """
    for label, pattern in patterns:
        for match in pattern.finditer(line):
            yield label, match.group(0).strip()


def _line_hits(relative_path, line_number, line, patterns, allow_patterns):
    """The hits on one line, as (relative_path, line_number, label,
    snippet) tuples, in pattern order.
    """
    return [(relative_path, line_number, label, snippet)
            for label, snippet in _matches_on_line(line, patterns)
            if not _is_accepted_hit(label, snippet, line, allow_patterns)]


def _scan_lines(relative_path, text_file, patterns, allow_patterns, hits):
    """Append every hit in an open file to hits, line by line.

    Appending as it goes (rather than returning a list) means the hits
    from lines read before a read error are kept, as they always were.
    """
    for line_number, line in enumerate(text_file, 1):
        hits.extend(_line_hits(relative_path, line_number, line, patterns,
                               allow_patterns))


def _scan_one_file(relative_path, patterns, allow_patterns, hits):
    """Append every hit in one repo file to hits; skip it if unreadable.

    Read as UTF-8 with undecodable bytes ignored, so one stray byte
    neither stops the whole scan nor skips the file; a file that cannot
    be opened at all is left out, and the rest are still scanned.
    """
    try:
        with open(os.path.join(REPO_ROOT, relative_path),
                  encoding="utf-8", errors="ignore") as text_file:
            _scan_lines(relative_path, text_file, patterns, allow_patterns,
                        hits)
    except (OSError, UnicodeError):
        return


def scan_files(patterns, allow_patterns, relative_paths):
    """Every hit in relative_paths, as (relative_path, line_number,
    label, snippet) tuples, in file then line then pattern order.

    The patterns are tried on every line, and every match is kept, so
    one accepted match cannot hide a real one beside it.
    """
    hits = []
    for relative_path in relative_paths:
        if _is_skipped(relative_path):
            continue
        _scan_one_file(relative_path, patterns, allow_patterns, hits)
    return hits


def _parse_arguments():
    """The command line, parsed.

    --path lets this guard another checkout instead of only the one it
    happens to live in. Before it existed, an unknown flag was silently
    ignored and you got a clean-looking scan of the WRONG directory;
    argparse now rejects one.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--path",
        help="repo to scan (default: the one this script lives in)")
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--all", action="store_true",
        help="scan every file, not only the ones git tracks "
             "(includes build output; expect noise)")
    scope_group.add_argument(
        "--tracked", action="store_true",
        help="scan only the files git tracks (the default); fail "
             "rather than walk the tree if this is not a checkout")
    parser.add_argument("--generic-only", action="store_true",
                        help="skip the private name list and say so; for CI, "
                             "where that list never exists")
    return parser.parse_args()


def _choose_files(args):
    """(relative_paths, scope_description) for the scope asked for, or
    None when --tracked was asked for and this is not a checkout.

    Without --tracked, a tree that is not a checkout is walked instead,
    and the scope line says so.
    """
    if args.all:
        return walked_files(), "every file in the tree"
    relative_paths = tracked_files()
    if relative_paths is not None:
        return relative_paths, (
            f"the {len(relative_paths)} files git tracks "
            f"(use --all for everything)")
    if args.tracked:
        # Walking instead would report on a different file set
        # than the one that was asked for.
        print(f"Not a git checkout (or git unavailable): {REPO_ROOT}")
        return None
    return walked_files(), (
        "every file in the tree "
        "(not a git checkout, so nothing is 'tracked')")


def _load_private_patterns(generic_only):
    """(private_patterns, notes), or none at all under --generic-only.

    CI can never hold the private list: it is gitignored by design, and
    putting the names anywhere a runner can read them would publish
    them. Saying so beats both alternatives -- failing every run, or
    calling a scan clean that never looked for names. The hook checks
    names on every clone that has the list.
    """
    if not generic_only:
        return load_local_patterns()
    print("  name detection skipped (--generic-only): the private pattern "
          "list is per-machine and never present in CI")
    return [], []


def _report(hits, private_pattern_notes, generic_only):
    """Print the verdict and the redacted hits; return the exit code.

    "Clean" is only honest when every category was actually checked:
    no hits with a warning about the private list is still 1, while
    --generic-only is 0 because it says out loud that names were not
    checked, and CI has no way to check them.
    """
    if not hits:
        if private_pattern_notes:
            print("PII scan: no hits from the patterns that RAN -- but see "
                  "the warnings above; this is not a clean bill of health.")
            return 1
        if generic_only:
            print("PII scan: clean for the built-in categories — names were "
                  "not checked here.")
            return 0
        print("PII scan: clean — no personal or identifying data found.")
        return 0
    print(f"PII scan: {len(hits)} potential hit(s) found:\n")
    for relative_path, line_number, label, snippet in hits:
        print(f"  {relative_path}:{line_number}  [{label}]  "
              f"{redact(label, snippet)}")
    print("\nReview each hit. Demo accounts (@demo.com) and reserved test "
          "domains are allowed;\nanything else should be removed, replaced, "
          "or -- once reviewed -- added to scripts/pii_allow.txt.")
    return 1


def main():
    """Scan, report, and return the exit code.

    0 is clean (or clean for the built-in categories under
    --generic-only), 1 is a hit or a category that could not be
    checked, and 2 is a scan that could not start: a --path that is
    not a directory, or --tracked outside a checkout. REPO_ROOT is
    rebound for --path before anything reads it, so every helper
    (including the pattern loaders) works on the repo being scanned.
    """
    global REPO_ROOT
    args = _parse_arguments()
    if args.path:
        REPO_ROOT = os.path.abspath(args.path)
        if not os.path.isdir(REPO_ROOT):
            print(f"No such directory: {REPO_ROOT}")
            return 2

    chosen_files = _choose_files(args)
    if chosen_files is None:
        return 2
    relative_paths, scope_description = chosen_files

    print(f"Scanning {REPO_ROOT}")
    private_patterns, private_pattern_notes = _load_private_patterns(
        args.generic_only)
    allow_patterns, allowlist_notes = load_allowlist()
    for note in private_pattern_notes:
        print(f"  !! {note}")
    for note in allowlist_notes:
        print(f"  !  {note}")
    print(f"  {len(BUILTIN_PATTERNS)} built-in pattern(s), "
          f"{len(private_patterns)} private pattern(s), "
          f"{len(allow_patterns)} reviewed exception(s)")
    print(f"  scanning {scope_description}")

    hits = scan_files(BUILTIN_PATTERNS + private_patterns, allow_patterns,
                      relative_paths)
    return _report(hits, private_pattern_notes, args.generic_only)


if __name__ == "__main__":
    sys.exit(main())
