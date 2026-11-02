#!/usr/bin/env python3
"""
extract_commands.py -- what the ORIGINAL Drobo Dashboard actually sends.

THE PROBLEM THIS EXISTS TO SOLVE
--------------------------------
We recovered all 103 command names and ids from the firmware. What the
firmware does NOT contain is what goes inside each command's <Params>
block, and this project has a standing rule that a guessed parameter is
an unreviewed instruction to somebody's storage -- so every command we
send goes out with empty Params.

That rule is why a whole row of the roadmap is stuck. eCmdSetDimming
(LED brightness), eCmdRename, eCmdGetEventLogs and the share/user
management commands all either need parameters or answer empty without
them, and no amount of reading the firmware will produce them. The one
thing that will is watching the original Dashboard do it, once, and
reading the bytes.

So: run a capture while clicking through Drobo Dashboard 3.5.0, point
this at the file, and it prints every command that went past with its
parameters verbatim. One session can unblock several features at once,
which is the whole reason this is a batch tool rather than another
one-command script like capture-identify.ps1.

  py tools/extract_commands.py <capture.pcapng>
  py tools/extract_commands.py <capture.pcapng> --command eCmdSetDimming
  py tools/extract_commands.py <capture.pcapng> --json out.json

WHAT IT DOES NOT DO
-------------------
It does not tell you a parameter is CORRECT. It tells you what one
version of one Dashboard sent to one device on one day. That is
evidence, and it is the best evidence available, but protocol-map.md's
CONFIRMED / FIRMWARE / GUESS discipline still applies: a parameter seen
once in a capture is CONFIRMED for the case captured and nothing more.
Two units, two firmware versions, or the same button pressed in a
different state may differ.

It also never sends anything. It reads a file.

SECRETS
-------
nasd has no encryption, so a capture taken while you type an admin
password contains that password in clear text. Anything that looks like
a credential is redacted from this tool's OUTPUT -- but the CAPTURE FILE
ITSELF is not sanitized and must not be committed. See
docs/captures/README.md.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from xml.etree import ElementTree
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sdk")))

import nasd_dissect  # noqa: E402 (sibling tool: pcap reading + TCP reassembly)

try:
    from drobo_nasd.commands import COMMANDS  # name -> id
except ImportError:  # pragma: no cover - only when run outside the repo
    COMMANDS = {}

_ID_TO_NAME = {v: k for k, v in COMMANDS.items()}

#: The command channel. Status (5000) carries no commands worth
#: extracting -- the device just pushes its greeting there.
DEFAULT_COMMAND_PORT = 5001

#: Same idea as config_cmd._CREDENTIAL_NAME_RE: match the FIELD
#: NAME, not the value, so a password is redacted whatever it
#: happens to contain.
_CREDENTIAL_NAME_RE = re.compile(r"pass|pwd|secret|token|credential|key", re.I)
_REDACTION_PLACEHOLDER = "[redacted]"

#: A whole XML document, as this firmware likes to nest them: escaped
#: text inside an element. Mistaking these for plain strings is what hid
#: the temperature and the performance counters for weeks, so we unwrap
#: them here too rather than printing an unreadable one-line blob.
_LOOKS_LIKE_XML_RE = re.compile(r"^\s*<\?xml|^\s*<[A-Za-z]")

#: How wide the rules between printed entries are.
_RULE_WIDTH = 72


def _blank_element(element: ElementTree.Element) -> None:
    """Replace an element's text, attributes and children with the
    redaction placeholder, so nothing under a credential survives."""
    element.text = _REDACTION_PLACEHOLDER
    element.attrib.clear()
    for child in list(element):
        element.remove(child)


def _redact_secret_elements(element: ElementTree.Element) -> None:
    """Blank any element whose TAG looks like a credential,
    children and all.
    """
    for child in list(element):
        if _CREDENTIAL_NAME_RE.search(child.tag):
            _blank_element(child)
            continue
        _redact_secret_elements(child)


def _indent(element: ElementTree.Element, level: int = 0) -> None:
    """ElementTree.indent equivalent, spelled out so the output is
    stable across versions.
    """
    newline_indent = "\n" + "  " * level
    if not len(element):
        if level and not (element.tail or "").strip():
            element.tail = newline_indent
        return
    if not (element.text or "").strip():
        element.text = newline_indent + "  "
    for child in element:
        _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = newline_indent + "  "
    if not (element[-1].tail or "").strip():
        element[-1].tail = newline_indent


def _pretty_xml(element: ElementTree.Element) -> str:
    """`element` as indented text.

    Indented with the hand-rolled _indent rather than
    ElementTree.indent, so the printed parameters look the same on
    every Python version and two captures can be diffed.
    """
    _indent(element)
    return ElementTree.tostring(element, encoding="unicode").strip()


def unwrap_escaped(text: str) -> str | None:
    """
    If `text` is itself an XML document (this firmware nests them as
    escaped text), return it pretty-printed. Otherwise None.

    The nested document is redacted like the outer one, because a
    credential can sit inside it just as easily.
    """
    if not text or not _LOOKS_LIKE_XML_RE.match(text):
        return None
    try:
        inner_root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    _redact_secret_elements(inner_root)
    return _pretty_xml(inner_root)


#: The XML root of a command, and of a reply. NOT "DRINETTM" -- that is
#: the eight-byte binary FRAME SIGNATURE that precedes each document on
#: the wire, and the first version of this tool scanned for "<DRINETTM"
#: as though it were a tag. It found nothing on a capture containing 137
#: perfectly good frames, which is what caught it. Confirmed against a
#: real Drobo Dashboard 3.5.0 session, 2026-08-05.
_DOCUMENT_ROOT_TAGS = (b"TMCmd", b"Result")


#: The opening tag of either root, and only of a root: the lookahead
#: stops "<Result" matching the "<ResultDetails" a reply also carries.
_DOCUMENT_OPENING_RE = re.compile(
    b"<(" + b"|".join(_DOCUMENT_ROOT_TAGS) + rb")(?=[\s/>])")


def find_xml_documents(stream_bytes: bytes) -> list[dict]:
    """
    Pull every command/reply document out of a reassembled byte stream.

    Framing-agnostic on purpose. nasd_dissect infers the binary header
    layout, and it is right, but this tool's job is the PAYLOAD -- which
    is self-delimiting text with an unmistakable root tag. Scanning for
    the tag means a header variant we have not seen costs us nothing
    here, where depending on the inferred framing would make this tool
    fail on exactly the novel captures it exists to read.

    One scan, for whichever root opens first, resuming after that
    document's end. The roots used to be scanned for separately, so the
    <Result> child inside every reply was found again as a document of
    its own -- a phantom command per reply. A stretch that does not
    parse is skipped rather than ending the scan, because one damaged
    frame should not hide the rest.
    """
    documents: list[dict] = []
    search_from = 0
    while True:
        opening = _DOCUMENT_OPENING_RE.search(stream_bytes, search_from)
        if opening is None:
            return documents
        closing = b"</" + opening.group(1) + b">"
        end = stream_bytes.find(closing, opening.start())
        if end < 0:
            # Never closed (a capture cut short): look past it.
            search_from = opening.end()
            continue
        search_from = end + len(closing)
        raw_document = stream_bytes[opening.start():search_from]
        try:
            root = ElementTree.fromstring(
                raw_document.decode("utf-8", "replace"))
        except ElementTree.ParseError:
            continue
        documents.append({"offset": opening.start(), "root": root,
                          "raw": raw_document})


def _first_child_text(root: ElementTree.Element, *tag_names: str) -> str:
    """The stripped text of the first of `tag_names` that `root` has
    with text, or "".

    Several spellings are accepted because firmware versions (and this
    tool's own early guesses) name the same element differently, and
    tolerating each costs nothing.
    """
    for name in tag_names:
        child = root.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _command_identity(command_text: str) -> tuple[int | None, str]:
    """(command id, command name) for the text of a <CmdID>, which may
    hold either the number or the name."""
    if command_text.isdigit():
        command_id = int(command_text)
        return command_id, _ID_TO_NAME.get(command_id, "")
    return COMMANDS.get(command_text), command_text


def _describe_params(
        params_element: ElementTree.Element) -> tuple[str, list[dict]]:
    """The redacted, pretty-printed <Params>, and every escaped
    document nested inside it.

    It works on a copy, so redacting never alters the parsed
    document a caller still holds. The copy drops the element's tail:
    it used to be made by a tostring/fromstring round trip, which
    carries the tail, so text after </Params> raised ParseError
    ("junk after document element") and ended the whole run.
    """
    params_copy = copy.deepcopy(params_element)
    params_copy.tail = None
    _redact_secret_elements(params_copy)
    params_xml = _pretty_xml(params_copy)
    escaped_documents = []
    for params_node in params_copy.iter():
        unwrapped = unwrap_escaped((params_node.text or "").strip())
        if unwrapped:
            escaped_documents.append(
                {"under": params_node.tag, "xml": unwrapped})
    return params_xml, escaped_documents


def describe_document(root: ElementTree.Element) -> dict:
    """
    Reduce one command (or reply) document to the facts worth printing.

    The id lives in <CmdID>. "Command" is checked too because that is
    what this tool originally guessed the element was called, and a
    capture where every command came back as "(none)" is how the guess
    was found out -- the real documents look like:

        <TMCmd><CmdID>30</CmdID><Params>...</Params><ESAID>...</ESAID></TMCmd>

    Both are accepted rather than one replaced, since a different
    firmware version naming it differently costs nothing to tolerate.
    """
    command_text = _first_child_text(root, "CmdID", "cmdid", "Command",
                                     "command")
    command_id, command_name = _command_identity(command_text)

    params_element = root.find("Params")
    if params_element is None:
        params_element = root.find("params")

    command_entry: dict = {
        "command_id": command_id,
        "command": command_name or command_text or "(none)",
        "has_params": params_element is not None and (
            len(params_element) > 0
            or bool((params_element.text or "").strip())),
        "params_xml": "",
        "escaped_documents": [],
        "result": _first_child_text(root, "Result", "result"),
    }

    if params_element is not None:
        (command_entry["params_xml"],
         command_entry["escaped_documents"]) = _describe_params(
             params_element)
    return command_entry


def _entries_for_direction(stream: dict, direction: str) -> list[dict]:
    """Describe every document in one direction of a stream, each
    labelled with the endpoints it travelled between."""
    stream_bytes = nasd_dissect.reassemble(stream[direction])
    if not stream_bytes:
        return []
    client_address = stream.get("client") or ("?", 0)
    server_address = stream.get("server") or ("?", 0)
    client_text = f"{client_address[0]}:{client_address[1]}"
    server_text = f"{server_address[0]}:{server_address[1]}"
    entries = []
    for document in find_xml_documents(stream_bytes):
        document_entry = describe_document(document["root"])
        document_entry["stream"] = (
            f"{client_text} -> {server_text}"
            if direction == "to_server"
            else f"{server_text} -> {client_text}")
        entries.append(document_entry)
    return entries


def extract_from_capture(path: str,
                         port: int = DEFAULT_COMMAND_PORT) -> dict:
    """Every request and response on one TCP port of a capture file.

    Takes the capture path, not parsed packets, so a caller (and the
    test suite) can point it at a file and get the same dict the --json
    output prints.
    """
    packets = nasd_dissect.read_capture(path)
    streams = nasd_dissect.collect_streams(packets, port)
    if not streams:
        return {"port": port, "requests": [], "responses": [], "streams": 0}

    requests: list[dict] = []
    responses: list[dict] = []
    for stream in streams.values():
        # Filed by direction: what went to the port is a request, what
        # came back is a response. collect_streams always knows which,
        # since the side that owns the port is the device. Content
        # cannot tell them apart -- a reply has the same TMCmd/CmdID
        # root as its command -- and classifying on it filed every
        # reply as a request (564 requests, 0 responses on 2026-08-06).
        requests.extend(_entries_for_direction(stream, "to_server"))
        responses.extend(_entries_for_direction(stream, "to_client"))
    return {"port": port, "requests": requests, "responses": responses,
            "streams": len(streams)}


def _build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        description="List every command the original Drobo Dashboard "
                    "sent, with parameters.")
    parser.add_argument("capture", help="a .pcap or .pcapng file")
    parser.add_argument("--port", type=int, default=DEFAULT_COMMAND_PORT,
                        help=f"TCP port to read "
                             f"(default {DEFAULT_COMMAND_PORT}, "
                             f"the command channel)")
    parser.add_argument("--command", default="",
                        help="show only this command (name or id), "
                             "e.g. eCmdSetDimming")
    parser.add_argument("--json", default="",
                        help="also write the full result to this file")
    return parser


def _print_no_streams_help() -> None:
    """Why a capture can have nothing on the port, since both usual
    causes are fixed by recording again rather than by this tool."""
    print("\n  Nothing on that port. Two usual reasons:")
    print("    - the capture was taken on the status port (5000); "
          "commands are on 5001")
    print("    - the Dashboard was already connected before recording "
          "started, so the")
    print("      commands you wanted went past before the capture began")


def _print_command_counts(all_requests: list[dict]) -> None:
    """How often each command was sent, across the whole capture (not
    only the --command filter), most frequent first."""
    command_counts = Counter(request["command"]
                             for request in all_requests)
    print("\n  Commands seen, most frequent first:")
    for command_name, count in command_counts.most_common():
        command_id = next((request["command_id"]
                           for request in all_requests
                           if request["command"] == command_name), None)
        print(f"    {count:4d}  {command_name}"
              + (f"  (id {command_id})" if command_id is not None else ""))


def _print_command_entry(command_entry: dict) -> None:
    """One command under a heading: its redacted parameters and any
    escaped documents nested inside them."""
    print("\n" + "-" * _RULE_WIDTH)
    entry_heading = command_entry["command"]
    if command_entry["command_id"] is not None:
        entry_heading += f"  (id {command_entry['command_id']})"
    print(entry_heading)
    print("-" * _RULE_WIDTH)
    if not command_entry["has_params"]:
        print("  <Params> empty -- same as what we already send.")
        return
    for line in command_entry["params_xml"].splitlines():
        print("  " + line)
    for escaped_document in command_entry["escaped_documents"]:
        print(f"\n  ...and nested inside <{escaped_document['under']}>, "
              f"an escaped document:")
        for line in escaped_document["xml"].splitlines():
            print("    " + line)


def _requests_matching(requests: list[dict],
                       wanted_command: str) -> list[dict]:
    """The requests whose command name or id is `wanted_command`, so
    --command accepts either."""
    return [request for request in requests
            if request["command"] == wanted_command
            or str(request["command_id"]) == wanted_command]


def _print_shown_requests(requests: list[dict]) -> None:
    """How many of the shown requests carried parameters, then each
    one in full.

    The point of the whole tool: which commands were sent WITH
    parameters. Those are the ones whose <Params> we could never
    recover from firmware.
    """
    with_params = [request for request in requests if request["has_params"]]
    print(f"\n  {len(with_params)} of {len(requests)} "
          f"shown carried parameters.")
    for command_entry in requests:
        _print_command_entry(command_entry)


def _print_closing_caveat() -> None:
    """The reminder that a capture is evidence for one case only,
    printed last so it is the thing left on screen."""
    print("\n" + "=" * _RULE_WIDTH)
    print("Remember: this is what ONE Dashboard sent to ONE device "
          "on ONE day.")
    print("Record it in your protocol notes as CONFIRMED "
          "for the case captured,")
    print("and not as a general specification. "
          "Do not commit the capture file.")


def main(argv=None) -> int:
    """Print the commands in one capture, optionally filtered to one
    command and written to a JSON file.

    Returns 2 when the capture does not exist, 1 when it holds no
    matching commands, and 0 otherwise.
    """
    args = _build_parser().parse_args(argv)

    if not os.path.exists(args.capture):
        print(f"No such capture: {args.capture}")
        return 2

    result = extract_from_capture(args.capture, args.port)
    requests = result["requests"]

    if args.command:
        requests = _requests_matching(requests, args.command.strip())

    print(f"\n{os.path.basename(args.capture)} -- TCP {result['port']}, "
          f"{result['streams']} stream(s), "
          f"{len(result['requests'])} command(s) sent")

    if not result["streams"]:
        _print_no_streams_help()
        return 1

    if not requests:
        print("\n  No commands matched." if args.command else
              "\n  No commands found -- "
              "the stream carried no <DRINETTM> documents.")
        return 1

    _print_command_counts(result["requests"])
    _print_shown_requests(requests)
    _print_closing_caveat()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as json_file:
            json.dump(result, json_file, indent=2)
        print(f"\nFull result written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
