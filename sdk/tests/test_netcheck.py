"""
Offline tests for netcheck.py -- "is this PC even on the Drobo's
network?".

Every parser case uses REAL output shapes from the three tools involved
(ipconfig, ip -o -4 addr, ifconfig), including a localised Windows
listing, because the whole design claim of parse_interfaces is that it
reads none of the labels. No subprocess is spawned and no socket is
opened: local_interfaces() is only exercised through its injectable
seam.

    py test_netcheck.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from drobo_nasd import netcheck  # noqa: E402
from drobo_nasd.netcheck import Interface  # noqa: E402

failures = []


def check(label, condition, failure_detail=""):
    print(
        ("  PASS  " if condition else "  FAIL  ")
        + label
        + (f"  {failure_detail}" if failure_detail and not condition else "")
    )
    if not condition:
        failures.append(label)


def networks_in(text):
    return [str(interface.network)
            for interface in netcheck.parse_interfaces(text)]


# --------------------------------------------------------------------------
print("\n== telling a netmask from an address ==")
# This is the whole trick the label-blind parser rests on: a host
# address is never a contiguous netmask, so the classification is exact,
# not a heuristic.
check(
    "255.255.255.0 is a /24", netcheck._prefix_from_mask("255.255.255.0") == 24
)
check("255.255.0.0 is a /16", netcheck._prefix_from_mask("255.255.0.0") == 16)
check(
    "255.255.255.128 is a /25",
    netcheck._prefix_from_mask("255.255.255.128") == 25,
)
check(
    "a host address is not a mask",
    netcheck._prefix_from_mask("10.0.0.50") is None,
)
check(
    "a gateway address is not a mask",
    netcheck._prefix_from_mask("10.9.9.1") is None,
)
check(
    "a non-contiguous mask is refused",
    netcheck._prefix_from_mask("255.0.255.0") is None,
)
# 0.0.0.0 IS a valid contiguous netmask, and Windows prints it as the
# gateway of an adapter that has none. Accepting it would declare this
# PC to be on a network containing every address there is -- silently
# disabling every warning this module exists to raise.
check(
    "0.0.0.0 is refused as a mask",
    netcheck._prefix_from_mask("0.0.0.0") is None,
)
check(
    "macOS hex mask 0xffffff00 is a /24",
    netcheck._prefix_from_hex("ffffff00") == 24,
)

# --------------------------------------------------------------------------
print("\n== Windows ipconfig: mask on the FOLLOWING line ==")
WINDOWS_IPCONFIG = """
Windows IP Configuration

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . :
   IPv4 Address. . . . . . . . . . . : 10.9.9.41
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 10.9.9.1

Ethernet adapter Ethernet 2:

   Connection-specific DNS Suffix  . :
   IPv4 Address. . . . . . . . . . . : 10.9.7.1
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . :
"""
check("both adapters found",
      networks_in(WINDOWS_IPCONFIG) == ["10.9.9.0/24", "10.9.7.0/24"],
      networks_in(WINDOWS_IPCONFIG))
# The regression that made this parser label-blind in the first place: a
# gateway and a DNS server are lone IPv4 addresses on their own lines,
# exactly like an interface address. Recording 8.8.8.8 as ours would
# have claimed this PC is on 8.8.8.0/24.
WINDOWS_IPCONFIG_WITH_DNS = """
   IPv4 Address. . . . . . . . . . . : 192.168.1.10
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.1.1
   DNS Servers . . . . . . . . . . . : 8.8.8.8
                                       1.1.1.1
   DHCP Server . . . . . . . . . . . : 192.168.1.1
"""
check(
    "a gateway is not mistaken for one of ours",
    networks_in(WINDOWS_IPCONFIG_WITH_DNS) == ["192.168.1.0/24"],
    networks_in(WINDOWS_IPCONFIG_WITH_DNS),
)
check("DNS servers are not mistaken for our networks",
      "8.8.8.0/24" not in networks_in(WINDOWS_IPCONFIG_WITH_DNS),
      networks_in(WINDOWS_IPCONFIG_WITH_DNS))

print("\n== the same listing in German, which reads no differently ==")
# The point of matching zero labels: this must work without the parser
# knowing the word "Subnetzmaske" -- or "Subnet", for that matter.
GERMAN_IPCONFIG = """
Drahtlos-LAN-Adapter WLAN:

   Verbindungsspezifisches DNS-Suffix:
   IPv4-Adresse  . . . . . . . . . . : 10.9.9.41
   Subnetzmaske  . . . . . . . . . . : 255.255.255.0
   Standardgateway . . . . . . . . . : 10.9.9.1
"""
check(
    "a localised listing parses identically",
    networks_in(GERMAN_IPCONFIG) == ["10.9.9.0/24"],
    networks_in(GERMAN_IPCONFIG),
)

# --------------------------------------------------------------------------
print("\n== Linux `ip -o -4 addr show`: CIDR on the same line ==")
LINUX_IP_ADDR_SHOW = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       "
    "valid_lft forever\n"
    "2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0\n"
    "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 "
    "scope global docker0\n"
)
check(
    "CIDR form parsed",
    networks_in(LINUX_IP_ADDR_SHOW) == ["192.168.1.0/24", "172.17.0.0/16"],
    networks_in(LINUX_IP_ADDR_SHOW),
)
check("loopback dropped", "127.0.0.0/8" not in networks_in(LINUX_IP_ADDR_SHOW))

print("\n== macOS/BSD ifconfig: hex mask on the same line ==")
BSD_IFCONFIG = """
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.33 netmask 0xffffff00 broadcast 192.168.1.255
"""
check("hex mask parsed", networks_in(BSD_IFCONFIG) == ["192.168.1.0/24"],
      networks_in(BSD_IFCONFIG))
# broadcast 192.168.1.255 sits on the same line as the address. It is
# not a valid netmask, so it is never read as one -- and the address is
# taken from the first token, not the last.
check(
    "the broadcast address is not read as an interface",
    "192.168.1.255"
    not in [
        interface.address
        for interface in netcheck.parse_interfaces(BSD_IFCONFIG)
    ],
)

DOTTED_IFCONFIG = (
    "eth0\n\tinet 10.0.0.7 netmask 255.255.255.0 broadcast 10.0.0.255\n"
)
check(
    "dotted mask on the same line parsed",
    networks_in(DOTTED_IFCONFIG) == ["10.0.0.0/24"],
    networks_in(DOTTED_IFCONFIG),
)

print("\n== addresses that say nothing are dropped ==")
APIPA_IPCONFIG = """
   Autoconfiguration IPv4 Address. . : 169.254.14.9
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
"""
# A self-assigned APIPA address means DHCP FAILED -- the opposite of "on
# a network". Treating it as one would let a disconnected PC claim it
# shares a network with something.
check("link-local (APIPA) dropped", networks_in(APIPA_IPCONFIG) == [],
      networks_in(APIPA_IPCONFIG))
check("an empty listing yields nothing", netcheck.parse_interfaces("") == [])
check("prose with no addresses yields nothing",
      netcheck.parse_interfaces("no adapters are present\n") == [])
check("an address with no mask anywhere is discarded, not guessed at",
      netcheck.parse_interfaces("   IPv4 Address. . : 192.168.1.5\n") == [])

# --------------------------------------------------------------------------
print("\n== the diagnosis: the failure this module was written for ==")
# A common cause of a timeout is Windows auto-joining a different SSID
# while the array is healthy.
WIFI_INTERFACES = [Interface("10.9.9.41", 24), Interface("10.9.7.1", 24)]
diagnosis = netcheck.diagnose("10.0.0.50", WIFI_INTERFACES)
check("a different network is spotted",
      diagnosis is not None and not diagnosis.same_network)
check("the Drobo's address is named", "10.0.0.50" in diagnosis.detail,
      diagnosis.detail)
check("this PC's networks are named",
      "10.9.9.0/24" in diagnosis.detail and "10.9.7.0/24" in diagnosis.detail,
      diagnosis.detail)
check(
    "it says the Drobo needs no changes",
    "nothing needs changing" in diagnosis.detail,
    diagnosis.detail,
)
# The claim is an explanation, never a proof: routed networks exist, and
# a tool that confidently blames the wrong thing is worse than a silent
# one.
check("a mismatch is never stated without its caveat", bool(diagnosis.caveat))
check(
    "the caveat admits routing exists",
    "routed together" in diagnosis.caveat,
    diagnosis.caveat,
)
check(
    "message carries all three parts",
    diagnosis.headline in diagnosis.message
    and diagnosis.detail in diagnosis.message
    and diagnosis.caveat in diagnosis.message,
)
check(
    "explain() returns that message",
    netcheck.explain("10.0.0.50", WIFI_INTERFACES) == diagnosis.message,
)

print("\n== and the cases where it must keep quiet ==")
SAME_NETWORK_INTERFACES = [Interface("10.0.0.40", 24)]
same_network_diagnosis = netcheck.diagnose("10.0.0.50",
                                           SAME_NETWORK_INTERFACES)
check(
    "same network -> same_network is True",
    same_network_diagnosis is not None and same_network_diagnosis.same_network,
)
# "Checked, and the network layout isn't the story" is a different
# answer from "couldn't check" -- so a Diagnosis is still returned, but
# with no text, so nothing can display it.
check(
    "same network -> nothing to display",
    same_network_diagnosis.message == "",
    repr(same_network_diagnosis.message),
)
check(
    "same network -> explain() is empty",
    netcheck.explain("10.0.0.50", SAME_NETWORK_INTERFACES) == "",
)

# A wider mask is honoured rather than second-guessed: 10.0.9.41/16
# really does contain 10.0.0.50, so there is nothing to warn about.
# Assuming /24 everywhere would have invented a mismatch here.
check(
    "a /16 that really does contain the target is respected",
    netcheck.diagnose("10.0.0.50", [Interface("10.0.9.41", 16)]).same_network,
)
check(
    "no interfaces -> no opinion", netcheck.diagnose("10.0.0.50", []) is None
)
check(
    "a hostname target -> no opinion",
    netcheck.diagnose("drobo.local", WIFI_INTERFACES) is None,
)
check("'auto' target -> no opinion",
      netcheck.diagnose("auto", WIFI_INTERFACES) is None)
check("an empty target -> no opinion",
      netcheck.diagnose("", WIFI_INTERFACES) is None)
check(
    "an IPv6 target -> no opinion",
    netcheck.diagnose("fe80::1", WIFI_INTERFACES) is None,
)

print("\n== an assumed mask is never presented as a measurement ==")
ASSUMED_MASK_INTERFACES = [
    Interface("10.9.9.41", netcheck.DEFAULT_PREFIX_LENGTH,
              assumed=True)]
assumed_mask_diagnosis = netcheck.diagnose("10.0.0.50",
                                           ASSUMED_MASK_INTERFACES)
check("assumed_prefix is flagged", assumed_mask_diagnosis.assumed_prefix)
check(
    "the guess is admitted in the caveat",
    "could not be read" in assumed_mask_diagnosis.caveat,
    assumed_mask_diagnosis.caveat,
)
measured_diagnosis = netcheck.diagnose("10.0.0.50", WIFI_INTERFACES)
check(
    "a measured mask makes no such admission",
    "could not be read" not in measured_diagnosis.caveat,
)

print("\n== many interfaces stay readable ==")
MANY_INTERFACES = [
    Interface(f"10.{n}.0.5", 24) for n in range(20, 27)
]  # none holds the target
many_interfaces_diagnosis = netcheck.diagnose("10.0.0.50", MANY_INTERFACES)
check(
    "the list is summarised, not dumped",
    "other" in many_interfaces_diagnosis.detail,
    many_interfaces_diagnosis.detail,
)
check("every network still reaches the structured form",
      len(many_interfaces_diagnosis.to_dict()["networks"]) == 7)

print("\n== the dict a front-end receives ==")
diagnosis_dict = diagnosis.to_dict()
check("carries same_network", diagnosis_dict["same_network"] is False)
check("carries the target", diagnosis_dict["target"] == "10.0.0.50")
check(
    "carries the networks",
    diagnosis_dict["networks"] == ["10.9.9.0/24", "10.9.7.0/24"],
)
check("carries a ready-made message",
      diagnosis_dict["message"] == diagnosis.message)

print("\n" + ("ALL PASS" if not failures
              else f"{len(failures)} FAILURES: {failures}"))
sys.exit(1 if failures else 0)
