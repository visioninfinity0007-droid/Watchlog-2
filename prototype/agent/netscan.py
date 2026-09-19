"""Adapter enumeration and bounded scan-target planning for recorder discovery.

FIELD FAILURE THIS EXISTS FOR (HASCO Steel, 2026-09-19). A Windows PC at
192.168.18.190 could open its Hikvision NVR at http://192.168.18.184/ in a browser
and already held `192.168.18.184 ac-b9-2f-39-5c-89 dynamic` in its ARP cache, yet
WatchLog Setup reported that no recorder was found.

Three things have to be right for that not to happen, and they are kept apart here
so each can be tested without opening a socket:

  enumerate_interfaces()  which networks exist, with their REAL prefixes
  neighbours()            which hosts the OS has already seen
  scan_targets()          which addresses to probe, bounded so Setup cannot hang

Deliberately NOT here: probing, port logic and vendor identification, which stay in
discover.py. This module never opens a network connection.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from typing import Callable, NamedTuple

try:                                    # optional: absent from the frozen build
    import psutil
except Exception:                       # noqa: BLE001
    psutil = None

# A /24 is 254 hosts. A /16 is 65,534, which would freeze a customer-facing wizard,
# so sweeping is bounded per interface and overall. Anything larger is REPORTED as
# bounded rather than silently truncated.
MAX_HOSTS_PER_INTERFACE = 1024
MAX_TOTAL_SWEEP_HOSTS = 4096


class Interface(NamedTuple):
    """One usable IPv4 adapter."""

    ip: str
    prefix: int
    name: str = ""

    @property
    def network(self) -> str:
        return str(ipaddress.ip_interface("%s/%d" % (self.ip, self.prefix)).network)

    @property
    def host_count(self) -> int:
        return max(0, (1 << (32 - self.prefix)) - 2)


def usable_ipv4(value: str) -> str | None:
    """A real, private, scannable host address, else None.

    Rejects loopback, link-local (169.254/16), public addresses, broadcast and
    subnet masks -- 255.255.255.0 parses as an address but is not a host.
    """
    try:
        addr = ipaddress.ip_address(str(value).strip())
    except Exception:                                    # noqa: BLE001
        return None
    if addr.version != 4 or addr.is_loopback or addr.is_multicast or addr.is_unspecified:
        return None
    if addr.is_link_local:                               # 169.254/16 means "no DHCP"
        return None
    if not addr.is_private:
        return None
    # 240.0.0.0/4 is reserved, and Python reports it as "private" -- so a subnet mask
    # like 255.255.255.0 passes every check above. Measured on a real Windows box:
    # without this the mask was enumerated as an adapter and generated 254 junk
    # scan targets in a network (255.255.255.0/24) that cannot exist.
    if addr in ipaddress.ip_network("240.0.0.0/4"):
        return None
    return str(addr)


def prefix_from_netmask(mask: str) -> int | None:
    """255.255.255.0 -> 24. None for anything that is not a real contiguous mask."""
    try:
        return ipaddress.ip_network("0.0.0.0/%s" % str(mask).strip()).prefixlen
    except Exception:                                    # noqa: BLE001
        return None


def _run(cmd: list[str], timeout: int = 8) -> str:
    """Run an OS command and return stdout, or "" on any failure."""
    kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    if sys.platform.startswith("win"):
        # The setup UI is --windowed; never flash a console window at a customer.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(cmd, **kwargs).stdout or ""    # noqa: S603
    except Exception:                                    # noqa: BLE001
        return ""


def _via_psutil() -> list[Interface]:
    """Exact addresses and netmasks, when psutil happens to be importable."""
    if psutil is None:
        return []
    out: list[Interface] = []
    try:
        stats = psutil.net_if_stats()
        for name, addresses in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue                                  # disconnected adapter
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                ip = usable_ipv4(address.address)
                if not ip:
                    continue
                prefix = prefix_from_netmask(getattr(address, "netmask", "") or "")
                out.append(Interface(ip=ip, prefix=prefix or 24, name=name))
    except Exception:                                    # noqa: BLE001
        return []
    return out


def _via_command(reader: Callable[[list[str], int], str] = _run) -> list[Interface]:
    """Parse the OS's own dump. THIS is the path that runs on a customer's PC --
    psutil is an optional import and is not in the frozen build.

    Locale-independent: no label text is matched, because a Windows box in any
    language still prints an address immediately followed by its subnet mask. The
    two are paired positionally, and usable_ipv4 rejects the mask itself.
    """
    try:
        text = reader(["ipconfig"] if sys.platform.startswith("win")
                      else ["ip", "-4", "-o", "addr"], 8)
    except Exception:                                    # noqa: BLE001
        return []
    if sys.platform.startswith("win"):
        tokens = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
        out: list[Interface] = []
        consumed_as_mask = set()
        for index, token in enumerate(tokens):
            if index in consumed_as_mask:
                continue                                 # this token was someone's netmask
            ip = usable_ipv4(token)
            if not ip:
                continue
            prefix = 24
            if index + 1 < len(tokens):
                candidate = prefix_from_netmask(tokens[index + 1])
                if candidate is not None:
                    prefix = candidate
                    consumed_as_mask.add(index + 1)
            out.append(Interface(ip=ip, prefix=prefix, name=""))
        return out

    out = []
    for name, cidr in re.findall(
            r"\d+:\s+(\S+)\s+inet\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})", text):
        ip, _, prefix = cidr.partition("/")
        if usable_ipv4(ip):
            out.append(Interface(ip=ip, prefix=int(prefix), name=name))
    return out


def _default_route() -> Interface | None:
    """The interface Windows would use for the Internet. Scanned FIRST, never ONLY.

    On HASCO's PC this could easily be a different adapter from the CCTV LAN; that
    is the whole reason discovery must not stop here.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))                    # selects a route, sends nothing
            ip = usable_ipv4(s.getsockname()[0])
            return Interface(ip=ip, prefix=24, name="default route") if ip else None
        finally:
            s.close()
    except Exception:                                    # noqa: BLE001
        return None


def enumerate_interfaces(sources: list[Callable[[], list[Interface]]] = None,
                         default_route: Callable[[], Interface | None] = None
                         ) -> list[Interface]:
    """Every usable IPv4 adapter, default-route first, de-duplicated by address.

    A VPN, Hyper-V or VMware adapter never displaces the real CCTV NIC -- they are
    all enumerated and all swept. An adapter enumerated with its true netmask
    REPLACES the assumed /24 of the default-route entry for the same address.
    """
    found: list[Interface] = []

    def add(iface: Interface | None):
        if iface and iface.ip and not any(e.ip == iface.ip for e in found):
            found.append(iface)

    add((default_route or _default_route)())
    for source in (sources if sources is not None else [_via_psutil, _via_command]):
        try:
            discovered = source()
        except Exception:                                # noqa: BLE001
            continue                                     # one bad source never blinds the rest
        for iface in discovered:
            existing = next((e for e in found if e.ip == iface.ip), None)
            if existing is None:
                add(iface)
            elif existing.prefix != iface.prefix or (iface.name and not existing.name):
                found[found.index(existing)] = iface     # real prefix beats the assumed /24
    return found


def neighbours(reader: Callable[[list[str], int], str] = _run) -> list[str]:
    """Hosts the OS has already seen (ARP / neighbour cache).

    HASCO's PC held 192.168.18.184 in ARP the entire time Setup was reporting that
    nothing was found. Using what the OS already learned is faster and more reliable
    than brute force and reaches devices a bounded sweep might not.

    An ARP entry alone does NOT mean a recorder. These are candidates only, until a
    recorder port actually answers.
    """
    cmd = ["arp", "-a"] if sys.platform.startswith("win") else ["ip", "neigh"]
    try:
        text = reader(cmd, 8)
    except Exception:                                    # noqa: BLE001
        # The neighbour cache is an optimisation. Losing it must never cost the
        # sweep that follows, which is the path that actually has to work.
        return []
    out: list[str] = []
    for token in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text or ""):
        ip = usable_ipv4(token)
        if ip and ip not in out and not ip.endswith(".255"):
            out.append(ip)
    return out


def scan_targets(interfaces: list[Interface], extra: list[str] = None
                 ) -> tuple[list[str], list[str]]:
    """Addresses to probe, plus a note for every network that had to be bounded.

    Honors the real prefix rather than assuming /24. Neighbour candidates go FIRST
    so a host the OS already knows is confirmed even if a large sweep is truncated
    behind it.
    """
    ordered: list[str] = []
    notes: list[str] = []

    def add(ip: str) -> bool:
        if ip and ip not in ordered:
            ordered.append(ip)
        return len(ordered) < MAX_TOTAL_SWEEP_HOSTS

    for ip in (extra or []):
        if not add(ip):
            break

    for iface in interfaces:
        try:
            network = ipaddress.ip_interface("%s/%d" % (iface.ip, iface.prefix)).network
        except Exception:                                # noqa: BLE001
            continue
        taken = 0
        truncated = False
        for host in network.hosts():
            if taken >= MAX_HOSTS_PER_INTERFACE:
                truncated = True
                break
            room = add(str(host))
            taken += 1
            if not room:
                truncated = taken < iface.host_count
                break
        if truncated:
            notes.append("%s bounded to %d of %d hosts"
                         % (network, taken, iface.host_count))
        if len(ordered) >= MAX_TOTAL_SWEEP_HOSTS:
            notes.append("sweep stopped at %d hosts overall" % MAX_TOTAL_SWEEP_HOSTS)
            break
    return ordered[:MAX_TOTAL_SWEEP_HOSTS], notes


__all__ = ["Interface", "MAX_HOSTS_PER_INTERFACE", "MAX_TOTAL_SWEEP_HOSTS",
           "enumerate_interfaces", "neighbours", "prefix_from_netmask",
           "scan_targets", "usable_ipv4"]
