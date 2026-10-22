"""
DRINETTM XML envelope -- build and parse, per docs/protocol-map.md
("Message envelope").

`FIRMWARE`-grade evidence for the vocabulary (recovered from adjacent string
clusters in `nasd`, not from a capture):

    <?xml version="1.0"?>
    <DRINETTM>
      <ESAID>...</ESAID>
      <Command>...</Command>        <!-- numeric id OR name -- GUESS which -->
      <Params>...</Params>
      <Error>0</Error>              <!-- response only -->
      <Result>...</Result>          <!-- response only -->
      <ResultDetails>...</ResultDetails>
    </DRINETTM>

What's confirmed: the root tag, ESAID, Params-as-a-container, and that
`<Error>N</Error>` is emitted as a literal string (so it appears verbatim on
the wire, per protocol-map.md). What's guessed: every other child element
name, and whether Command carries the numeric id or the `eCmd*` name --
protocol-map.md shows it both ways ("<Command>85</Command> <!-- or the name
-->"). This module supports both so that whichever the capture confirms,
nothing here has to change shape -- only which form callers pass in.

This module only builds and parses the XML text. It knows nothing about
sockets or the still-unknown byte framing around a message -- see framing.py
and client.py for that.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from . import commands

ROOT_TAG = "DRINETTM"

#: The number a live 5N puts in <Result> when it accepts a command.
#: MEASURED 2026-08-07, the same value from eCmdGetSysInfo, eCmdGetConfig and
#: eCmdIdentify -- two commands that demonstrably work and one that
#: demonstrably does nothing. It also sits, uninterpreted, in a sysinfo test
#: fixture recorded weeks earlier, so it has been constant for as long as this
#: project has been looking.
#:
#: WHAT THIS IS NOT: proof that a different value means failure. No command has
#: ever returned anything else here, so the failure side of the comparison has
#: never been observed. Treat a match as "the device did not object" and a
#: mismatch as "worth looking at", never as a confirmed error.
#:
#: And note what eCmdIdentify proves: ACCEPTED IS NOT THE SAME AS DID
#: SOMETHING. Identify returns this value and blinks nothing at all.
RESULT_ACCEPTED = 0x2_0000_0000  # 8589934592
_XML_DECLARATION = '<?xml version="1.0"?>'  # exact string per protocol-map.md
_INTEGER_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.\d+")


class EnvelopeError(ValueError):
    """Raised for malformed or unrecognisable DRINETTM messages."""


@dataclass
class Envelope:
    """The parsed contents of a DRINETTM message."""

    esa_id: str
    command_id: Optional[int]  # None if the wire value didn't resolve to a known id
    command_name: Optional[str]  # None if command_id has no entry in commands.COMMANDS
    command_raw: str  # exactly what was in <Command>, before any interpretation
    params: dict = field(default_factory=dict)
    error: Optional[int] = None
    result: Any = None
    result_details: Any = None


# ---------------------------------------------------------------------------
# value <-> XML helpers (used for Params, Result, ResultDetails)
# ---------------------------------------------------------------------------


def _to_wire_text(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _coerce_wire_value(text: Optional[str]) -> Any:
    """Best-effort str -> int/float, mirroring what a hand-rolled XML reader
    on the other end would need to do too -- <Error>0</Error> is a literal
    string on the wire, not a typed value."""
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


def _build_container(parent: ElementTree.Element, data: dict) -> None:
    """Recursively populate `parent` from a dict. Nested dicts become nested
    elements; lists/tuples repeat the same tag once per item (this is how
    UXMLNode-style trees typically represent repeated records)."""
    for key, value in data.items():
        if isinstance(value, dict):
            child = ElementTree.SubElement(parent, key)
            _build_container(child, value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                child = ElementTree.SubElement(parent, key)
                if isinstance(item, dict):
                    _build_container(child, item)
                else:
                    child.text = _to_wire_text(item)
        else:
            child = ElementTree.SubElement(parent, key)
            child.text = _to_wire_text(value)


def _parse_container(element: ElementTree.Element) -> dict:
    """Inverse of _build_container. Repeated tags collapse into a list."""
    result: dict = {}
    for child in element:
        value: Any
        if len(child):
            value = _parse_container(child)
        else:
            value = _coerce_wire_value(child.text)
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def _resolve_command(command: Union[int, str]) -> tuple[str, Optional[int], Optional[str]]:
    """Returns (wire_text, command_id, command_name) for a caller-supplied
    command, which may be a numeric id or an `eCmd*` name."""
    if isinstance(command, str):
        if command not in commands.COMMANDS:
            raise KeyError(
                f"{command!r} is not in commands.COMMANDS -- if this is a real "
                f"eCmd* name missing from the table, fix commands.py; otherwise "
                f"this is a typo"
            )
        command_id = commands.COMMANDS[command]
        return str(command_id), command_id, command
    command_id = int(command)
    return str(command_id), command_id, commands.ID_TO_NAME.get(command_id)


def _serialize(root: ElementTree.Element) -> str:
    body = ElementTree.tostring(root, encoding="unicode")
    return f"{_XML_DECLARATION}\n{body}"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build_command(esa_id: Union[str, int], command: Union[int, str], params: Optional[dict] = None) -> str:
    """
    Build a DRINETTM *request* message: ESAID, Command, optional Params.

    `command` may be a numeric id (int) or an `eCmd*` name (str) -- see
    commands.py. This function does NOT check commands.DO_NOT_SEND; that
    guard lives in client.py, which is the layer that actually has a socket.
    """
    wire_text, _, _ = _resolve_command(command)
    root = ElementTree.Element(ROOT_TAG)
    ElementTree.SubElement(root, "ESAID").text = str(esa_id)
    ElementTree.SubElement(root, "Command").text = wire_text
    if params:
        params_element = ElementTree.SubElement(root, "Params")
        _build_container(params_element, params)
    return _serialize(root)


def build_response(
    esa_id: Union[str, int],
    command: Union[int, str],
    error: int = 0,
    params: Optional[dict] = None,
    result: Any = None,
    result_details: Any = None,
) -> str:
    """
    Build a DRINETTM *response* message: everything build_command() has, plus
    Error (and optionally Result/ResultDetails). Mainly useful for tests --
    real responses come from the device -- but keeping build and parse
    symmetric is what makes the round-trip test meaningful.
    """
    wire_text, _, _ = _resolve_command(command)
    root = ElementTree.Element(ROOT_TAG)
    ElementTree.SubElement(root, "ESAID").text = str(esa_id)
    ElementTree.SubElement(root, "Command").text = wire_text
    if params:
        params_element = ElementTree.SubElement(root, "Params")
        _build_container(params_element, params)
    ElementTree.SubElement(root, "Error").text = str(int(error))
    if result is not None:
        result_element = ElementTree.SubElement(root, "Result")
        if isinstance(result, dict):
            _build_container(result_element, result)
        else:
            result_element.text = _to_wire_text(result)
    if result_details is not None:
        details_element = ElementTree.SubElement(root, "ResultDetails")
        if isinstance(result_details, dict):
            _build_container(details_element, result_details)
        else:
            details_element.text = _to_wire_text(result_details)
    return _serialize(root)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def parse(xml_text: str) -> Envelope:
    """Parse a DRINETTM message (request or response shape -- whichever
    elements are present come back populated, the rest stay None/empty)."""
    text = xml_text.strip()
    if text.startswith("<?xml"):
        # drop the declaration line before handing to ElementTree -- stdlib's
        # parser is fine with it, but keeping this explicit documents that the
        # exact declaration text is not itself meaningful to us
        text = text.split("?>", 1)[1].strip()
    try:
        root = ElementTree.fromstring(text)
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
    command_id: Optional[int]
    command_name: Optional[str]
    if _INTEGER_RE.fullmatch(command_raw):
        command_id = int(command_raw)
        command_name = commands.ID_TO_NAME.get(command_id)
    else:
        command_id = commands.COMMANDS.get(command_raw)
        command_name = command_raw if command_raw in commands.COMMANDS else None

    params_element = root.find("Params")
    params = (_parse_container(params_element)
              if params_element is not None else {})

    error_element = root.find("Error")
    error = int(error_element.text) if error_element is not None and error_element.text else None

    # See RESULT_ACCEPTED for what the number in here means, and how little of
    # that is actually known.
    result_element = root.find("Result")
    result: Any = None
    if result_element is not None:
        result = _parse_container(result_element) if len(result_element) else _coerce_wire_value(result_element.text)

    details_element = root.find("ResultDetails")
    result_details: Any = None
    if details_element is not None:
        result_details = (
            _parse_container(details_element) if len(details_element) else _coerce_wire_value(details_element.text)
        )

    return Envelope(
        esa_id=esa_id,
        command_id=command_id,
        command_name=command_name,
        command_raw=command_raw,
        params=params,
        error=error,
        result=result,
        result_details=result_details,
    )
