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

SWEEP_RANGE = (1, 10000)

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def local_ipv4() -> str:
    """Best-guess local IP by asking the OS
    which interface reaches the internet.
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
    local_ip = local_ipv4()
    return str(ipaddress.ip_network(local_ip + "/24", strict=False))


def is_tcp_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.settimeout(timeout)
    try:
        return tcp_socket.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        tcp_socket.close()


def hexdump(data: bytes, limit: int = 256) -> list[str]:
    """Classic offset / hex / ascii dump,
    capped so output stays readable.
    """
    lines = []
    data = data[:limit]
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
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


def _encode_dns_name(name: str) -> bytes:
    encoded = b""
    for label in name.split("."):
        if label:
            encoded += bytes([len(label)]) + label.encode("utf-8")
    return encoded + b"\x00"


def _decode_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    resume = offset
    guard = 0
    while offset < len(data) and guard < 128:
        guard += 1
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = struct.unpack(
                "!H", data[offset:offset + 2])[0] & 0x3FFF
            if not jumped:
                resume = offset + 2
            jumped = True
            offset = pointer
            continue
        offset += 1
        labels.append(
            data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), (resume if jumped else offset)


def _build_mdns_query(question_name: str, question_type: int = 12) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    # qclass 0x8001 = IN with the "unicast response please" bit set
    return (header + _encode_dns_name(question_name)
            + struct.pack("!HH", question_type, 0x8001))


def _parse_mdns_response(data: bytes) -> list[dict]:
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
        record_type, _rclass, _ttl, record_data_length = struct.unpack(
            "!HHIH", data[offset:offset + 10])
        offset += 10
        record_data = data[offset:offset + record_data_length]
        record = {"name": name,
                  "type": DNS_RECORD_TYPE_NAMES.get(record_type,
                                                    str(record_type))}
        if record_type == 12:  # PTR
            record["value"], _ = _decode_dns_name(data, offset)
        elif record_type == 1 and record_data_length == 4:  # A
            record["value"] = socket.inet_ntoa(record_data)
        elif record_type == 33 and record_data_length >= 6:  # SRV
            _priority, _weight, port = struct.unpack("!HHH", record_data[:6])
            target, _ = _decode_dns_name(data, offset + 6)
            record["value"] = f"{target}:{port}"
            record["port"] = port
        elif record_type == 16:  # TXT
            parts, text_position = [], 0
            while text_position < len(record_data):
                text_length = record_data[text_position]
                text_start = text_position + 1
                text_end = text_start + text_length
                text_bytes = record_data[text_start:text_end]
                parts.append(text_bytes.decode("utf-8", "replace"))
                text_position += 1 + text_length
            record["value"] = " | ".join(parts)
        else:
            record["value"] = record_data.hex()
        records.append(record)
        offset += record_data_length
    return records


def mdns_discover(wait: float = 4.0) -> list[dict]:
    """Enumerate service types,
    then ask about anything Drobo-ish we see.
    """
    queries = [
        "_services._dns-sd._udp.local",
        # Confirmed from firmware: /etc/avahi/services/nasd.service
        # advertises _nasd._tcp on port 5000. This is how Dashboard
        # finds a Drobo.
        "_nasd._tcp.local",
        "_smb._tcp.local",
        "_afpovertcp._tcp.local",
        "_device-info._tcp.local",
    ]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    unique_records: dict[tuple, dict] = {}
    try:
        for question_name in queries:
            try:
                sock.sendto(_build_mdns_query(question_name),
                            (MDNS_MULTICAST_ADDRESS, MDNS_PORT))
            except OSError as exc:
                print(f"  ! could not send mDNS query {question_name}: {exc}",
                      file=sys.stderr)
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                data, sender_address = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            for record in _parse_mdns_response(data):
                record["from"] = sender_address[0]
                key = (record["from"], record["name"], record["type"],
                       record.get("value"))
                unique_records.setdefault(key, record)
    finally:
        sock.close()
    return list(unique_records.values())


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def scan_subnet(cidr: str, ports: dict[int, str],
                workers: int = 256) -> list[dict]:
    network = ipaddress.ip_network(cidr, strict=False)
    targets = [(str(h), p) for h in network.hosts() for p in ports
               if p != 5353]
    print(f"  scanning {network} -- {len(targets)} host/port pairs ...")
    open_ports_by_host: dict[str, list[int]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(
            lambda target: (target, is_tcp_port_open(*target)), targets)
        for (host, port), is_open in results:
            if is_open:
                open_ports_by_host.setdefault(host, []).append(port)
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
                "notes": [f"{p}: {ports[p]}" for p in open_ports],
            }
        )
    return hosts


def port_sweep(host: str, first_port: int, last_port: int,
               workers: int = 512) -> list[int]:
    print(f"  sweeping {host} ports {first_port}-{last_port} ...")
    ports = range(first_port, last_port + 1)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(
            lambda port: (port, is_tcp_port_open(host, port, 0.5)), ports)
        return [port for port, is_open in results if is_open]


# --------------------------------------------------------------------------
# Fingerprinting
# --------------------------------------------------------------------------

PROBES: list[tuple[str, bytes]] = [
    ("silence", b""),
    ("crlf", b"\r\n"),
    ("http-get", b"GET / HTTP/1.0\r\nHost: drobo\r\n\r\n"),
    ("xml-ish", b"<?xml version=\"1.0\"?><request/>\r\n"),
]


def fingerprint_port(host: str, port: int, timeout: float = 2.5) -> dict:
    """Try each probe; record the first one that gets a reply."""
    result: dict = {"port": port, "attempts": []}
    for label, payload in PROBES:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if sock.connect_ex((host, port)) != 0:
                result["attempts"].append(
                    {"probe": label, "error": "connect failed"})
                continue
            if payload:
                sock.sendall(payload)
            received = b""
            try:
                while len(received) < 2048:
                    piece = sock.recv(1024)
                    if not piece:
                        break
                    received += piece
                    sock.settimeout(0.4)
            except socket.timeout:
                pass
            attempt = {"probe": label, "bytes": len(received)}
            if received:
                attempt["hexdump"] = hexdump(received)
                attempt["text"] = received[:512].decode("utf-8", "replace")
            result["attempts"].append(attempt)
            if received:
                break  # got something; no need to keep poking
        except OSError as exc:
            result["attempts"].append({"probe": label, "error": str(exc)})
        finally:
            sock.close()
    return result


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def print_mdns(records: list[dict]) -> None:
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
    print(f"\n  --- port {fingerprint['port']} ---")
    for attempt in fingerprint["attempts"]:
        if "error" in attempt:
            print(f"    probe {attempt['probe']:<9} error: {attempt['error']}")
            continue
        print(f"    probe {attempt['probe']:<9} {attempt['bytes']} bytes back")
        for line in attempt.get("hexdump", []):
            print(f"      {line}")


def main(argv: list[str] | None = None) -> int:
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
    portsweep_parser.add_argument("--lo", type=int, default=SWEEP_RANGE[0])
    portsweep_parser.add_argument("--hi", type=int, default=SWEEP_RANGE[1])

    fingerprint_parser = subparsers.add_parser(
        "fingerprint", help="see what each open port says")
    fingerprint_parser.add_argument("host")
    fingerprint_parser.add_argument(
        "--ports", default=None, help="comma-separated; default = quick list")

    report_parser = subparsers.add_parser("report", help="everything, as JSON")
    report_parser.add_argument("host")
    report_parser.add_argument("-o", "--out", default="drobo-findings.json")

    args = parser.parse_args(argv)

    if args.cmd == "mdns":
        print("Asking the network what services it advertises ...")
        print_mdns(mdns_discover(args.wait))
        return 0

    if args.cmd == "scan":
        cidr = args.cidr or default_cidr()
        print(f"Local IP looks like {local_ipv4()}")
        hosts = scan_subnet(cidr, QUICK_PORTS)
        if not hosts:
            print("  no hosts answered on any candidate port.")
        for host_entry in hosts:
            label = (f" ({host_entry['hostname']})"
                     if host_entry["hostname"] else "")
            print(f"\n  {host_entry['ip']}{label}")
            for note in host_entry["notes"]:
                print(f"      {note}")
        return 0

    if args.cmd == "portsweep":
        open_ports = port_sweep(args.host, args.lo, args.hi)
        print(f"  open: {open_ports or '(none)'}")
        return 0

    if args.cmd == "fingerprint":
        ports = (
            [int(p) for p in args.ports.split(",")]
            if args.ports
            else [p for p in QUICK_PORTS if p != 5353]
        )
        for port in ports:
            if is_tcp_port_open(args.host, port):
                print_fingerprint(fingerprint_port(args.host, port))
        return 0

    if args.cmd == "report":
        print("1/3 mDNS ...")
        mdns_records = mdns_discover()
        print("2/3 port sweep ...")
        open_ports = port_sweep(args.host, *SWEEP_RANGE)
        print(f"    open: {open_ports}")
        print("3/3 fingerprint ...")
        fingerprints = [fingerprint_port(args.host, p) for p in open_ports]
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
