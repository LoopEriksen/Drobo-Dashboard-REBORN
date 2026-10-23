"""
Is this machine even on the same network as the Drobo?

When the Drobo stops answering, "timed out" is a true answer and a
useless one. It reads as "your NAS is broken", and very often the real
cause is that the PC has drifted onto a different Wi-Fi network -- a
phone hotspot, a guest SSID, whatever Windows decided to join
automatically. The array is usually fine.

So before anything reports "cannot reach the Drobo", it can cheaply
check the one thing that explains that message more often than a dead
NAS does: are we even on its network? A Drobo at 10.0.0.50 cannot be
reached from a PC on 10.9.9.41 by any ordinary home setup, and neither
can mDNS discovery find it -- multicast does not cross a subnet boundary
either, which is why "the device picker is empty" and "the dashboard
says unreachable" turn out to be the same problem wearing two hats.

(Addresses in this file and its tests are placeholders. Real ones are
kept out of the source tree on purpose -- see ROADMAP.md Phase 6 --
which is why the suite proves the parser against fixture text rather
than against whatever this machine happens to be plugged into today.)

WHAT THIS IS NOT: proof. Two subnets CAN be routed to each other, and
plenty of real networks do exactly that. A mismatch here does not mean
the Drobo is unreachable, and a match does not mean it is reachable.
This module only ever produces an EXPLANATION to attach to a failure
that already happened -- never a prediction, never a refusal to try.
Every message it writes says so, because a tool that confidently blames
the wrong thing is worse than one that says nothing.

Two things are measured, both locally, neither touching the network:

  local_interfaces()  every IPv4 address this machine holds, with its
                      subnet mask, read from the OS's own tool
                      (ipconfig / ip / ifconfig)

  diagnose(target)    whether `target` falls inside any of those
                      subnets, and a plain-English sentence about it if
                      it doesn't

No sockets are opened, no packets are sent, and nothing here can fail in
a way that matters: when the mask cannot be read, or the OS tool is
missing, or the platform is unfamiliar, the answer degrades to "can't
say" and the caller carries on exactly as before.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from dataclasses import dataclass

#: Assumed when a real subnet mask cannot be read. Right on essentially
#: every home network, and WRONG often enough that anything relying on
#: it says so out loud -- see Diagnosis.caveat.
DEFAULT_PREFIX_LENGTH = 24

#: How many networks a message lists before it starts summarising. A PC
#: with WSL, Hyper-V, VirtualBox and a VPN can hold six or seven, and a
#: sentence naming all of them stops being readable long before it stops
#: being accurate.
_MAX_NETWORKS_LISTED = 4

_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_CIDR_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})\b")
_HEX_NETMASK_RE = re.compile(r"\b0x([0-9a-fA-F]{8})\b")


@dataclass(frozen=True)
class Interface:
    """One IPv4 address this machine holds,
    and how wide its network is.
    """

    address: str
    prefix_length: int
    #: True when prefix_length is DEFAULT_PREFIX_LENGTH because nothing
    #: could be read, not because the OS said so. Callers must surface
    #: this rather than presenting a guess as a measurement.
    assumed: bool = False

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(
            f"{self.address}/{self.prefix_length}", strict=False
        )

    def network_contains(self, address: ipaddress.IPv4Address) -> bool:
        return address in self.network


@dataclass(frozen=True)
class Diagnosis:
    """What we can say about `target`
    from where this machine is standing.
    """

    target: str
    interfaces: tuple[Interface, ...]
    same_network: bool
    #: Set when any interface's mask was assumed rather than read.
    assumed_prefix: bool
    #: One sentence naming the problem. Always safe to show on its own.
    headline: str
    #: The addresses and what to do about them. Written to FOLLOW the
    #: headline -- it opens with "It is on ...", so a front-end that
    #: shows detail without headline leaves the reader guessing what
    #: "it" is. Show both, or show `message`.
    detail: str
    #: The honest limits of the claim above -- routed networks, guessed
    #: masks. Never empty when same_network is False; a mismatch is
    #: never stated without it.
    caveat: str

    @property
    def message(self) -> str:
        """Everything, in one string, for a caller with only one field
        to fill.
        """
        return " ".join(
            p for p in (self.headline, self.detail, self.caveat) if p
        )

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "networks": [str(i.network) for i in self.interfaces],
            "addresses": [i.address for i in self.interfaces],
            "same_network": self.same_network,
            "assumed_prefix": self.assumed_prefix,
            "headline": self.headline,
            "detail": self.detail,
            "caveat": self.caveat,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Reading this machine's own addresses
# ---------------------------------------------------------------------------


def _prefix_from_mask(dotted_mask: str) -> int | None:
    """
    Prefix length for a dotted netmask, or None if it isn't one.

    ipaddress does the real work, including rejecting non-contiguous
    masks -- which is exactly what makes this usable as a TEST rather
    than just a conversion. Given "255.255.255.0" it answers 24; given
    "192.168.1.1" it answers None, and that is how the line-by-line
    parser below tells an address apart from a mask without knowing a
    single label in a single language.

    A /0 mask is refused on purpose. Windows prints "Default Gateway . .
    : 0.0.0.0" for an adapter with no gateway, and 0.0.0.0 is a
    perfectly valid contiguous netmask -- accepting it would silently
    declare that this machine is on a network containing every address
    in existence, which would suppress every warning this module exists
    to raise.
    """
    try:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{dotted_mask}").prefixlen
    except ValueError:
        return None
    return prefix or None


def _prefix_from_hex(hex_mask: str) -> int | None:
    """BSD/macOS ifconfig writes the mask as 0xffffff00."""
    try:
        packed = int(hex_mask, 16).to_bytes(4, "big")
    except (ValueError, OverflowError):
        return None
    return _prefix_from_mask(str(ipaddress.IPv4Address(packed)))


def _is_usable_address(address: str) -> bool:
    """
    Whether an address tells us anything about where this machine is.

    Loopback is not a network anyone's Drobo is on. Link-local
    (169.254.x.x) is what a NIC gives itself when DHCP fails -- it means
    the opposite of "connected to a network", so treating it as one
    would be actively misleading. Anything unparseable is dropped rather
    than guessed at.
    """
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed_address.version != 4:
        return False
    return not (parsed_address.is_loopback or parsed_address.is_link_local
                or parsed_address.is_multicast
                or parsed_address.is_unspecified)


def parse_interfaces(text: str) -> list[Interface]:
    """
    Pull {address, prefix} pairs out of an OS network listing, whatever
    OS and whatever LANGUAGE it came from.

    Deliberately label-blind. `ipconfig` on a German Windows says
    "Subnetzmaske", on a French one "Masque de sous-reseau", and a
    parser that looks for the word "Subnet" quietly reports nothing on
    either. So this matches no labels at all -- only the shape of the
    numbers:

      same line, CIDR      "inet 192.168.1.5/24 brd ..." (ip -o -4 addr)
      same line, hex       "inet 192.168.1.5 netmask 0xffffff00"
                           (macOS/BSD)
      same line, dotted    "inet 192.168.1.5 netmask 255.255.255.0"
      next line, dotted    "IPv4 Address . : 192.168.1.5" (Windows)
                           "Subnet Mask  . : 255.255.255.0"

    An address with no mask ANYWHERE is discarded, not kept with a
    guess. That rule is what makes the two-line Windows form safe:
    "Default Gateway . : 192.168.1.1" and "DNS Servers . : 8.8.8.8" are
    also lone IPv4 addresses on their own lines, and neither is followed
    by a mask, so both fall out without this needing to recognise the
    words "gateway" or "DNS" in any language. Recording a DNS server's
    address as one of ours would have claimed we're on 8.8.8.0/24 -- a
    network we are emphatically not on.

    Callers who end up with nothing should fall back to
    local_interfaces()' routing probe, which is honest about guessing.
    """
    interfaces: list[Interface] = []
    pending_address: str | None = None  # an address still waiting for its mask

    for line in text.splitlines():
        cidr_match = _CIDR_RE.search(line)
        if cidr_match:
            address, prefix = cidr_match.group(1), int(cidr_match.group(2))
            pending_address = None
            if _is_usable_address(address) and 0 < prefix <= 32:
                interfaces.append(Interface(address, prefix))
            continue

        ipv4_tokens = _IPV4_RE.findall(line)
        hex_mask_match = _HEX_NETMASK_RE.search(line)

        if not ipv4_tokens:
            if hex_mask_match and pending_address:
                prefix = _prefix_from_hex(hex_mask_match.group(1))
                if prefix:
                    interfaces.append(Interface(pending_address, prefix))
                pending_address = None
            continue

        # Split this line's IPv4-shaped tokens into "could be a mask"
        # and "must be an address". A host address is never a contiguous
        # netmask, so this classification is exact rather than
        # heuristic.
        addresses = [t for t in ipv4_tokens if _prefix_from_mask(t) is None]
        masks = [p for p in (_prefix_from_mask(t) for t in ipv4_tokens) if p]

        if addresses:
            # A new address supersedes any earlier one still waiting:
            # the previous line's lone address never got a mask, so it
            # was a gateway, a DNS server, or something else that isn't
            # ours.
            line_address = addresses[0]
            prefix = masks[0] if masks else (
                _prefix_from_hex(hex_mask_match.group(1))
                if hex_mask_match else None)
            if prefix:
                pending_address = None
                if _is_usable_address(line_address):
                    interfaces.append(Interface(line_address, prefix))
            else:
                pending_address = (
                    line_address if _is_usable_address(line_address)
                    else None)
            continue

        if masks and pending_address:
            interfaces.append(Interface(pending_address, masks[0]))
            pending_address = None

    seen: set[tuple[str, int]] = set()
    unique_interfaces: list[Interface] = []
    for interface in interfaces:
        key = (interface.address, interface.prefix_length)
        if key not in seen:
            seen.add(key)
            unique_interfaces.append(interface)
    return unique_interfaces


def _read_interface_text(timeout: float = 3.0) -> str | None:
    """
    Ask the OS for its network listing. Returns None -- never raises --
    on any failure, exactly like discovery._read_arp_table_text, and for
    the same reason: a missing binary or an unfamiliar platform must
    never be able to make the agent unusable. Not knowing where we are
    is a normal outcome here, not an error.
    """
    if sys.platform.startswith("win"):
        listing_commands = [["ipconfig"]]
    else:
        listing_commands = [
            ["ip", "-o", "-4", "addr", "show"],
            ["ifconfig"],
            ["ifconfig", "-a"],
        ]
    for listing_command in listing_commands:
        try:
            result = subprocess.run(
                listing_command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return None


def _routing_probe() -> str:
    """
    The address this machine would send from, per its own routing table.

    A UDP "connect" to a public address sends no packet -- it only asks
    the OS which local address it would use -- so this costs nothing and
    works with no network at all. Returns "" rather than a loopback
    address on failure: 127. 0.0.1 would be a confident, wrong answer
    about where this machine lives.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 53))
        address = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return address if _is_usable_address(address) else ""


def local_interfaces(timeout: float = 3.0) -> list[Interface]:
    """
    Every IPv4 network this machine is on, best effort.

    Falls back to the routing probe with an ASSUMED /24 when the OS
    listing can't be read or yields nothing -- flagged assumed=True, so
    a caller can tell "your PC is on 10.9.9.0/24" (measured) from "your
    PC is on 10.9.9.41 and /24 is the usual mask" (a guess that happens
    to be right almost everywhere). Returns [] when even that fails,
    which callers must treat as "no opinion" rather than "no networks".
    """
    text = _read_interface_text(timeout)
    if text:
        interfaces = parse_interfaces(text)
        if interfaces:
            return interfaces
    address = _routing_probe()
    if not address:
        return []
    return [Interface(address, DEFAULT_PREFIX_LENGTH, assumed=True)]


# ---------------------------------------------------------------------------
# The diagnosis
# ---------------------------------------------------------------------------


def _join_with_and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _describe_networks(interfaces: tuple[Interface, ...]) -> str:
    network_names = [str(i.network) for i in interfaces]
    if len(network_names) <= _MAX_NETWORKS_LISTED:
        return _join_with_and(network_names)
    unlisted_count = len(network_names) - _MAX_NETWORKS_LISTED
    return (
        _join_with_and(network_names[:_MAX_NETWORKS_LISTED])
        + f" (and {unlisted_count} other"
        f"{'s' if unlisted_count > 1 else ''})"
    )


def diagnose(
    target: str, interfaces: list[Interface] | None = None
) -> Diagnosis | None:
    """
    Whether `target` is on a network this machine is also on.

    Returns None -- meaning "no opinion, say nothing" -- when the
    question can't be answered honestly: a target that isn't an IPv4
    literal (a hostname, "auto", an empty string), or a machine whose
    own addresses couldn't be determined. Callers should treat None as
    "add nothing to the error you were already going to show".

    `interfaces` is injectable so this is testable against fixed inputs
    without a network, a platform, or a subprocess.
    """
    try:
        target_address = ipaddress.ip_address(target.strip())
    except (ValueError, AttributeError):
        return None
    if target_address.version != 4:
        return None

    known_interfaces = tuple(
        interfaces if interfaces is not None else local_interfaces())
    if not known_interfaces:
        return None

    on_same_network = any(interface.network_contains(target_address)
                          for interface in known_interfaces)
    any_mask_assumed = any(interface.assumed for interface in known_interfaces)
    networks_description = _describe_networks(known_interfaces)

    if on_same_network:
        # Nothing useful to add: the Drobo is on our network and still
        # isn't answering, so the network layout is not the story.
        # Returned rather than suppressed so a caller can distinguish
        # "checked, fine" from "couldn't check" -- but the text stays
        # empty so nobody displays it.
        return Diagnosis(
            target=str(target_address), interfaces=known_interfaces,
            same_network=True, assumed_prefix=any_mask_assumed,
            headline="", detail="", caveat="",
        )

    headline = "This PC is on a different network from the Drobo."
    detail = (
        f"It is on {networks_description}, while the Drobo was last seen "
        f"at {target_address}. Reconnect this PC to the Drobo's network "
        f"and it should come straight back -- nothing needs changing "
        f"on the Drobo itself."
    )
    caveat = (
        "Worth knowing: separate networks can be routed together, so this "
        "is the likely explanation rather than a certainty."
    )
    if any_mask_assumed:
        caveat += (
            " This PC's subnet mask could not be read, so the usual /24 was "
            "assumed when working that out."
        )
    return Diagnosis(
        target=str(target_address), interfaces=known_interfaces,
        same_network=False, assumed_prefix=any_mask_assumed,
        headline=headline, detail=detail, caveat=caveat,
    )


def explain(target: str, interfaces: list[Interface] | None = None) -> str:
    """
    One sentence to append to an "unreachable" message, or "" if there
    is nothing worth saying. The convenience form of diagnose() for
    callers that only have room for a string.
    """
    diagnosis = diagnose(target, interfaces)
    return (diagnosis.message
            if diagnosis and not diagnosis.same_network else "")


def _main(argv: list[str] | None = None) -> int:
    """
    Answer the question from a terminal, with no agent running:

        py -m drobo_nasd.netcheck 10.0.0.50

    Exists so the capture scripts (and anyone debugging at 1am) can ask
    it directly. Exit status is the answer: 0 same network, 1 different,
    2 no opinion -- so a shell script can branch on it without parsing
    text.
    """
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("usage: py -m drobo_nasd.netcheck <ip-address>")
        return 2

    interfaces = local_interfaces()
    print("This PC:")
    for interface in interfaces:
        print(f"  {interface.address}  on  {interface.network}"
              + ("   (mask assumed, not read)" if interface.assumed else ""))
    if not interfaces:
        print("  (could not read this machine's addresses)")

    diagnosis = diagnose(args[0], interfaces)
    print()
    if diagnosis is None:
        print(f"No opinion about {args[0]!r} -- it is not an IPv4 "
              f"address, or this machine's own addresses could not be read.")
        return 2
    if diagnosis.same_network:
        print(f"{diagnosis.target} is on this PC's network. If it still "
              f"isn't answering, the network layout is not the reason.")
        return 0
    print(diagnosis.message)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
