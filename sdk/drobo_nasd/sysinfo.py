"""
The Drobo's own temperature and uptime -- eCmdGetSysInfo.

WHY THIS EXISTS, AND WHY WE THOUGHT IT WAS IMPOSSIBLE
-----------------------------------------------------
For a long time this project reported "this Drobo doesn't measure its
own temperature". That was wrong, and the reason is worth writing down
so nobody repeats it.

The status document the device pushes on TCP 5000 carries a per-drive
`mTemperature` field, and on a 5N it is `0` on every slot. From that we
concluded the hardware had no thermal sensor. It does -- it just isn't
in that document. The chassis temperature comes from a *command*,
`eCmdGetSysInfo`, on TCP 5001.

The second mistake was subtler. Early probes of `eCmdGetSysInfo` looked
like they succeeded about one time in six, which pointed at the RSA
challenge-response that gates the privileged commands. They were
actually succeeding far more often than that; the replies were simply
being read wrongly.

`eCmdGetConfig` puts real XML children inside `<ResultDetails>`.
`eCmdGetSysInfo` puts an **entity-escaped XML document** there instead
-- `&lt;Temperature&gt;`, not `<Temperature>`. Anything looking for a
`<Temperature>` element found nothing and called the command unanswered.
Measured properly, with the inner document unescaped and parsed a second
time, it answers **15 out of 15**.

So: this needs no key, no handshake, and no firmware change. Two
different commands on the same channel just encode their results two
different ways, and that is the thing to check first if another command
ever looks flaky.

Read-only. Sends one command, which is a query.
"""

from __future__ import annotations

import contextlib
import html
import socket
from collections.abc import Iterator
from xml.etree import ElementTree

from . import commands as _commands
from .config_cmd import (
    DEFAULT_PORT,
    TYPE_COMMAND,
    TYPE_LOGIN,
    TYPE_LOGIN_OK,
    TYPE_RESULT,
    ConfigError,
    _build_frame,
    _login_payload,
    _read_frame,
)

# Anything outside this is not a chassis temperature -- it's a parse
# that went wrong, or a field that means something else. A Drobo that
# genuinely reached 95 C would have shut itself down long before
# reporting it.
PLAUSIBLE_TEMPERATURE_RANGE_C = (1, 95)


class SysInfoError(ConfigError):
    """
    The device would not, or could not, report its system information.

    A ConfigError underneath, so a caller that already handles the
    command channel's own failures handles this one too.
    """


def _inner_document(
        details: ElementTree.Element) -> ElementTree.Element | None:
    """
    Get at the document hiding inside <ResultDetails>.

    Two shapes are possible and both are seen in practice:

      * real child elements  -- what eCmdGetConfig returns
      * escaped XML as text  -- what eCmdGetSysInfo returns

    Handle both, so this keeps working if a firmware revision changes
    its mind.
    """
    if len(details):
        return details

    text = (details.text or "").strip()
    if not text:
        return None
    # html.unescape rather than a manual replace: the device also emits
    # &quot; inside the inner XML declaration.
    unescaped = html.unescape(text).strip()
    # Drop an inner <?xml ...?> declaration; ElementTree rejects one
    # that isn't at the very start of the string it is handed.
    if unescaped.startswith("<?xml"):
        declaration_end = unescaped.find("?>")
        if declaration_end != -1:
            unescaped = unescaped[declaration_end + 2:].strip()
    if not unescaped:
        return None
    try:
        return ElementTree.fromstring(unescaped)
    except ElementTree.ParseError as exc:
        raise SysInfoError(
            f"could not read the system-info document: {exc}"
        ) from exc


def _child_int_or_none(element: ElementTree.Element | None,
                       tag: str) -> int | None:
    """
    A missing field and a field we can't read both mean 'no answer',
    not 0.

    The twin of esatm._child_int_or_none, for the same reason: a
    reading that never arrived must not look like a real 0 (a 0 C
    chassis, an idle array). This one also accepts a None element,
    because the inner document can be missing altogether.
    """
    if element is None:
        return None
    child = element.find(tag)
    if child is None or not (child.text or "").strip():
        return None
    try:
        return int((child.text or "").strip())
    except ValueError:
        return None


def parse_sysinfo(payload: bytes) -> dict:
    """
    Turn an eCmdGetSysInfo result into {'temperature_c',
    'uptime_seconds'}.

    Separate from the socket work so it is testable with no device
    present. Either value may be None -- the device answering but
    omitting a field is a real case, and is not an error.
    """
    trimmed_payload = payload.rstrip(b"\x00").rstrip()
    if not trimmed_payload:
        return {"temperature_c": None, "uptime_seconds": None}
    sysinfo_element = _result_element(trimmed_payload, "SysInfo")

    temperature_c = _child_int_or_none(sysinfo_element, "Temperature")
    if temperature_c is not None and not (
            PLAUSIBLE_TEMPERATURE_RANGE_C[0] <= temperature_c
            <= PLAUSIBLE_TEMPERATURE_RANGE_C[1]):
        # Report nothing rather than something wrong. A bogus reading on
        # a dashboard is worse than a blank one.
        temperature_c = None

    uptime_seconds = _child_int_or_none(sysinfo_element, "UpTime")
    if uptime_seconds is not None and uptime_seconds < 0:
        uptime_seconds = None

    return {"temperature_c": temperature_c, "uptime_seconds": uptime_seconds}


def _result_element(trimmed_payload: bytes,
                    tag: str) -> ElementTree.Element | None:
    """
    The `tag` element of a result's inner document, or the inner
    document itself when it has no `tag` child, or None when there is
    no inner document at all.

    eCmdGetSysInfo and eCmdGetPerformance wrap their answer the same
    way, so both parsers read it through here. The device nests the
    named element inside <ResultDetails>; this tolerates it being
    either the root of the inner document or a child of it.
    """
    try:
        root = ElementTree.fromstring(trimmed_payload)
    except ElementTree.ParseError as exc:
        raise SysInfoError(f"malformed result XML: {exc}") from exc

    details = root.find("ResultDetails")
    if details is None:
        raise SysInfoError("result carried no <ResultDetails>")

    inner_document = _inner_document(details)
    if inner_document is None or inner_document.tag == tag:
        return inner_document
    # `is not None`, never `or`: an Element with no children is falsy,
    # so `or` would quietly fall back to the parent.
    nested_element = inner_document.find(tag)
    return nested_element if nested_element is not None else inner_document


def parse_performance(payload: bytes) -> dict:
    """
    Turn an eCmdGetPerformance result into throughput and IOPS counters.

    Returns {'read_mb_per_s', 'write_mb_per_s', 'iops', 'tier_iops'},
    any of which may be None if the device omitted it.

    `ReadThroughout` / `WriteThroughout` are Drobo's own spelling of
    "throughput" -- kept verbatim, like `eCmdGetLunInitatorExtraInfo`.

    UNITS: MEGABYTES per second, inferred -- not stated by the device
    -------------------------------------------------------------------------
    The device sends bare integers with no unit anywhere in the
    document. These were briefly modelled as bytes/second, which a
    measurement then disproved.

    Measured 2026-07-26,
    reading files off the array over SMB while polling:

        idle                    read 0    iops 2
        under sustained load    read 10   iops 21

    A reading of 10 cannot be bytes/second -- that would be ten bytes --
    and ten kilobytes/second is far too slow for a sustained SMB read.
    Megabytes per second is the only unit that gives a plausible figure
    for that load on this hardware, so MB/s is assumed.

    Still inferred rather than confirmed, because Drobo never documented
    it. Treated as a magnitude worth showing, not a figure to do
    arithmetic on.

    THE COUNTERS LAG, BADLY
    -------------------------------------------------------------------------
    Same measurement: the reading stayed at 0 for the first ~24 seconds
    of heavy load before rising, and was still reporting 10 a full 25
    seconds AFTER the load stopped. So this is a slow-moving average,
    not an instantaneous rate. Anything displaying it should say "recent
    activity" rather than implying live throughput, and should never be
    used to decide whether the array is busy right now.
    """
    trimmed_payload = payload.rstrip(b"\x00").rstrip()
    if not trimmed_payload:
        return {"read_mb_per_s": None, "write_mb_per_s": None,
                "iops": None, "tier_iops": None}
    performance_element = _result_element(trimmed_payload, "Performance")

    def non_negative_counter(tag: str) -> int | None:
        """
        The counter `tag`, or None when it is absent or negative: a
        negative rate is meaningless, so report nothing rather than
        nonsense.
        """
        value = _child_int_or_none(performance_element, tag)
        return None if value is None or value < 0 else value

    return {
        "read_mb_per_s": non_negative_counter("ReadThroughout"),
        "write_mb_per_s": non_negative_counter("WriteThroughout"),
        "iops": non_negative_counter("Iops"),
        "tier_iops": non_negative_counter("TierIOps"),
    }


def _command_frame(command_name: str, esa_id: str) -> bytes:
    """
    Build one command frame, resolving the id by NAME and refusing
    writes.

    By name, so a typo or an id this project never confirmed cannot
    reach the device as a number; the do-not-send check comes first so
    a write is refused even if someone passes one here.
    """
    if command_name in _commands.DO_NOT_SEND:
        raise SysInfoError(f"{command_name} is on the do-not-send list")
    command_id = _commands.COMMANDS.get(command_name)
    if command_id is None:
        raise SysInfoError(f"{command_name} has no confirmed command id")
    request_payload = (
        f"<TMCmd><CmdID>{command_id}</CmdID><Params></Params>"
        f"<ESAID>{esa_id}</ESAID></TMCmd>").encode() + b"\x00"
    return _build_frame(TYPE_COMMAND, request_payload)


def _send_and_read_result(sock, command_name: str, esa_id: str) -> bytes:
    """
    Send one command on an already-logged-in socket and return its
    body.

    An empty body is raised as an error rather than handed on, so "the
    device said nothing" is never mistaken for a reading.
    """
    sock.sendall(_command_frame(command_name, esa_id))
    frame_type, body = _read_frame(sock)
    if frame_type != TYPE_RESULT:
        raise SysInfoError(
            f"unexpected reply type 0x{frame_type:02x} for {command_name}"
        )
    if not body:
        raise SysInfoError(f"device returned nothing for {command_name}")
    return body


@contextlib.contextmanager
def _logged_in_connection(
        host: str, esa_id: str, port: int, timeout: float,
        error_type: type[ConfigError] = SysInfoError,
) -> Iterator[socket.socket]:
    """
    A command-channel socket with the plain login already accepted,
    closed when the block ends however it ends.

    Every read-only query on port 5001 starts the same way: connect,
    send the login frame, read one reply and check it is a login OK.
    Writing that out once keeps the frames each caller sends in the
    same order. `error_type` is the error a refused login raises, so
    each module keeps reporting it as its own.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(_build_frame(TYPE_LOGIN, _login_payload(esa_id)))
        frame_type, _ = _read_frame(sock)
        if frame_type != TYPE_LOGIN_OK:
            raise error_type(f"login refused (type 0x{frame_type:02x})")
        yield sock
    finally:
        sock.close()


def get_sysinfo(host: str, esa_id: str, port: int = DEFAULT_PORT,
                timeout: float = 8.0) -> dict:
    """
    Ask the Drobo how warm it is and how long it has been up. Read-only.

    Raises SysInfoError on anything unexpected. Callers should treat a
    failure as "unavailable right now" and carry on monitoring -- never
    as a fault.

    The agent itself polls through get_sysinfo_and_performance; this is
    the one-command form, for a caller that wants only these two
    readings.
    """
    with _logged_in_connection(host, esa_id, port, timeout) as sock:
        return parse_sysinfo(
            _send_and_read_result(sock, "eCmdGetSysInfo", esa_id))


def get_sysinfo_and_performance(host: str, esa_id: str,
                                port: int = DEFAULT_PORT,
                                timeout: float = 8.0) -> dict:
    """
    Both readings over ONE connection: temperature, uptime and
    throughput.

    Deliberately not two calls. This device gets tired: after roughly
    ninety command-port connections in an hour it stopped answering
    `eCmdGetSysInfo` entirely and took about half an hour to recover
    (docs/command-surface.md). Connection count is the resource worth
    conserving, so we log in once and ask twice -- one extra command on
    a socket we already hold is nearly free, a second socket is not.

    Performance failing does NOT lose the temperature. The sysinfo
    reading is taken first and returned even if the performance command
    then errors, since temperature is the one with an alert rule
    attached to it.
    """
    with _logged_in_connection(host, esa_id, port, timeout) as sock:
        readings = dict(parse_sysinfo(
            _send_and_read_result(sock, "eCmdGetSysInfo", esa_id)))
        readings["performance"] = None
        try:
            readings["performance"] = parse_performance(
                _send_and_read_result(sock, "eCmdGetPerformance", esa_id))
        except (SysInfoError, OSError):
            pass  # keep the temperature we already have
        return readings
