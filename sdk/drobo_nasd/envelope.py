"""
DRINETTM XML envelope -- build and parse, per docs/protocol-map.md
("Message envelope").

`FIRMWARE`-grade evidence for the vocabulary (recovered from adjacent
string clusters in `nasd`, not from a capture):

    <?xml version="1.0"?>
    <DRINETTM>
      <ESAID>...</ESAID>
      <Command>...</Command>        <!-- numeric id OR name
                                         -- GUESS which -->
      <Params>...</Params>
      <Error>0</Error>              <!-- response only -->
      <Result>...</Result>          <!-- response only -->
      <ResultDetails>...</ResultDetails>
    </DRINETTM>

What's confirmed: the root tag, ESAID, Params-as-a-container, and that
`<Error>N</Error>` is emitted as a literal string (so it appears
verbatim on the wire, per protocol-map.md). What's guessed: every other
child element name, and whether Command carries the numeric id or the
`eCmd*` name -- protocol-map.md shows it both ways
("<Command>85</Command> <!-- or the name -->"). This module supports
both so that whichever the capture confirms, nothing here has to change
shape -- only which form callers pass in.

This module only builds and parses the XML text. It knows nothing about
sockets or the still-unknown byte framing around a message -- see
framing.py and client.py for that.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from . import commands

ROOT_TAG = "DRINETTM"

#: The number a live 5N puts in <Result> when it accepts a command.
#: MEASURED 2026-08-07, the same value from eCmdGetSysInfo,
#: eCmdGetConfig and eCmdIdentify -- two commands that demonstrably work
#: and one that demonstrably does nothing. It also sits, uninterpreted,
#: in a sysinfo test fixture recorded weeks earlier, so it has been
#: constant for as long as this project has been looking.
#:
#: WHAT THIS IS NOT: proof that a different value means failure. No
#: command has ever returned anything else here, so the failure side of
#: the comparison has never been observed. Treat a match as "the device
#: did not object" and a mismatch as "worth looking at", never as a
#: confirmed error.
#:
#: And note what eCmdIdentify proves: ACCEPTED IS NOT THE SAME AS DID
#: SOMETHING. Identify returns this value and blinks nothing at all.
RESULT_ACCEPTED = 0x2_0000_0000  # 8589934592
_XML_DECLARATION = '<?xml version="1.0"?>'  # exact string per protocol-map.md
_INTEGER_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.\d+")


class EnvelopeError(ValueError):
    """Raised for malformed or unrecognisable DRINETTM messages.

    A ValueError subclass because a bad message is bad input, so a
    caller that already handles ValueError keeps working, while one
    that cares can catch this narrower type.
    """


@dataclass
class Envelope:
    """The parsed contents of a DRINETTM message.

    `command_raw` is kept beside command_id and command_name because
    whether <Command> carries the numeric id or the eCmd* name is still
    a guess (see the module docstring). Keeping exactly what the wire
    said means a capture can settle the question without anything here
    having thrown the evidence away.
    """

    esa_id: str
    # None if the wire value didn't resolve to a known id
    command_id: Optional[int]
    # None if command_id has no entry in commands.COMMANDS
    command_name: Optional[str]
    # exactly what was in <Command>, before any interpretation
    command_raw: str
    params: dict = field(default_factory=dict)
    error: Optional[int] = None
    result: Any = None
    result_details: Any = None


# ---------------------------------------------------------------------------
# value <-> XML helpers (used for Params, Result, ResultDetails)
# ---------------------------------------------------------------------------


def _to_wire_text(value: Any) -> str:
    """
    The text a value is written as on the wire.

    A bool becomes "1" or "0" rather than str()'s "True"/"False": the
    protocol carries every value as text, and digits are what a reader
    on the other end can turn back into a number (_coerce_wire_value
    does exactly that here); "True" is not. The check is needed at all
    because str(True) gives "True", and everything else is written as
    its str().
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _coerce_wire_value(text: Optional[str]) -> Any:
    """Best-effort str -> int/float, mirroring what a hand-rolled XML
    reader on the other end would need to do too -- <Error>0</Error> is
    a literal string on the wire, not a typed value."""
    if text is None:
        return ""
    if _INTEGER_RE.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            pass
    elif _FLOAT_RE.fullmatch(text):
        try:
            return float(text)
        except ValueError:
            pass
    return text


def _build_container(parent: ElementTree.Element,
                     values_by_tag: dict) -> None:
    """Recursively populate `parent` from a dict. Nested dicts become
    nested elements; lists/tuples repeat the same tag once per item
    (this is how UXMLNode-style trees typically represent repeated
    records)."""
    for tag, value in values_by_tag.items():
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            _append_value(parent, tag, item)


def _append_value(parent: ElementTree.Element, tag: str,
                  value: Any) -> None:
    """
    Add one <tag> under `parent`: a nested container for a dict, text
    for anything else.

    Lists are expanded by the caller, one call per item, so a list
    nested inside a list is written as its text rather than expanded
    again -- exactly what _build_container has always done.
    """
    child = ElementTree.SubElement(parent, tag)
    if isinstance(value, dict):
        _build_container(child, value)
        return
    child.text = _to_wire_text(value)


def _parse_container(element: ElementTree.Element) -> dict:
    """Inverse of _build_container.
    Repeated tags collapse into a list.

    Leaf text is coerced to int or float where it looks like one, since
    the wire carries every value as text (see _coerce_wire_value).
    """
    values_by_tag: dict = {}
    for child in element:
        value: Any
        if len(child):
            value = _parse_container(child)
        else:
            value = _coerce_wire_value(child.text)
        if child.tag not in values_by_tag:
            values_by_tag[child.tag] = value
            continue
        existing = values_by_tag[child.tag]
        if isinstance(existing, list):
            existing.append(value)
        else:
            values_by_tag[child.tag] = [existing, value]
    return values_by_tag


def _resolve_command(
    command: Union[int, str],
) -> tuple[str, Optional[int], Optional[str]]:
    """Returns (wire_text, command_id, command_name) for a
    caller-supplied command, which may be a numeric id or an `eCmd*`
    name.

    A name has to be looked up to find its id, so a name missing from
    commands.COMMANDS cannot be sent at all and is almost certainly a
    typo: it raises KeyError. An int is already the wire form and needs
    no lookup, so it passes through, with command_name None when the
    table has no entry for it."""
    if isinstance(command, str):
        if command not in commands.COMMANDS:
            raise KeyError(
                f"{command!r} is not in commands.COMMANDS -- if this is a "
                f"real eCmd* name missing from the table, fix commands.py; "
                f"otherwise this is a typo"
            )
        command_id = commands.COMMANDS[command]
        return str(command_id), command_id, command
    command_id = int(command)
    return str(command_id), command_id, commands.ID_TO_NAME.get(command_id)


def _serialize(root: ElementTree.Element) -> str:
    """
    The message text: the exact declaration, a newline, then the XML.

    The declaration is written by hand because protocol-map.md records
    this exact string, and ElementTree's own declaration is a different
    one (single quotes, with an encoding).
    """
    body = ElementTree.tostring(root, encoding="unicode")
    return f"{_XML_DECLARATION}\n{body}"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _build_request_root(esa_id: Union[str, int], command: Union[int, str],
                        params: Optional[dict]) -> ElementTree.Element:
    """
    The <DRINETTM> root with ESAID, Command and (when given) Params.

    Shared by build_command and build_response, so a response built for
    a test starts exactly the way a real request does.
    """
    wire_text, _, _ = _resolve_command(command)
    root = ElementTree.Element(ROOT_TAG)
    ElementTree.SubElement(root, "ESAID").text = str(esa_id)
    ElementTree.SubElement(root, "Command").text = wire_text
    if params:
        params_element = ElementTree.SubElement(root, "Params")
        _build_container(params_element, params)
    return root


def build_command(
    esa_id: Union[str, int],
    command: Union[int, str],
    params: Optional[dict] = None,
) -> str:
    """
    Build a DRINETTM *request* message: ESAID, Command, optional Params.

    `command` may be a numeric id (int) or an `eCmd*` name (str) -- see
    commands.py. This function does NOT check commands.DO_NOT_SEND; that
    guard lives in client.py, which is the layer that actually has a
    socket.
    """
    return _serialize(_build_request_root(esa_id, command, params))


def build_response(
    esa_id: Union[str, int],
    command: Union[int, str],
    error: int = 0,
    params: Optional[dict] = None,
    result: Any = None,
    result_details: Any = None,
) -> str:
    """
    Build a DRINETTM *response* message: everything build_command() has,
    plus Error (and optionally Result/ResultDetails). Mainly useful for
    tests -- real responses come from the device -- but keeping build
    and parse symmetric is what makes the round-trip test meaningful.
    """
    root = _build_request_root(esa_id, command, params)
    ElementTree.SubElement(root, "Error").text = str(int(error))
    if result is not None:
        _append_value(root, "Result", result)
    if result_details is not None:
        _append_value(root, "ResultDetails", result_details)
    return _serialize(root)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def parse(xml_text: str) -> Envelope:
    """Parse a DRINETTM message (request or response shape -- whichever
    elements are present come back populated, the rest stay
    None/empty).

    Lenient on purpose: which child elements the device really sends is
    mostly a guess (see the module docstring), so only the root,
    <ESAID> and <Command> are required, and everything else is read
    when it is there."""
    document_text = _without_declaration(xml_text.strip())
    try:
        root = ElementTree.fromstring(document_text)
    except ElementTree.ParseError as exc:
        raise EnvelopeError(f"not well-formed XML: {exc}") from exc

    if root.tag != ROOT_TAG:
        raise EnvelopeError(f"expected root <{ROOT_TAG}>, got <{root.tag}>")

    esa_id_element = root.find("ESAID")
    command_element = root.find("Command")
    if esa_id_element is None or command_element is None:
        raise EnvelopeError("DRINETTM message missing <ESAID> or <Command>")

    esa_id = esa_id_element.text or ""
    command_raw = command_element.text or ""
    command_id, command_name = _resolve_wire_command(command_raw)

    params_element = root.find("Params")
    params = (_parse_container(params_element)
              if params_element is not None else {})

    error_code = _parse_error_code(root)

    # See RESULT_ACCEPTED for what the number in here means, and how
    # little of that is actually known.
    result = _parse_optional_value(root.find("Result"))
    result_details = _parse_optional_value(root.find("ResultDetails"))

    return Envelope(
        esa_id=esa_id,
        command_id=command_id,
        command_name=command_name,
        command_raw=command_raw,
        params=params,
        error=error_code,
        result=result,
        result_details=result_details,
    )


def _without_declaration(document_text: str) -> str:
    """
    The document with any leading <?xml ...?> declaration dropped.

    stdlib's parser is fine with a declaration, but dropping it here
    documents that the exact declaration text is not itself meaningful
    to us. One with no "?>" is malformed and is an EnvelopeError: until
    2026-11-02 split("?>", 1)[1] let an IndexError out instead.
    """
    if not document_text.startswith("<?xml"):
        return document_text
    _, separator, document_text = document_text.partition("?>")
    if not separator:
        raise EnvelopeError("unterminated XML declaration")
    return document_text.strip()


def _resolve_wire_command(
    command_raw: str,
) -> tuple[Optional[int], Optional[str]]:
    """
    (command_id, command_name) for the text found in <Command>.

    Digits are read as the numeric id and anything else as an eCmd*
    name, because the wire form is not yet known; either part is None
    when the table has no entry for it.

    Digits int() refuses (more than Python's 4300-digit limit) are an
    EnvelopeError, the one error parse promises: until 2026-11-02
    int()'s ValueError escaped, the case _coerce_wire_value already
    guarded.
    """
    if _INTEGER_RE.fullmatch(command_raw):
        try:
            command_id = int(command_raw)
        except ValueError as exc:
            raise EnvelopeError(
                f"<Command> is a number of {len(command_raw)} digits, "
                f"too long to read") from exc
        return command_id, commands.ID_TO_NAME.get(command_id)
    command_name = (command_raw if command_raw in commands.COMMANDS
                    else None)
    return commands.COMMANDS.get(command_raw), command_name


def _parse_error_code(root: ElementTree.Element) -> Optional[int]:
    """The integer in <Error>, or None when the element is absent or
    empty (a request carries none).

    Text that is not an integer is an EnvelopeError, the one error
    parse promises; until 2026-11-02 int()'s ValueError escaped.
    """
    error_element = root.find("Error")
    if error_element is None or not error_element.text:
        return None
    try:
        return int(error_element.text)
    except ValueError as exc:
        raise EnvelopeError(
            f"<Error> is not an integer: {error_element.text!r}") from exc


def _parse_optional_value(element: Optional[ElementTree.Element]) -> Any:
    """
    A <Result> or <ResultDetails> as a value: None when absent, a dict
    when it has children, otherwise its coerced text.

    The device may put either a typed scalar or a whole tree in these,
    so both shapes are accepted.
    """
    if element is None:
        return None
    if len(element):
        return _parse_container(element)
    return _coerce_wire_value(element.text)
