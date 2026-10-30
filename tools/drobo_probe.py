#!/usr/bin/env python3
"""
drobo_probe.py - Phase 1 discovery tool for Drobo Dashboard REBORN.

Finds a Drobo on the local network and reports what it is listening on,
so we can work out how the original Drobo Dashboard talks to it. Nothing
here writes to the Drobo -- it only opens connections and reads what
comes back.

Standard library only, Python 3.9+. No admin rights needed.

Usage:
    py drobo_probe.py mdns
        Ask the network to list every advertised service (Bonjour/mDNS).
        This is how Drobo Dashboard finds a Drobo, so the Drobo should
        answer.

    py drobo_probe.py scan
    py drobo_probe.py scan --cidr 10.0.0.0/24
        Sweep the local subnet for hosts that look like a NAS.

    py drobo_probe.py portsweep 10.0.0.50
        Every port from 1-10000 on one host. Run this once you know the
        IP.

    py drobo_probe.py fingerprint 10.0.0.50
        Connect to each open port and record whatever it says.

    py drobo_probe.py report 10.0.0.50 -o findings.json
        mdns + portsweep + fingerprint, written out as JSON.

NOTE ON PORT NUMBERS: the "likely Drobo" ports below are candidates, not
confirmed facts. Confirming which port Dashboard actually uses is the
whole point of this tool -- see
docs/archive/phase-1-capture-playbook.md.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# --------------------------------------------------------------------------
# Candidate ports. Stage-1 scan only, to find the box quickly.
# --------------------------------------------------------------------------
QUICK_PORTS = {
    21: "FTP",
    22: "SSH (dropbear, usually only if a DroboApp enabled it)",
    23: "Telnet",
    80: "HTTP",
    111: "rpcbind / NFS",
    139: "NetBIOS session (SMB1)",
    443: "HTTPS",
    445: "SMB (file sharing)",
    548: "AFP (Apple filing)",
    631: "IPP printing",
    873: "rsync",
    2049: "NFS",
    3260: "iSCSI (business/SAN models)",
    5000: "nasd - the Drobo management protocol (CONFIRMED from firmware)",
    5001: "candidate: Drobo management (unconfirmed)",
    5353: "mDNS (UDP - not tested by TCP scan)",
    8080: "HTTP alt / DroboApp web UI",
    8081: "HTTP alt / DroboApp web UI",
    8443: "HTTPS alt / DroboApp web UI",
    9000: "DroboApp range",
}

SWEEP_PORT_RANGE = (1, 10000)

# How long a TCP connect may take before the port counts as closed. The
# subnet scan uses the shorter one, because it makes many more
# attempts; both are ample on a LAN.
SCAN_CONNECT_TIMEOUT_SECONDS = 0.35
PORT_SWEEP_CONNECT_TIMEOUT_SECONDS = 0.5

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def local_ipv4() -> str:
    """Best-guess local IP by asking the OS
    which interface reaches the internet.

    Connecting a UDP socket sends no packet; it only makes the OS pick
    a route, whose source address is the one wanted.
    """
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp_socket.connect(("8.8.8.8", 53))
        return udp_socket.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        udp_socket.close()


def default_cidr() -> str:
    """The /24 around this machine's address.

    A home network is almost always a /24, and guessing one keeps the
    scan short when no --cidr is given.
    """
    local_ip = local_ipv4()
    return str(ipaddress.ip_network(local_ip + "/24", strict=False))


def is_tcp_port_open(host: str, port: int,
                     timeout_seconds: float = SCAN_CONNECT_TIMEOUT_SECONDS
                     ) -> bool:
    """Whether a TCP connect to host:port succeeds.

    connect_ex and a short timeout keep a sweep of thousands of ports
    fast on a LAN: a refused or silent port costs at most the timeout,
    never an exception.
    """
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.settimeout(timeout_seconds)
    try:
        return tcp_socket.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        tcp_socket.close()


def hexdump(data: bytes, max_bytes: int = 256) -> list[str]:
    """Classic offset / hex / ascii dump,
    capped so output stays readable.
    """
    lines = []
    data = data[:max_bytes]
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk).ljust(47)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "."
                             for byte in chunk)
        lines.append(f"{offset:04x}  {hex_part}  {ascii_part}")
    return lines


# --------------------------------------------------------------------------
# Minimal mDNS (Bonjour) client
#
# We send one query to the multicast address asking for the special
# "_services._dns-sd._udp.local" record, which means "list every service
# type you advertise". We set the QU bit so responders reply straight
# back to us instead of multicasting -- that avoids fighting with
# Apple's Bonjour service for UDP port 5353 on Windows.
# --------------------------------------------------------------------------

MDNS_MULTICAST_ADDRESS = "224.0.0.251"
MDNS_PORT = 5353
DNS_RECORD_TYPE_NAMES = {1: "A", 12: "PTR", 16: "TXT", 28: "AAAA", 33: "SRV"}
DNS_TYPE_A = 1
DNS_TYPE_PTR = 12
DNS_TYPE_TXT = 16
DNS_TYPE_SRV = 33

# A name can point at another through a compression pointer, so a
# hostile or broken packet can make a loop; this caps the hops.
MAX_LABEL_HOPS = 128

# The service types asked about, in order. The first lists every type
# the network advertises; the rest are asked directly in case a
# responder ignores the enumeration.
MDNS_QUERY_NAMES = (
    "_services._dns-sd._udp.local",
    # Confirmed from firmware: /etc/avahi/services/nasd.service
    # advertises _nasd._tcp on port 5000. This is how Dashboard
    # finds a Drobo.
    "_nasd._tcp.local",
    "_smb._tcp.local",
    "_afpovertcp._tcp.local",
    "_device-info._tcp.local",
)

# Larger than any mDNS reply on an ordinary network (jumbo frames
# included), so no reply is cut short.
MDNS_RECEIVE_BUFFER_BYTES = 9000
# How often the reply loop wakes to check its deadline.
MDNS_RECEIVE_POLL_SECONDS = 0.5


def _encode_dns_name(name: str) -> bytes:
    """`name` as DNS length-prefixed labels; empty labels (a trailing
    dot) are dropped rather than ending the name early."""
    encoded = b""
    for label in name.split("."):
        if label:
            encoded += bytes([len(label)]) + label.encode("utf-8")
    return encoded + b"\x00"


def _decode_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    """The name at `offset`, and the offset just past it.

    Compression pointers are followed, but the returned offset is the
    one after the FIRST pointer, because that is where the record
    continues. The hop limit stops a pointer loop from spinning
    forever.
    """
    labels: list[str] = []
    resume_offset = None  # set when the first pointer is followed
    label_hop_count = 0
    while offset < len(data) and label_hop_count < MAX_LABEL_HOPS:
        label_hop_count += 1
        label_length = data[offset]
        if label_length == 0:
            offset += 1
            break
        is_pointer = label_length & 0xC0 == 0xC0
        if is_pointer and offset + 1 >= len(data):
            break  # a pointer cut off by the end of the packet
        if is_pointer:
            pointer = struct.unpack(
                "!H", data[offset:offset + 2])[0] & 0x3FFF
            # Only the first pointer decides where the record goes on.
            resume_offset = (offset + 2 if resume_offset is None
                             else resume_offset)
            offset = pointer
            continue
        offset += 1
        labels.append(
            data[offset:offset + label_length].decode("utf-8", "replace"))
        offset += label_length
    return ".".join(labels), (
        resume_offset if resume_offset is not None else offset)


def _build_mdns_query(question_name: str,
                      question_type: int = DNS_TYPE_PTR) -> bytes:
    """One mDNS question for `question_name`.

    The QU ("unicast response please") bit is set so replies come
    straight back to this socket, which need not own UDP 5353.
    """
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    # qclass 0x8001 = IN with the "unicast response please" bit set
    return (header + _encode_dns_name(question_name)
            + struct.pack("!HH", question_type, 0x8001))


def _decode_txt(record_data: bytes) -> str:
    """A TXT record's length-prefixed strings, joined with " | "."""
    parts, text_position = [], 0
    while text_position < len(record_data):
        text_length = record_data[text_position]
        text_start = text_position + 1
        text_end = text_start + text_length
        text_bytes = record_data[text_start:text_end]
        parts.append(text_bytes.decode("utf-8", "replace"))
        text_position += 1 + text_length
    return " | ".join(parts)


def _decode_record_value(record_type: int, data: bytes, offset: int,
                         record_data: bytes,
                         record_data_length: int) -> dict:
    """The printable fields of one resource record: always "value",
    plus "port" for SRV.

    Names inside PTR and SRV data are decoded from the whole packet
    (`data` at `offset`), because they may use compression pointers
    into it. A type this tool does not decode is shown as hex. The
    size checks use the length the record declares, not what the
    packet still holds.
    """
    if record_type == DNS_TYPE_PTR:
        value, _ = _decode_dns_name(data, offset)
        return {"value": value}
    if record_type == DNS_TYPE_A and record_data_length == 4:
        return {"value": socket.inet_ntoa(record_data)}
    if record_type == DNS_TYPE_SRV and record_data_length >= 6:
        _priority, _weight, port = struct.unpack("!HHH", record_data[:6])
        target, _ = _decode_dns_name(data, offset + 6)
        return {"value": f"{target}:{port}", "port": port}
    if record_type == DNS_TYPE_TXT:
        return {"value": _decode_txt(record_data)}
    return {"value": record_data.hex()}


def _parse_mdns_response(data: bytes) -> list[dict]:
    """Every answer, authority and additional record in one mDNS
    packet, as {"name", "type", "value"} (plus "port" for SRV).

    A truncated packet yields the records read so far rather than an
    error, because one malformed responder should not hide the rest.
    """
    records: list[dict] = []
    if len(data) < 12:
        return records
    (_, _, question_count, answer_count, authority_count,
     additional_count) = struct.unpack("!HHHHHH", data[:12])
    offset = 12
    for _ in range(question_count):
        _, offset = _decode_dns_name(data, offset)
        offset += 4
    for _ in range(answer_count + authority_count + additional_count):
        if offset >= len(data):
            break
        name, offset = _decode_dns_name(data, offset)
        if offset + 10 > len(data):
            break
        record_type, _record_class, _ttl_seconds, record_data_length = (
            struct.unpack("!HHIH", data[offset:offset + 10]))
        offset += 10
        record_data = data[offset:offset + record_data_length]
        record = {"name": name,
                  "type": DNS_RECORD_TYPE_NAMES.get(record_type,
                                                    str(record_type))}
        record.update(_decode_record_value(
            record_type, data, offset, record_data, record_data_length))
        records.append(record)
        offset += record_data_length
    return records


def _send_mdns_queries(mdns_socket: socket.socket) -> None:
    """Send every question in MDNS_QUERY_NAMES. A query that cannot be
    sent is reported and skipped, so one failure does not stop the
    rest."""
    for question_name in MDNS_QUERY_NAMES:
        try:
            mdns_socket.sendto(_build_mdns_query(question_name),
                               (MDNS_MULTICAST_ADDRESS, MDNS_PORT))
        except OSError as exc:
            print(f"  ! could not send mDNS query {question_name}: {exc}",
                  file=sys.stderr)


def _collect_mdns_replies(mdns_socket: socket.socket,
                          wait_seconds: float) -> dict[tuple, dict]:
    """Every distinct record received within `wait_seconds`, keyed by
    (sender, name, type, value) so a responder answering twice is
    listed once."""
    unique_records: dict[tuple, dict] = {}
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        try:
            data, sender_address = mdns_socket.recvfrom(
                MDNS_RECEIVE_BUFFER_BYTES)
        except socket.timeout:
            continue
        except OSError:
            break
        for record in _parse_mdns_response(data):
            record["from"] = sender_address[0]
            record_key = (record["from"], record["name"], record["type"],
                          record.get("value"))
            unique_records.setdefault(record_key, record)
    return unique_records


def mdns_discover(wait_seconds: float = 4.0) -> list[dict]:
    """Enumerate service types,
    then ask about anything Drobo-ish we see.

    This is how Drobo Dashboard finds a Drobo, so it is the first thing
    to try. The QU bit (see _build_mdns_query) means this socket need
    not bind UDP 5353, which Apple's Bonjour service holds on Windows.
    """
    mdns_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mdns_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    mdns_socket.settimeout(MDNS_RECEIVE_POLL_SECONDS)
    try:
        _send_mdns_queries(mdns_socket)
        unique_records = _collect_mdns_replies(mdns_socket, wait_seconds)
    finally:
        mdns_socket.close()
    return list(unique_records.values())


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def _open_ports_by_host(targets: list[tuple[str, int]],
                        worker_count: int) -> dict[str, list[int]]:
    """Connect to every (host, port) in parallel; the open ports, per
    host."""
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = pool.map(
            lambda target: (target, is_tcp_port_open(*target)), targets)
        open_targets = [target for target, is_open in results if is_open]
    open_ports_by_host: dict[str, list[int]] = {}
    for host, port in open_targets:
        open_ports_by_host.setdefault(host, []).append(port)
    return open_ports_by_host


def scan_subnet(cidr: str, ports: dict[int, str],
                worker_count: int = 256) -> list[dict]:
    """Every host in `cidr` with at least one of `ports` open, in
    address order, with its reverse-DNS name and a note per port.

    The mDNS port is skipped because it is UDP, and a TCP connect says
    nothing about it.
    """
    network = ipaddress.ip_network(cidr, strict=False)
    targets = [(str(host), port) for host in network.hosts()
               for port in ports if port != MDNS_PORT]
    print(f"  scanning {network} -- {len(targets)} host/port pairs ...")
    open_ports_by_host = _open_ports_by_host(targets, worker_count)
    hosts = []
    for host, open_ports in sorted(
            open_ports_by_host.items(),
            key=lambda host_and_ports: ipaddress.ip_address(
                host_and_ports[0])):
        open_ports.sort()
        try:
            hostname = socket.gethostbyaddr(host)[0]
        except OSError:
            hostname = ""
        hosts.append(
            {
                "ip": host,
                "hostname": hostname,
                "open_ports": open_ports,
                "notes": [f"{port}: {ports[port]}" for port in open_ports],
            }
        )
    return hosts


def port_sweep(host: str, first_port: int, last_port: int,
               worker_count: int = 512) -> list[int]:
    """The open TCP ports from `first_port` to `last_port` inclusive,
    in order.

    Run once the Drobo's address is known, to find services the quick
    candidate list does not name.
    """
    print(f"  sweeping {host} ports {first_port}-{last_port} ...")
    ports = range(first_port, last_port + 1)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = pool.map(
            lambda port: (port, is_tcp_port_open(
                host, port, PORT_SWEEP_CONNECT_TIMEOUT_SECONDS)),
            ports)
        return [port for port, is_open in results if is_open]


# --------------------------------------------------------------------------
# Fingerprinting
# --------------------------------------------------------------------------

FINGERPRINT_PROBES: list[tuple[str, bytes]] = [
    ("silence", b""),
    ("crlf", b"\r\n"),
    ("http-get", b"GET / HTTP/1.0\r\nHost: drobo\r\n\r\n"),
    ("xml-ish", b"<?xml version=\"1.0\"?><request/>\r\n"),
]

# Enough of a reply to recognise the service; reading more only slows
# the run down.
MAX_REPLY_BYTES = 2048
REPLY_CHUNK_BYTES = 1024
# Once a reply has started, how long a pause ends it.
REPLY_FOLLOW_UP_TIMEOUT_SECONDS = 0.4
# How much of a reply is kept as text in the report.
REPLY_TEXT_BYTES = 512


def _read_reply(tcp_socket: socket.socket) -> bytes:
    """Whatever the peer sends, up to MAX_REPLY_BYTES.

    The first read waits the full connect timeout; after that a short
    pause ends the reply, because a service that has said its piece
    usually goes quiet rather than closing.
    """
    received_bytes = b""
    while len(received_bytes) < MAX_REPLY_BYTES:
        try:
            chunk = tcp_socket.recv(REPLY_CHUNK_BYTES)
        except socket.timeout:
            break
        if not chunk:
            break
        received_bytes += chunk
        tcp_socket.settimeout(REPLY_FOLLOW_UP_TIMEOUT_SECONDS)
    return received_bytes


def _try_probe(host: str, port: int, probe_name: str, probe_bytes: bytes,
               timeout_seconds: float) -> dict:
    """Connect, send one probe (nothing for "silence"), and record what
    came back, or the error."""
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.settimeout(timeout_seconds)
    try:
        if tcp_socket.connect_ex((host, port)) != 0:
            return {"probe": probe_name, "error": "connect failed"}
        if probe_bytes:
            tcp_socket.sendall(probe_bytes)
        received_bytes = _read_reply(tcp_socket)
        attempt = {"probe": probe_name, "bytes": len(received_bytes)}
        if received_bytes:
            attempt["hexdump"] = hexdump(received_bytes)
            attempt["text"] = received_bytes[:REPLY_TEXT_BYTES].decode(
                "utf-8", "replace")
        return attempt
    except OSError as exc:
        return {"probe": probe_name, "error": str(exc)}
    finally:
        tcp_socket.close()


def fingerprint_port(host: str, port: int,
                     timeout_seconds: float = 2.5) -> dict:
    """Try each probe; record the first one that gets a reply.

    Silence comes first because many services greet a client
    unprompted (nasd's status port among them), and saying nothing
    is the least likely way to upset an unknown service.
    """
    result: dict = {"port": port, "attempts": []}
    for probe_name, probe_bytes in FINGERPRINT_PROBES:
        attempt = _try_probe(host, port, probe_name, probe_bytes,
                             timeout_seconds)
        result["attempts"].append(attempt)
        if attempt.get("bytes"):
            break  # got something; no need to keep poking
    return result


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def print_mdns(records: list[dict]) -> None:
    """The mDNS records grouped by the host that sent them, so one
    Drobo's services read together."""
    if not records:
        print("  (no mDNS replies -- "
              "see the troubleshooting notes in the playbook)")
        return
    by_host: dict[str, list[dict]] = {}
    for record in records:
        by_host.setdefault(record["from"], []).append(record)
    for host, host_records in sorted(by_host.items()):
        print(f"\n  from {host}")
        for record in sorted(host_records,
                             key=lambda entry: (entry["type"], entry["name"])):
            print(f"    {record['type']:<5} {record['name']}")
            print(f"          -> {record.get('value', '')}")


def print_fingerprint(fingerprint: dict) -> None:
    """Each probe of one port, with a hex dump of any reply."""
    print(f"\n  --- port {fingerprint['port']} ---")
    for attempt in fingerprint["attempts"]:
        if "error" in attempt:
            print(f"    probe {attempt['probe']:<9} error: {attempt['error']}")
            continue
        print(f"    probe {attempt['probe']:<9} {attempt['bytes']} bytes back")
        for line in attempt.get("hexdump", []):
            print(f"      {line}")


def _build_parser() -> argparse.ArgumentParser:
    """The command line: one sub-command per job, under the dest
    "cmd"."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    mdns_parser = subparsers.add_parser(
        "mdns", help="list advertised network services")
    mdns_parser.add_argument("--wait", type=float, default=4.0)

    scan_parser = subparsers.add_parser(
        "scan", help="sweep the subnet for likely NAS hosts")
    scan_parser.add_argument("--cidr", default=None)

    portsweep_parser = subparsers.add_parser(
        "portsweep", help="all ports on one host")
    portsweep_parser.add_argument("host")
    portsweep_parser.add_argument("--lo", type=int,
                                  default=SWEEP_PORT_RANGE[0])
    portsweep_parser.add_argument("--hi", type=int,
                                  default=SWEEP_PORT_RANGE[1])

    fingerprint_parser = subparsers.add_parser(
        "fingerprint", help="see what each open port says")
    fingerprint_parser.add_argument("host")
    fingerprint_parser.add_argument(
        "--ports", default=None, help="comma-separated; default = quick list")

    report_parser = subparsers.add_parser("report", help="everything, as JSON")
    report_parser.add_argument("host")
    report_parser.add_argument("-o", "--out", default="drobo-findings.json")
    return parser


def _run_mdns(args: argparse.Namespace) -> int:
    """The mdns sub-command."""
    print("Asking the network what services it advertises ...")
    print_mdns(mdns_discover(args.wait))
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    """The scan sub-command: the quick candidate ports on every host of
    the subnet."""
    cidr = args.cidr or default_cidr()
    print(f"Local IP looks like {local_ipv4()}")
    hosts = scan_subnet(cidr, QUICK_PORTS)
    if not hosts:
        print("  no hosts answered on any candidate port.")
    for host_entry in hosts:
        hostname_suffix = (f" ({host_entry['hostname']})"
                           if host_entry["hostname"] else "")
        print(f"\n  {host_entry['ip']}{hostname_suffix}")
        for note in host_entry["notes"]:
            print(f"      {note}")
    return 0


def _run_portsweep(args: argparse.Namespace) -> int:
    """The portsweep sub-command."""
    open_ports = port_sweep(args.host, args.lo, args.hi)
    print(f"  open: {open_ports or '(none)'}")
    return 0


def _run_fingerprint(args: argparse.Namespace) -> int:
    """The fingerprint sub-command: probe each open port of --ports,
    or of the quick list (less the UDP mDNS port)."""
    ports = (
        [int(port) for port in args.ports.split(",")]
        if args.ports
        else [port for port in QUICK_PORTS if port != MDNS_PORT]
    )
    for port in ports:
        if is_tcp_port_open(args.host, port):
            print_fingerprint(fingerprint_port(args.host, port))
    return 0


def _run_report(args: argparse.Namespace) -> int:
    """The report sub-command: mdns, a full port sweep and a
    fingerprint of every open port, written to one JSON file."""
    print("1/3 mDNS ...")
    mdns_records = mdns_discover()
    print("2/3 port sweep ...")
    open_ports = port_sweep(args.host, *SWEEP_PORT_RANGE)
    print(f"    open: {open_ports}")
    print("3/3 fingerprint ...")
    fingerprints = [fingerprint_port(args.host, port) for port in open_ports]
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target": args.host,
        "scanner_ip": local_ipv4(),
        "mdns": mdns_records,
        "open_ports": open_ports,
        "fingerprints": fingerprints,
    }
    with open(args.out, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2)
    print(f"\nWrote {args.out}")
    return 0


_SUBCOMMAND_RUNNERS = {
    "mdns": _run_mdns,
    "scan": _run_scan,
    "portsweep": _run_portsweep,
    "fingerprint": _run_fingerprint,
    "report": _run_report,
}


def main(argv: list[str] | None = None) -> int:
    """Run one sub-command. Returns its exit code, or 1 for a
    sub-command with no runner (argparse requires one, so this is a
    safety net)."""
    args = _build_parser().parse_args(argv)
    runner = _SUBCOMMAND_RUNNERS.get(args.cmd)
    if runner is None:
        return 1
    return runner(args)


if __name__ == "__main__":
    sys.exit(main())
