#!/usr/bin/env python3
"""
dashboard_strings.py -- mine the ORIGINAL Dashboard client for protocol
vocabulary.

WHY THIS EXISTS, AND WHY IT SHOULD HAVE EXISTED SOONER
-------------------------------------------------------
This project mined the FIRMWARE hard (see fw_params.py) and treated the
original Windows Dashboard as a thing to capture packets from. That was
a blind spot, and the maintainer pointed it out: the firmware is the
SERVER, which *receives* commands, while the Dashboard is the CLIENT,
which *builds* them. If you want to know what goes inside <Params>, the
side that constructs it is the better place to look.

It paid off immediately. `eCmdSetDimming` had been blocked for weeks on
"we do not know its parameter and will not guess one". The firmware
yielded nothing. The client holds the answer as a plain UTF-16 string:
**DimmingLevel**.

WHAT IT CAN AND CANNOT TELL YOU
--------------------------------
It recovers the VOCABULARY -- the exact spelling of tags the client
uses. It does not recover the NESTING, because the strings are scattered
literals grouped by code paths rather than laid out in document order.
Knowing `DimmingLevel` exists does not prove the body is
`<Params><DimmingLevel>59</DimmingLevel></Params>` rather than a level
deeper.

So this is FIRMWARE-grade evidence in protocol-map.md's sense: stronger
than a guess, weaker than a capture. A capture still settles the shape.
What this changes is that the capture now has to confirm a specific,
named hypothesis instead of discovering the name from scratch.

TWO ENCODINGS, AND WHY BOTH MATTER
-----------------------------------
These are native Windows C++ binaries. Log/format strings tend to be
ASCII; anything that touched a wide-char API (`%ls` all over the logging
proves it did) is UTF-16LE. `DimmingLevel` exists ONLY as UTF-16 -- an
ASCII-only scan misses it entirely, which is exactly how a
`strings`-style pass would conclude "not there".

SECRETS
-------
The client ships hardcoded credentials for services that no longer
exist. They are redacted from this tool's output on sight: dead or not,
this repository does not carry other people's keys, and a key printed
into a commit message or a terminal log is a key in the repository's
history forever.

    py tools/dashboard_strings.py
        "C:/Program Files (x86)/Drobo/Drobo Dashboard/DDService.exe"
    py tools/dashboard_strings.py <exe> --near DimmingLevel
    py tools/dashboard_strings.py <exe> --gaps
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sdk")))

try:
    from drobo_nasd.commands import COMMANDS
except ImportError:  # pragma: no cover
    COMMANDS = {}

_ASCII_RE = re.compile(rb"[\x20-\x7e]{3,}")
_UTF16_RE = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")

#: An XML tag as these binaries spell them.
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,40}$")

#: Anything that looks like a credential. Long high-entropy
#  runs, and the field
#: names that introduce them. Matched generously -- a false
#  positive costs one
#: redacted line, a false negative puts somebody's key in a commit.
_CREDENTIAL_SHAPED_RE = re.compile(r"^[A-Za-z0-9+/_-]{24,}={0,2}$")
_KEY_CONTEXT = re.compile(r"api.?key|secret|token|password|credential", re.I)
#: A field name with its value in the same string: "x-api-key: ...",
#: "?api_key=...". The name has to be followed directly by ":" or "=",
#: so a URL whose path merely says /token or /forgot-password is kept;
#: matching any ":" would count the "://" of every URL.
_KEY_ASSIGNMENT = re.compile(
    "(?:" + _KEY_CONTEXT.pattern + r")\w*\s*[:=]\s*\S", re.I)
_REDACTION_PLACEHOLDER = "[redacted: credential-shaped string]"

#: Noise that passes the tag shape but is plainly not
#  protocol vocabulary.
_NOISE_WORDS = {
    "true", "false", "null", "NULL", "None", "Error", "ERROR", "INFO", "WARN",
    "DEBUG", "Software", "Drobo", "Windows", "Microsoft", "System", "String",
    "Params", "Result", "CmdID", "ESAID", "TMCmd",
}


def extract_printable_strings(
        binary_bytes: bytes) -> list[tuple[int, str, str]]:
    """
    Every printable run, in BOTH encodings, as (offset, text, encoding).

    Both, always. `DimmingLevel` -- the string this whole tool justified
    itself with -- exists only as UTF-16, so an ASCII-only pass reports
    it missing.
    """
    found_strings = [(match.start(),
                      match.group().decode("ascii", "replace"), "ascii")
                     for match in _ASCII_RE.finditer(binary_bytes)]
    found_strings += [(match.start(),
                       match.group().decode("utf-16-le", "replace"),
                       "utf-16")
                      for match in _UTF16_RE.finditer(binary_bytes)]
    found_strings.sort(key=lambda entry: entry[0])
    return found_strings


def redact(text: str) -> str:
    """Never let a credential-shaped string reach the output."""
    if _KEY_ASSIGNMENT.search(text):
        return _REDACTION_PLACEHOLDER
    # A long run with any digit or symbol in it. Only pure letters pass,
    # as a long CamelCase tag does; a key that merely starts like a tag
    # (a letter-led 40-character API-Gateway key) is still a key.
    if _CREDENTIAL_SHAPED_RE.match(text) and not text.isalpha():
        return _REDACTION_PLACEHOLDER
    return text


def is_protocol_tag(text: str) -> bool:
    return (bool(_TAG_RE.match(text)) and text not in _NOISE_WORDS
            and not text.startswith("eCmd"))


def count_tags(binary_bytes: bytes) -> dict[str, int]:
    """Every tag-shaped string, with how many times it appears."""
    tag_counts: dict[str, int] = {}
    for _offset, text, _encoding in extract_printable_strings(binary_bytes):
        if is_protocol_tag(text) and not _CREDENTIAL_SHAPED_RE.match(text):
            tag_counts[text] = tag_counts.get(text, 0) + 1
    return tag_counts


def strings_near(binary_bytes: bytes, anchor: str,
                 span: int = 20) -> list[str]:
    """Strings surrounding the first occurrence of `anchor`, in its own
    encoding."""
    all_strings = extract_printable_strings(binary_bytes)
    for index, (_offset, text, anchor_encoding) in enumerate(all_strings):
        if text == anchor:
            start = max(0, index - span)
            window = all_strings[start: index + span]
            # A binary keeps a header name and its value as separate
            # literals, so the one after "x-api-key" goes as well. That
            # name can sit just before the window's left edge, so the
            # scan starts from the nearest literal before it in the
            # same encoding. After a password-named tag such as
            # "SMTPPassword" the next tag is hidden too: losing a tag
            # is the safe way to be wrong. A name in one encoding does
            # not hide a value in the other, which redact() alone
            # decides; the window shows only one encoding anyway.
            before_window = (earlier_text
                             for _offset, earlier_text, earlier_encoding
                             in reversed(all_strings[:start])
                             if earlier_encoding == anchor_encoding)
            after_field_name = bool(
                _KEY_CONTEXT.search(next(before_window, "")))
            shown = []
            for _offset, window_text, window_encoding in window:
                if window_encoding != anchor_encoding:
                    continue
                if after_field_name:
                    shown.append(_REDACTION_PLACEHOLDER)
                else:
                    shown.append(redact(window_text))
                after_field_name = bool(_KEY_CONTEXT.search(window_text))
            return shown
    return []


def find_endpoints(binary_bytes: bytes) -> list[str]:
    """
    Network destinations the client talks to.

    Worth surfacing because of what it revealed here: the original email
    alerts did NOT use SMTP. They called Drobo's own AWS API Gateway,
    which went away with the company -- so "email alerts" cannot be
    reimplemented by copying what the Dashboard did. It has to be built
    fresh.
    """
    endpoints = []
    for _offset, text, _encoding in extract_printable_strings(binary_bytes):
        lowered_text = text.lower()
        if any(marker in lowered_text for marker in (
                "http://", "https://", "amazonaws.com", ".com/", "/prod/")):
            redacted_text = redact(text.strip())
            if redacted_text not in endpoints and len(redacted_text) < 120:
                endpoints.append(redacted_text)
    return endpoints


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Mine the original Drobo Dashboard client for "
                    "protocol vocabulary.")
    parser.add_argument(
        "binary",
        help="Drobo Dashboard.exe, DDService.exe or DDAssist.exe")
    parser.add_argument("--near", default="",
                        help="show strings surrounding this one")
    parser.add_argument(
        "--gaps", action="store_true",
        help="tags the client knows that our SDK never mentions")
    parser.add_argument("--endpoints", action="store_true",
                        help="network destinations")
    parser.add_argument("--min-count", type=int, default=1)
    args = parser.parse_args(argv)

    if not os.path.exists(args.binary):
        print(f"No such file: {args.binary}")
        return 2
    binary_bytes = open(args.binary, "rb").read()

    if args.near:
        nearby_strings = strings_near(binary_bytes, args.near)
        if not nearby_strings:
            print(f"{args.near!r} not found as an exact string (try the "
                  f"other encoding -- some tags exist only as UTF-16).")
            return 1
        print(f"\nStrings around {args.near!r}:\n")
        for nearby_string in nearby_strings:
            print("   ", nearby_string[:100])
        return 0

    if args.endpoints:
        print("\nNetwork destinations in the client:\n")
        for endpoint in find_endpoints(binary_bytes):
            print("   ", endpoint)
        print("\nNote: Drobo's own cloud endpoints are dead -- the "
              "company was")
        print("liquidated in 2023. Anything depending on them cannot be "
              "revived,")
        print("only reimplemented.")
        return 0

    tag_counts = count_tags(binary_bytes)

    if args.gaps:
        # What does the client name that we have never even mentioned?
        # That difference is the honest to-do list for protocol
        # coverage.
        our_source = ""
        sdk_package_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "sdk", "drobo_nasd")
        for file_name in os.listdir(sdk_package_dir):
            if file_name.endswith(".py"):
                our_source += open(os.path.join(sdk_package_dir, file_name),
                                   encoding="utf-8",
                                   errors="replace").read()
        unmentioned_tags = sorted((tag for tag, count in tag_counts.items()
                                   if count >= args.min_count
                                   and tag not in our_source),
                                  key=lambda tag: (-tag_counts[tag], tag))
        print(f"\n{len(unmentioned_tags)} tag(s) the client uses that our "
              f"SDK never mentions.")
        print("Candidates for protocol coverage -- vocabulary, NOT confirmed "
              "nesting.\n")
        for tag in unmentioned_tags[:80]:
            print(f"   {tag_counts[tag]:4}x  {tag}")
        return 0

    print(f"\n{os.path.basename(args.binary)}: {len(tag_counts)} distinct "
          f"tag-shaped strings")
    print("Vocabulary only. The nesting still needs a capture to confirm.\n")
    for tag in sorted(tag_counts,
                      key=lambda tag: (-tag_counts[tag], tag))[:60]:
        print(f"   {tag_counts[tag]:4}x  {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
