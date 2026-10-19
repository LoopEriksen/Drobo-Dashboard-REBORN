#!/usr/bin/env python3
"""
Offline tests for dashboard_strings.py's redaction. No Drobo needed.

The tool reads the original Dashboard's program files, and those files
carry a hardcoded key for Drobo's dead API Gateway. Its promise is that
a credential never reaches its output. Two holes broke that promise:

  1. Only a long key-shaped run was caught. A key that arrives with its
     field name ("x-api-key: ...", "?api_key=...") was printed in clear,
     and so was the separate literal that follows a bare "x-api-key" in
     --near output.
  2. A key-shaped run was let through whenever its first 20 characters
     looked like a tag, so a letter-led 40-character key passed.

Every string and blob here is synthetic. No vendor binary is read.

    py tools/test_dashboard_strings.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dashboard_strings  # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label
          + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def utf16_blob(*literals):
    """Literals as a native binary stores wide strings: NUL-ended."""
    return b"".join(text.encode("utf-16-le") + b"\x00\x00"
                    for text in literals)


def ascii_blob(*literals):
    """Literals as a native binary stores narrow strings: NUL-ended."""
    return b"".join(text.encode("ascii") + b"\x00" for text in literals)


redact = dashboard_strings.redact
REDACTED = dashboard_strings._REDACTED

# Synthetic values in the shapes the real client holds, never its key.
LETTER_LED_KEY = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
LATE_SYMBOL_KEY = "Abcdefghijklmnopqrst-uvwxyz0123456789"
DIGIT_LED_KEY = "0123456789AbCdEfGhIjKlMnOpQrStUv"
HEADER_VALUE = "AbCdEf123"
QUERY_URL = "https://example.invalid/prod/send?api_key=AbCdEf123456"

print("== a field name with its value is redacted ==")
check("an x-api-key header line is redacted",
      redact(f"x-api-key: {HEADER_VALUE}") == REDACTED,
      redact(f"x-api-key: {HEADER_VALUE}"))
check("a URL carrying ?api_key= is redacted",
      redact(QUERY_URL) == REDACTED, redact(QUERY_URL))
check("a client_secret= pair is redacted",
      redact("client_secret=AbCdEf123") == REDACTED)

print("\n== a URL that only mentions a field name is kept ==")
for url in ("https://example.invalid/oauth/token",
            "https://example.invalid/account/forgot-password"):
    check(f"{url} is kept", redact(url) == url, redact(url))

print("\n== a key that starts like a tag is still a key ==")
check("a letter-led 40-character key is redacted",
      redact(LETTER_LED_KEY) == REDACTED, redact(LETTER_LED_KEY))
check("a key whose first symbol comes after character 20 is redacted",
      redact(LATE_SYMBOL_KEY) == REDACTED, redact(LATE_SYMBOL_KEY))
check("a digit-led key is still redacted",
      redact(DIGIT_LED_KEY) == REDACTED)

print("\n== protocol vocabulary is kept ==")
for tag in ("DimmingLevel", "MailServer", "Password",
            "SomeVeryLongCamelCaseTagName", "DNASGetEmailAlertsConfig"):
    check(f"{tag} is kept", redact(tag) == tag, redact(tag))

print("\n== near() ==")
wide = utf16_blob("MailServer", "x-api-key", HEADER_VALUE, "DimmingLevel")
around = dashboard_strings.near(wide, "MailServer")
check("near() finds the anchor in a UTF-16 blob",
      "MailServer" in around, around)
check("near() keeps the field name itself",
      "x-api-key" in around, around)
check("near() redacts the literal after x-api-key",
      HEADER_VALUE not in around and REDACTED in around, around)
check("-- and only that one: the next literal is kept",
      "DimmingLevel" in around, around)
# 19 tags put "x-api-key" one literal outside the 20-literal window
# before the anchor, so its value is the window's first literal.
edge = utf16_blob("x-api-key", HEADER_VALUE,
                  *[f"Tag{number:02d}Name" for number in range(19)],
                  "MailServer")
edge_around = dashboard_strings.near(edge, "MailServer")
check("near() redacts a value whose field name is just outside the window",
      HEADER_VALUE not in edge_around and REDACTED in edge_around,
      edge_around)
check("-- and keeps the tag after it",
      "Tag00Name" in edge_around, edge_around)
inline = utf16_blob("MailServer", f"x-api-key: {HEADER_VALUE}", "UseSSL")
inline_around = dashboard_strings.near(inline, "MailServer")
check("near() redacts a header and value held as one literal",
      not any(HEADER_VALUE in text for text in inline_around),
      inline_around)
check("near() redacts a letter-led key in its window",
      LETTER_LED_KEY not in dashboard_strings.near(
          utf16_blob("DimmingLevel", LETTER_LED_KEY), "DimmingLevel"))
check("near() of a string that is not there is empty",
      dashboard_strings.near(wide, "NotInTheBlob") == [])

print("\n== endpoints() ==")
found = dashboard_strings.endpoints(
    ascii_blob(QUERY_URL, "https://example.invalid/oauth/token"))
check("endpoints() never prints the key from ?api_key=",
      not any("AbCdEf123456" in text for text in found), found)
check("-- it shows the redaction in its place",
      REDACTED in found, found)
check("endpoints() keeps a URL whose path says /token",
      "https://example.invalid/oauth/token" in found, found)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
