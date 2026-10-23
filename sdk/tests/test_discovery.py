"""
Offline tests for finding the Drobo without a static IP
(drobo_nasd/discovery.py).

Everything here runs without a LAN or a real device: mDNS packets are
built by hand, and "the Drobo" for identity-verification tests is a
local TCP server that plays back the real captured greeting at
sdk/tests/fixtures/esatm-sample.xml (esa_id "FAKESERIALFAKES"). The one
exception -- a live end-to-end check against real hardware, at the
address in the DROBO_LIVE_HOST environment variable -- SKIPs gracefully
when no device answers, so this suite passes on a machine with no Drobo
too.

    py test_discovery.py

Run this after changing anything under drobo_nasd/discovery.py or the
identity bits of drobo_nasd/esatm.py.
"""

import contextlib
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import warnings

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import discovery, esatm  # noqa: E402

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "esatm-sample.xml")
SAMPLE_ESA_ID = "FAKESERIALFAKES"
# Overridable so no real address needs to live in this file. The default
# is a placeholder; the live check simply SKIPs unless a device answers
# there.
LIVE_HOST = os.environ.get("DROBO_LIVE_HOST", "10.0.0.50")
LIVE_PORT = 5000

failures = []


def check(label, condition, failure_detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label +
          (f"  {failure_detail}" if failure_detail and not condition else ""))
    if not condition:
        failures.append(label)


GREETING_PAYLOAD = open(FIXTURE_PATH, "rb").read()


# ---------------------------------------------------------------------------
# A tiny fake nasd: serve one DRINASD greeting on localhost, exactly
# like the real device does unprompted on connect, so
# verify_candidate/resolve() can be exercised end-to-end through the
# real socket path.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def fake_drobo(payload: bytes):
    frame = (esatm.SIGNATURE + b"\x01\x01\x00\x00"
             + struct.pack(">I", len(payload)) + payload)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    # Set here, before the thread starts, not inside serve(): the
    # with-block below can close server_socket before the thread first
    # runs, and settimeout() on a closed socket raised an uncaught
    # OSError that printed a traceback.
    server_socket.settimeout(2.0)
    host, port = server_socket.getsockname()

    def serve():
        try:
            connection, _ = server_socket.accept()
        except OSError:
            return
        try:
            connection.sendall(frame)
        except OSError:
            pass
        finally:
            connection.close()

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    try:
        yield host, port
    finally:
        server_socket.close()
        server_thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
print("\n== verify_candidate: identity match ==")
# ---------------------------------------------------------------------------

with fake_drobo(GREETING_PAYLOAD) as (host, port):
    candidate = discovery.Candidate(host, port, "test")
    verified = discovery.verify_candidate(candidate,
                                          expected_esa_id=SAMPLE_ESA_ID)
    check("match returns VerificationResult",
          isinstance(verified, discovery.VerificationResult))
    check(
        "match esa_id correct",
        verified.identity["esa_id"] == SAMPLE_ESA_ID,
        verified.identity["esa_id"],
    )
    check("match is not flagged first_run", verified.first_run is False)
    check(
        "identity carries disk_pack_id",
        verified.identity["disk_pack_id"] == "0000000000000000",
        verified.identity["disk_pack_id"],
    )
    check("identity carries name",
          verified.identity["name"] == "TestDrobo", verified.identity["name"])
    check("identity carries model",
          verified.identity["model"] == "Drobo 5N", verified.identity["model"])
    check(
        "identity carries firmware",
        verified.identity["firmware"].startswith("4.3.1"),
        verified.identity["firmware"],
    )

# ---------------------------------------------------------------------------
print("\n== verify_candidate: identity mismatch is REFUSED ==")
# ---------------------------------------------------------------------------

with fake_drobo(GREETING_PAYLOAD) as (host, port):
    candidate = discovery.Candidate(host, port, "test")
    try:
        discovery.verify_candidate(
            candidate, expected_esa_id="drb-not-the-real-one")
        check("mismatch raises IdentityMismatchError", False)
    except discovery.IdentityMismatchError as exc:
        check("mismatch raises IdentityMismatchError", True)
        check(
            "mismatch message names both ids",
            "drb-not-the-real-one" in str(exc) and SAMPLE_ESA_ID in str(exc),
            str(exc),
        )

# ---------------------------------------------------------------------------
print("\n== verify_candidate: first run, no expectation configured ==")
# ---------------------------------------------------------------------------

with fake_drobo(GREETING_PAYLOAD) as (host, port):
    candidate = discovery.Candidate(host, port, "test")
    verified = discovery.verify_candidate(candidate, expected_esa_id="")
    check("first run accepts and reports identity", verified.first_run is True)
    check(
        "first run still reports the real esa_id",
        verified.identity["esa_id"] == SAMPLE_ESA_ID,
        verified.identity["esa_id"],
    )

# ---------------------------------------------------------------------------
print("\n== resolve(): end-to-end refuse-on-mismatch (the safety property) ==")
# ---------------------------------------------------------------------------

_original_discover_mdns, _original_resolve_hostname, _original_sweep_subnet = (
    discovery.discover_mdns,
    discovery.resolve_hostname,
    discovery.sweep_subnet,
)
discovery.discover_mdns = lambda timeout=3.0: []
discovery.resolve_hostname = lambda hostname, port=esatm.DEFAULT_PORT: None
try:
    with fake_drobo(GREETING_PAYLOAD) as (host, port):
        try:
            discovery.resolve(expected_esa_id="drb-someone-elses-drobo",
                              last_known_ip=host, port=port, mdns_timeout=0.1)
            check("resolve() refuses a mismatched device end-to-end", False)
        except discovery.IdentityMismatchError:
            check("resolve() refuses a mismatched device end-to-end", True)

    print("\n== resolve(): end-to-end first-run pairing ==")
    with fake_drobo(GREETING_PAYLOAD) as (host, port):
        verified = discovery.resolve(
            expected_esa_id="", last_known_ip=host, port=port, mdns_timeout=0.1
        )
        check("resolve() accepts and reports on first run",
              verified.first_run is True)
        check(
            "resolve() found it via last-known",
            verified.candidate.source == "last-known",
            verified.candidate.source,
        )
        check(
            "resolve() reports the real esa_id",
            verified.identity["esa_id"] == SAMPLE_ESA_ID,
            verified.identity["esa_id"],
        )

    print("\n== resolve(): nothing found at all ==")
    try:
        discovery.resolve(expected_esa_id="", last_known_ip="",
                          port=59999, mdns_timeout=0.1)
        check("resolve() with no candidates raises LookupError", False)
    except discovery.IdentityMismatchError:
        check(
            "resolve() with no candidates raises LookupError, not "
            "IdentityMismatchError",
            False,
        )
    except LookupError:
        check("resolve() with no candidates raises LookupError", True)
finally:
    (
        discovery.discover_mdns,
        discovery.resolve_hostname,
        discovery.sweep_subnet,
    ) = (
        _original_discover_mdns,
        _original_resolve_hostname,
        _original_sweep_subnet,
    )


# ---------------------------------------------------------------------------
print("\n== mDNS record parsing against synthetic packets (no LAN) ==")
# ---------------------------------------------------------------------------


def _encode_dns_name(name: str) -> bytes:
    encoded_name = b""
    for label in name.split("."):
        if label:
            encoded_name += bytes([len(label)]) + label.encode("ascii")
    return encoded_name + b"\x00"


def _dns_resource_record(name: str, record_type: int,
                         record_data: bytes) -> bytes:
    return (
        _encode_dns_name(name)
        + struct.pack("!HHIH", record_type, 1, 120, len(record_data))
        + record_data
    )


srv_record_data = struct.pack("!HHH", 0, 0,
                              5000) + _encode_dns_name("MyDrobo.local")
a_record_data = socket.inet_aton("10.0.0.50")
plain_packet = (
    struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0)
    + _dns_resource_record("MyDrobo._nasd._tcp.local",
                           discovery.RECORD_TYPE_SRV, srv_record_data)
    + _dns_resource_record("MyDrobo.local", discovery.RECORD_TYPE_A,
                           a_record_data)
)
plain_records = discovery.parse_mdns_response(plain_packet)
srv_records = [r for r in plain_records if r["type"]
               == discovery.RECORD_TYPE_SRV]
a_records = [r for r in plain_records if r["type"] == discovery.RECORD_TYPE_A]
check("two records parsed", len(plain_records) == 2, plain_records)
check("SRV record extracted with port",
      srv_records and srv_records[0]["port"] == 5000, srv_records)
check("SRV record target name decoded",
      srv_records and srv_records[0]["value"] == "MyDrobo.local", srv_records)
check("A record extracted",
      a_records and a_records[0]["value"] == "10.0.0.50", a_records)

# name compression: a second record whose name is a pointer back into
# the packet points at offset 12, the first record's name
compressed_name_pointer = struct.pack("!H", 0xC000 | 12)
compressed_packet = (
    struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0)
    + _dns_resource_record("MyDrobo._nasd._tcp.local",
                           discovery.RECORD_TYPE_SRV, srv_record_data)
    + compressed_name_pointer
    + struct.pack("!HHIH", discovery.RECORD_TYPE_A, 1,
                  120, len(a_record_data))
    + a_record_data
)
compressed_records = discovery.parse_mdns_response(compressed_packet)
check(
    "compressed name pointer resolves",
    len(compressed_records) == 2
    and compressed_records[1]["name"] == "MyDrobo._nasd._tcp.local",
    compressed_records,
)

# a short/garbage packet must not raise
check("garbage packet yields no records, no crash",
      discovery.parse_mdns_response(b"\x00" * 4) == [])

# ---------------------------------------------------------------------------
print("\n== discovery ordering: mDNS, hostname, last-known, sweep (opt-in) ==")
# ---------------------------------------------------------------------------

_original_discover_mdns, _original_resolve_hostname, _original_sweep_subnet = (
    discovery.discover_mdns,
    discovery.resolve_hostname,
    discovery.sweep_subnet,
)
discovery.discover_mdns = lambda timeout=3.0: [
    discovery.Candidate("10.0.0.5", 5000, "mdns")]
discovery.resolve_hostname = lambda hostname, port=esatm.DEFAULT_PORT: (
    discovery.Candidate("10.0.0.6", port, "hostname") if hostname else None
)
discovery.sweep_subnet = lambda **kwargs: [
    discovery.Candidate("10.0.0.7", 5000, "sweep")]
try:
    candidates = discovery._gather_candidates(
        hostname="drobo.local", last_known_ip="10.0.0.9",
        try_sweep=False, mdns_timeout=0.1)
    check("sweep excluded when not opted in",
          all(c.source != "sweep" for c in candidates), candidates)
    check("order is mdns, hostname, last-known",
          [c.source for c in candidates] == ["mdns", "hostname", "last-known"],
          [c.source for c in candidates])

    candidates_with_sweep = discovery._gather_candidates(
        hostname="drobo.local", last_known_ip="10.0.0.9",
        try_sweep=True, mdns_timeout=0.1)
    check("sweep included and last when opted in",
          [c.source for c in candidates_with_sweep] == [
              "mdns", "hostname", "last-known", "sweep"],
          [c.source for c in candidates_with_sweep])

    last_known_only_candidates = discovery._gather_candidates(
        hostname="", last_known_ip="10.0.0.9",
        try_sweep=False, mdns_timeout=0.1)
    check("last-known IP is tried even with no hostname",
          any(c.host == "10.0.0.9" and c.source == "last-known"
              for c in last_known_only_candidates),
          last_known_only_candidates)

    discovery.discover_mdns = lambda timeout=3.0: [
        discovery.Candidate("10.0.0.9", 5000, "mdns")]
    deduplicated_candidates = discovery._gather_candidates(
        hostname="", last_known_ip="10.0.0.9",
        try_sweep=False, mdns_timeout=0.1)
    check(
        "duplicate host:port collapsed, higher-priority source kept",
        len(deduplicated_candidates) == 1
        and deduplicated_candidates[0].source == "mdns",
        deduplicated_candidates,
    )

    # -- addresses somebody typed, because mDNS doesn't work on their
    # network --
    discovery.discover_mdns = lambda timeout=3.0: [
        discovery.Candidate("10.0.0.5", 5000, "mdns")]
    manual_first_candidates = discovery._gather_candidates(
        hostname="drobo.local", last_known_ip="10.0.0.9",
        try_sweep=False, mdns_timeout=0.1, manual_ips=["10.0.0.77"])
    check("a typed address is tried FIRST, ahead of the multicast browse",
          [c.source for c in manual_first_candidates] == [
              "manual", "mdns", "hostname", "last-known"],
          [c.source for c in manual_first_candidates])
    # The reason for that order: on a network that filters multicast,
    # mDNS never answers, and making someone wait out a dead browse on
    # every poll when they have told us exactly where the device is
    # would be perverse.
    check("the typed address is the one that was typed",
          manual_first_candidates[0].host == "10.0.0.77",
          manual_first_candidates[0])

    blank_filtered_candidates = discovery._gather_candidates(
        try_sweep=False, mdns_timeout=0.1,
        manual_ips=["  ", "", "10.0.0.77", None])
    check(
        "blank and None entries are dropped rather than probed",
        [c.host for c in blank_filtered_candidates if c.source == "manual"]
        == ["10.0.0.77"],
        blank_filtered_candidates,
    )
    check("whitespace around a typed address is trimmed",
          all(c.host == c.host.strip() for c in blank_filtered_candidates))

    duplicate_candidates = discovery._gather_candidates(
        try_sweep=False, mdns_timeout=0.1, manual_ips=["10.0.0.5"])
    check("a typed address that mDNS also found is not probed twice",
          len([c for c in duplicate_candidates if c.host == "10.0.0.5"]) == 1,
          duplicate_candidates)
    check("-- and 'manual' wins, since it was asked for explicitly",
          [c for c in duplicate_candidates if c.host == "10.0.0.5"][0].source
          == "manual", duplicate_candidates)

    candidates_without_manual_ips = discovery._gather_candidates(
        try_sweep=False, mdns_timeout=0.1)
    check("no manual_ips changes nothing",
          all(c.source != "manual"
              for c in candidates_without_manual_ips))
finally:
    (
        discovery.discover_mdns,
        discovery.resolve_hostname,
        discovery.sweep_subnet,
    ) = (
        _original_discover_mdns,
        _original_resolve_hostname,
        _original_sweep_subnet,
    )


# ---------------------------------------------------------------------------
print("\n== sweep_subnet: time-bounded and off unless requested ==")
# ---------------------------------------------------------------------------

_sweep_started_at = time.time()
swept = discovery.sweep_subnet(
    cidr="198.51.100.0/28", port=59998, time_budget=1.0, workers=16)
_elapsed = time.time() - _sweep_started_at
check(
    "sweep of a TEST-NET-2 /28 finds nothing (nobody there)",
    swept == [],
    swept,
)
check("sweep stays roughly within its time budget", _elapsed < 6.0, _elapsed)


# ---------------------------------------------------------------------------
print(
    "\n== MAC/ARP corroboration: parsing realistic captured arp output "
    "(no LAN) =="
)
# ---------------------------------------------------------------------------

WINDOWS_ARP_A = """
Interface: 10.0.0.50 --- 0x9
  Internet Address      Physical Address      Type
  10.0.0.1            aa-bb-cc-dd-ee-01     dynamic
  10.0.0.50           e0-ab-cd-12-34-56     dynamic
  169.254.7.77          ff-ee-dd-cc-bb-aa     dynamic
  224.0.0.22             01-00-5e-00-00-16     static
"""

UNIX_ARP_N = """
Address                  HWtype  HWaddress           Flags Mask            Iface
10.0.0.1              ether   aa:bb:cc:dd:ee:01   C                     eth0
10.0.0.50             ether   e0:ab:cd:12:34:56   C                     eth0
"""  # noqa: E501

UNIX_ARP_A = """
? (10.0.0.1) at aa:bb:cc:dd:ee:01 [ether] on eth0
? (10.0.0.50) at e0:ab:cd:12:34:56 [ether] on eth0
"""

windows_arp_table = discovery.parse_arp_table(WINDOWS_ARP_A)
check("windows arp -a: real Drobo IP maps to its MAC",
      windows_arp_table.get("10.0.0.50") == "e0:ab:cd:12:34:56",
      windows_arp_table)
check("windows arp -a: dash-separated MAC normalized to colons",
      ":" in windows_arp_table.get("10.0.0.50", ""),
      windows_arp_table.get("10.0.0.50"))
check("windows arp -a: header/interface line contributes no bogus entry",
      len(windows_arp_table) == 4, windows_arp_table)

unix_arp_n_table = discovery.parse_arp_table(UNIX_ARP_N)
check("unix arp -n: real Drobo IP maps to its MAC",
      unix_arp_n_table.get("10.0.0.50") == "e0:ab:cd:12:34:56",
      unix_arp_n_table)

unix_arp_a_table = discovery.parse_arp_table(UNIX_ARP_A)
check("unix arp -a ('? (ip) at mac' form): real Drobo IP maps to its MAC",
      unix_arp_a_table.get("10.0.0.50") == "e0:ab:cd:12:34:56",
      unix_arp_a_table)

check("garbage arp text yields empty table, no crash",
      discovery.parse_arp_table("nonsense\n\n") == {})

# ---------------------------------------------------------------------------
print(
    "\n== MAC/ARP corroboration: absent/unreadable table "
    "degrades gracefully =="
)
# ---------------------------------------------------------------------------

_original_read_arp_table_text = discovery._read_arp_table_text
discovery._read_arp_table_text = lambda timeout=2.0: None
try:
    observed_mac = discovery.mac_for_ip("10.0.0.50")
    check("mac_for_ip returns None (not an exception) when arp is unreadable",
          observed_mac is None, observed_mac)
finally:
    discovery._read_arp_table_text = _original_read_arp_table_text


# _read_arp_table_text itself must swallow subprocess failure, not just
# callers of it.
_original_subprocess_run = subprocess.run
subprocess.run = lambda *args, **kwargs: (_ for _ in ()).throw(
    FileNotFoundError("no arp on PATH"))
try:
    arp_text = discovery._read_arp_table_text(timeout=0.5)
    check(
        "no 'arp' binary on PATH does not raise (Windows path degrades to "
        "None)",
        arp_text is None or isinstance(arp_text, str),
    )
finally:
    subprocess.run = _original_subprocess_run

# ---------------------------------------------------------------------------
print(
    "\n== verify_candidate: expected_mac REFUSES on mismatch, "
    "WARNS when unknown =="
)
# ---------------------------------------------------------------------------

_original_mac_for_ip = discovery.mac_for_ip

# "observed" MAC, deliberately wrong
discovery.mac_for_ip = lambda ip, timeout=2.0: "aa:bb:cc:dd:ee:ff"
try:
    with fake_drobo(GREETING_PAYLOAD) as (host, port):
        candidate = discovery.Candidate(host, port, "test")
        try:
            discovery.verify_candidate(
                candidate,
                expected_esa_id=SAMPLE_ESA_ID,
                expected_mac="11:22:33:44:55:66",
            )
            check(
                "MAC mismatch raises IdentityMismatchError even though esa_id "
                "matched",
                False,
            )
        except discovery.IdentityMismatchError as exc:
            check(
                "MAC mismatch raises IdentityMismatchError even though esa_id "
                "matched",
                True,
            )
            check(
                "MAC mismatch message names both expected and observed MAC",
                "11:22:33:44:55:66" in str(exc)
                and "aa:bb:cc:dd:ee:ff" in str(exc),
                str(exc),
            )
finally:
    discovery.mac_for_ip = _original_mac_for_ip

# ARP table had nothing for this host
discovery.mac_for_ip = lambda ip, timeout=2.0: None
try:
    with fake_drobo(GREETING_PAYLOAD) as (host, port):
        candidate = discovery.Candidate(host, port, "test")
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            verified = discovery.verify_candidate(
                candidate,
                expected_esa_id=SAMPLE_ESA_ID,
                expected_mac="11:22:33:44:55:66",
            )
            check("expected_mac set but MAC unknown does NOT refuse",
                  isinstance(verified, discovery.VerificationResult))
            check("expected_mac set but MAC unknown still confirms esa_id",
                  verified.first_run is False)
            check(
                "expected_mac set but MAC unknown emits a warning, not "
                "silence",
                any("MAC" in str(w.message) for w in caught_warnings),
                [str(w.message) for w in caught_warnings],
            )
finally:
    discovery.mac_for_ip = _original_mac_for_ip

# ---------------------------------------------------------------------------
print(
    "\n== address sanity: link-local ranks last even when higher-priority "
    "source =="
)
# ---------------------------------------------------------------------------

_original_discover_mdns, _original_resolve_hostname, _original_sweep_subnet = (
    discovery.discover_mdns,
    discovery.resolve_hostname,
    discovery.sweep_subnet,
)
# mDNS (normally highest priority) hands back the link-local address;
# the routable one only shows up as the lower-priority last-known-good
# IP.
discovery.discover_mdns = lambda timeout=3.0: [
    discovery.Candidate("169.254.7.77", 5000, "mdns")]
discovery.resolve_hostname = lambda hostname, port=esatm.DEFAULT_PORT: None
try:
    ranked_candidates = discovery._gather_candidates(
        hostname="", last_known_ip="10.0.0.50",
        try_sweep=False, mdns_timeout=0.1)
    check(
        "routable address is ranked before link-local, despite lower source "
        "priority",
        [c.host for c in ranked_candidates] == ["10.0.0.50", "169.254.7.77"],
        [c.host for c in ranked_candidates],
    )
finally:
    (
        discovery.discover_mdns,
        discovery.resolve_hostname,
        discovery.sweep_subnet,
    ) = (
        _original_discover_mdns,
        _original_resolve_hostname,
        _original_sweep_subnet,
    )

# ---------------------------------------------------------------------------
print(
    "\n== allowed_subnets: rejects an out-of-range candidate "
    "before connecting =="
)
# ---------------------------------------------------------------------------

# The two addresses must be in genuinely different /24s, or this test
# passes without testing anything. Using the documentation range
# (RFC 5737) for the out-of-range one makes that impossible to break
# by a search-and-replace.
in_range_candidate = discovery.Candidate("10.0.0.50", 5000, "test")
out_of_range_candidate = discovery.Candidate("203.0.113.5", 5000, "test")
allowed, rejected = discovery.filter_allowed_subnets(
    [in_range_candidate, out_of_range_candidate], ["10.0.0.0/24"])
check("in-range candidate kept", allowed == [in_range_candidate], allowed)
check("out-of-range candidate rejected",
      rejected == [out_of_range_candidate], rejected)

no_restriction_allowed, no_restriction_rejected = (
    discovery.filter_allowed_subnets(
        [in_range_candidate, out_of_range_candidate], None
    )
)
check(
    "no allowed_subnets configured means nothing is rejected",
    no_restriction_allowed == [in_range_candidate, out_of_range_candidate]
    and no_restriction_rejected == [],
    (no_restriction_allowed, no_restriction_rejected),
)

# end-to-end via resolve(): the fake Drobo listens on 127.0.0.1, which
# is outside the allowed range, so resolve() must refuse it WITHOUT ever
# connecting -- if it had connected it would return VerificationResult,
# not raise.
_original_discover_mdns, _original_resolve_hostname, _original_sweep_subnet = (
    discovery.discover_mdns,
    discovery.resolve_hostname,
    discovery.sweep_subnet,
)
discovery.discover_mdns = lambda timeout=3.0: []
discovery.resolve_hostname = lambda hostname, port=esatm.DEFAULT_PORT: None
try:
    with fake_drobo(GREETING_PAYLOAD) as (host, port):
        try:
            discovery.resolve(
                expected_esa_id=SAMPLE_ESA_ID,
                last_known_ip=host,
                port=port,
                mdns_timeout=0.1,
                allowed_subnets=["10.0.0.0/24"],
            )
            check(
                "resolve() refuses an out-of-subnet candidate end-to-end",
                False,
            )
        except discovery.IdentityMismatchError as exc:
            check(
                "resolve() refuses an out-of-subnet candidate end-to-end", True
            )
            check(
                "subnet-refusal message names the allowed range and the "
                "offending host",
                "10.0.0.0/24" in str(exc) and host in str(exc),
                str(exc),
            )
finally:
    (
        discovery.discover_mdns,
        discovery.resolve_hostname,
        discovery.sweep_subnet,
    ) = (
        _original_discover_mdns,
        _original_resolve_hostname,
        _original_sweep_subnet,
    )


# ---------------------------------------------------------------------------
print("\n== live device (optional) ==")
# ---------------------------------------------------------------------------

try:
    probe_socket = socket.create_connection((LIVE_HOST, LIVE_PORT),
                                            timeout=1.5)
    probe_socket.close()
    live_available = True
except OSError:
    live_available = False

if not live_available:
    print(f"  SKIP    no Drobo answered at {LIVE_HOST}:{LIVE_PORT} -- "
          "fine on a machine with no Drobo on the LAN")
else:
    # First-run pairing against the real device: pass no expected id,
    # since hardcoding this unit's serial would both fail on anyone
    # else's Drobo and bake a real device serial into the repo (see
    # ROADMAP.md Phase 6).
    candidate = discovery.Candidate(LIVE_HOST, LIVE_PORT, "config")
    verified = discovery.verify_candidate(
        candidate, expected_esa_id="", timeout=5.0)
    live_esa_id = verified.identity["esa_id"]
    check("live device returns a plausible esa_id",
          isinstance(live_esa_id, str) and len(live_esa_id) > 4, live_esa_id)
    check("live device reports a model", bool(verified.identity["model"]),
          verified.identity["model"])

    # And the safety property that actually matters, proven against real
    # hardware rather than a fake server: a wrong expectation is
    # refused.
    try:
        discovery.verify_candidate(
            candidate, expected_esa_id="drb-definitely-not-this", timeout=5.0
        )
        check("live device refuses a wrong expected esa_id", False,
              "it accepted a serial that does not match")
    except discovery.IdentityMismatchError:
        check("live device refuses a wrong expected esa_id", True)


print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
