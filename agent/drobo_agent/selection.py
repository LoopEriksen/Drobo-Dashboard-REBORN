"""
Choosing which Drobo the agent watches: the decisions behind
POST /api/drobo/select, kept apart from the HTTP handling.

Until 2026-11-20 nothing could change the Drobo a running agent
watched. The Windows app's "type its address" box probed the address
(/api/discover?host=), said "Found <name>", and Connect then opened a
dashboard that stayed unreachable, because the agent never used that
address; and with two Drobos on the network the picker opened whichever
one the agent already had, whichever row was clicked (readiness M2 and
S8). The route reads the address's status greeting -- the same
read-only identity check the probe makes, no login and nothing sent
that changes anything -- and only a device that answers, with the
serial asked for, is watched and kept in config.json.

Who may be chosen, and why:

  - First run (no serial pinned yet): whatever answers as a Drobo at the
    address, and its serial is pinned from then on.
  - The pinned Drobo at a new address: always. This is the typed
    address the M2 tester needed.
  - A Drobo whose serial is not the pinned one: only when the request
    names that serial in "esa_id". A picker row carries the serial the
    device itself reported, so clicking a row names it; an address typed
    with no serial is refused with both serials named, and the browser
    then offers a second, deliberate click that names it. The Windows
    app's start-up reconnect to the Drobo it remembers sends no serial,
    so an app launch alone never re-pairs the agent. This covers a
    replaced Drobo too, while the agent is running; an agent that only
    finds the stranger at startup still refuses to start, as before,
    and its message says which line of config.json to change.
  - "esa_id" given and the device reports another: refused. The
    address now belongs to a different box from the row that was
    clicked.

A switch has a consequence for every change a front end sends: the
page or window it came from may still show the Drobo watched before.
Until 2026-11-21 no write said which Drobo it was made against, so a
share deleted from a page still showing Drobo A went to Drobo B if B
had a share of that name (found by the final review of 2026-11-20,
with loopback fakes and a delete that sent nothing). Every write that
reaches a Drobo now takes an optional "esa_id", the serial of the
Drobo the change was made against, and is refused (409, nothing sent)
when the agent now watches another. Left out, the write goes ahead as
before, so a script or an older front end is not broken by it.

Nothing here touches the network or a file; api.py does both.
"""

from __future__ import annotations

from typing import NamedTuple

#: The host the simulated Drobo's picker row carries (api.py's
#: _simulated_device_entry). Selecting it is how the app opens the
#: simulator, which has no address to point at.
SIMULATED_HOST = "simulated"

#: The longest host name DNS allows. Anything longer is not an address.
MAX_HOST_LENGTH = 253

#: The longest single label (the part between dots) DNS allows.
MAX_LABEL_LENGTH = 63


class SelectRequest(NamedTuple):
    """A select request whose fields have been checked."""
    host: str
    esa_id: str


def parse_select_request(body: dict):
    """
    (SelectRequest, None) for a usable body, or (None, refusal) with a
    plain sentence for the 400.

    Strict about shape because the host goes straight into a socket
    connect and, on success, into config.json: a value with spaces or
    a path in it is a mistake to say so about, not a host to try.
    """
    host = body.get("host")
    esa_id = body.get("esa_id")
    if not isinstance(host, str) or not host.strip():
        return None, refusal(
            "bad_request",
            'Send the Drobo\'s address as "host", for example '
            '{"host": "192.0.2.10"}.')
    host = host.strip()
    if not is_address_shaped(host):
        return None, refusal(
            "bad_request",
            "That is not an address. Send an IP address or a name such "
            "as drobo.local, with nothing else in it.", host=host)
    if esa_id is not None and not isinstance(esa_id, str):
        return None, refusal(
            "bad_request",
            '"esa_id" must be the Drobo\'s serial as text, or left out.',
            host=host)
    return SelectRequest(host, (esa_id or "").strip()), None


def is_address_shaped(host: str) -> bool:
    """
    True when `host` could be an IP address or a DNS name: not too
    long, no slash or space, and no empty or over-long label (one
    trailing dot, as a fully written name has, is allowed).

    The labels are checked because the resolver refuses a name such
    as "drobo..local" with UnicodeError rather than OSError; before
    this check (2026-11-20) a typo like that ended the request with
    the connection dropped and no answer, and the app said it could
    not reach the agent.
    """
    if (len(host) > MAX_HOST_LENGTH or "/" in host
            or any(character.isspace() for character in host)):
        return False
    labels = host[:-1].split(".") if host.endswith(".") else host.split(".")
    return all(0 < len(label) <= MAX_LABEL_LENGTH for label in labels)


def refusal(reason: str, sentence: str, **details) -> dict:
    """
    An error body: the sentence a person reads, under "error" as every
    agent route has it, plus a fixed `reason` a front-end can act on
    (the browser offers a second button for "paired_with_another").
    """
    return {"error": sentence, "reason": reason, **details}


def nothing_answered(host: str) -> dict:
    """The refusal for an address where no Drobo answered."""
    return refusal(
        "not_found",
        f"Nothing at {host} answered as a Drobo, so nothing was changed. "
        f"Check the address, that the Drobo is powered on, and that this "
        f"PC is on the same network as it, with any VPN disconnected.",
        host=host)


def identity_refusal(request: SelectRequest, identity: dict,
                     pinned_esa_id: str):
    """
    (HTTP status, refusal) when the device that answered at
    request.host may not be watched, or None when it may. See the
    module docstring for the rules and why.
    """
    found_esa_id = identity.get("esa_id", "")
    device = device_entry(request.host, identity)
    if not found_esa_id:
        return 502, refusal(
            "no_serial",
            f"Something at {request.host} answered as a Drobo but gave no "
            f"serial, so the agent could not tell it apart from another "
            f"one later. Nothing was changed.",
            host=request.host, device=device)
    if request.esa_id and found_esa_id != request.esa_id:
        return 409, refusal(
            "serial_mismatch",
            f"The Drobo at {request.host} identifies as {found_esa_id!r}, "
            f"not {request.esa_id!r}, the one that was picked. Nothing "
            f"was changed. Search again and pick it from the new list.",
            host=request.host, esa_id_found=found_esa_id,
            esa_id_expected=request.esa_id, device=device)
    if pinned_esa_id and found_esa_id != pinned_esa_id and not request.esa_id:
        return 409, refusal(
            "paired_with_another",
            f"The Drobo at {request.host} identifies as {found_esa_id!r} "
            f"({device['name'] or 'no name'}), not {pinned_esa_id!r}, the "
            f"Drobo this agent is paired with. Nothing was changed. To "
            f"watch this one instead, choose it again by its serial.",
            host=request.host, esa_id_found=found_esa_id,
            esa_id_expected=pinned_esa_id, device=device)
    return None


def device_entry(host: str, identity: dict) -> dict:
    """What the device said about itself, in /api/discover's words."""
    return {
        "host": host,
        "name": identity.get("name", ""),
        "model": identity.get("model", ""),
        "firmware": identity.get("firmware", ""),
        "esa_id": identity.get("esa_id", ""),
    }


def already_watching_answer(host: str, identity: dict,
                            status: dict) -> dict:
    """
    The 200 body when the Drobo chosen is the one the agent is already
    reading, reachable: nothing saved and nothing repointed, so
    "saved_to" is empty, and "watched_at" is the address the agent
    reads it at, which can differ from `host` when mDNS found it.
    """
    device = device_entry(host, identity)
    watched_at = status.get("device", {}).get("ip", "") or host
    return {"ok": True, "already_watching": True,
            "message": (f"Already watching {device['name'] or 'the Drobo'} "
                        f"at {watched_at}. Nothing was changed."),
            "selected": device, "previous_esa_id": device["esa_id"],
            "saved_to": "", "watched_at": watched_at, "reachable": True}


def selected_answer(host: str, identity: dict, previous_esa_id: str,
                    saved_to: str, reachable: bool) -> dict:
    """
    The 200 body: what is now watched, which Drobo it replaced, where
    the choice was kept, and whether the first reading of it worked.
    """
    device = device_entry(host, identity)
    message = f"Now watching {device['name'] or 'the Drobo'} at {host}."
    if previous_esa_id and previous_esa_id != device["esa_id"]:
        message += (f" It replaces {previous_esa_id!r}, the Drobo this "
                    f"agent was paired with.")
    if not reachable:
        message += (" It did not answer the first reading just now; the "
                    "agent keeps trying.")
    return {"ok": True, "message": message, "selected": device,
            "previous_esa_id": previous_esa_id, "saved_to": saved_to,
            "reachable": reachable}


def parse_change_esa_id(body: dict):
    """
    (serial, None) for the "esa_id" a change to a Drobo may carry, ""
    when it is left out, or ("", refusal) when it is not text.

    Strict about the type for the reason parse_select_request is: a
    serial sent as a number is a front end's mistake, and reading it as
    left out would quietly switch the check below off.
    """
    esa_id = body.get("esa_id")
    if esa_id is None:
        return "", None
    if not isinstance(esa_id, str):
        return "", refusal(
            "bad_request",
            '"esa_id" must be the serial of the Drobo the change was '
            'made against, as text, or left out.')
    return esa_id.strip(), None


def another_drobo_refusal(made_against_esa_id: str, watched_esa_id: str,
                          watched_name: str):
    """
    The refusal for a change made against the Drobo
    `made_against_esa_id` while the agent watches `watched_esa_id`, or
    None when it may go ahead: the serials match, or either is unknown.

    An unknown watched serial lets it through because there is then
    nothing to mistake it for: the real driver has none only before its
    first reading, when api.py's _command_target refuses every change
    anyway, and the simulator is one Drobo.
    """
    if (not made_against_esa_id or not watched_esa_id
            or made_against_esa_id == watched_esa_id):
        return None
    return refusal(
        "another_drobo",
        f"Nothing was sent: this change was made against the Drobo "
        f"{made_against_esa_id!r}, but the agent now watches "
        f"{watched_name or 'another Drobo'} ({watched_esa_id!r}). Reload "
        f"the page, or reopen the window it came from, check which Drobo "
        f"it shows, and try again.",
        esa_id_sent=made_against_esa_id, esa_id_watched=watched_esa_id)
