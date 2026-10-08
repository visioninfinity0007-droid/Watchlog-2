#!/usr/bin/env python3
"""Vendor discovery: Hikvision SADP and Dahua DHDiscover, the protocols their own tools use.

Why this exists (HASCO, Hikvision DS-7608NI-Q1, 5.1.2 Setup, 2026-10-08): "no recorder found".
ONVIF WS-Discovery needs ONVIF enabled on the recorder and answers by unicast, and the port
sweep only covers the PC's own /24s, so a recorder on another subnet of the same LAN (a Wi-Fi
PC, a recorder left on its factory 192.0.0.64 / 192.168.1.108 address) is invisible to both.

* Hikvision SADP: an XML <Probe> to 239.255.255.250:37020. Devices answer to the same multicast
  group, so the answer arrives even when the recorder's IP is in another subnet. SADP is on by
  default on Hikvision recorders.
* Dahua DHDiscover: a DHIP-framed JSON "DHDiscover.search" to 255.255.255.255:37810 and to the
  239.255.255.251:37810 group. Dahua recorders answer with their address, model and MAC.

Read-only, outbound-first UDP like wsdiscovery: the socket sends and reads only replies to its
own request. Every interface the PC has (discover.local_ipv4s) is probed, so a second adapter
on the CCTV network is covered too. Stdlib only. Never raises.
"""
from __future__ import annotations

import json
import re
import socket
import struct
import time
import uuid

SADP_GROUP, SADP_PORT = "239.255.255.250", 37020
DH_GROUP, DH_PORT = "239.255.255.251", 37810
DH_BROADCAST = "255.255.255.255"

_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _valid_ip(value) -> str | None:
    text = str(value or "").strip()
    m = _IPV4.match(text)
    if not m or any(int(x) > 255 for x in m.groups()) or text.startswith(("0.", "127.", "255.")):
        return None
    return text


def _xml_tag(blob: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", blob, re.IGNORECASE)
    return m.group(1).strip() if m else None


def sadp_probe_bytes(probe_id: str | None = None) -> bytes:
    pid = probe_id or str(uuid.uuid4()).upper()
    return (f'<?xml version="1.0" encoding="utf-8"?>'
            f"<Probe><Uuid>{pid}</Uuid><Types>inquiry</Types></Probe>").encode("utf-8")


def parse_sadp(data: bytes) -> dict | None:
    """One SADP <ProbeMatch> -> {ip, label, vendor_hint, ...}; None when not a device reply."""
    try:
        blob = data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    if "<ProbeMatch" not in blob:
        return None
    ip = _valid_ip(_xml_tag(blob, "IPv4Address"))
    if not ip:
        return None
    model = _xml_tag(blob, "DeviceDescription") or _xml_tag(blob, "DeviceType") or ""
    row = {"ip": ip, "label": f"Hikvision {model}".strip(), "source": "SADP",
           "vendor_hint": "hikvision"}
    port = _xml_tag(blob, "HttpPort")
    if port and port.isdigit():
        row["preferred_web_port"] = int(port)
    if (_xml_tag(blob, "Activated") or "").lower() == "false":
        row["inactive"] = True
    mask, gateway = _xml_tag(blob, "IPv4SubnetMask"), _xml_tag(blob, "IPv4Gateway")
    if mask:
        row["subnet_mask"] = mask
    if gateway:
        row["gateway"] = gateway
    return row


def dahua_probe_bytes(seq: int = 1) -> bytes:
    body = json.dumps({"method": "DHDiscover.search", "params": {"mac": "", "uni": 1}},
                      separators=(",", ":")).encode("utf-8")
    # DHIP header: 0x20 0 0 0 'DHIP', session id, request id, body length, 0, body length, 0.
    header = struct.pack("<I4sIIIIII", 0x20, b"DHIP", 0, seq, len(body), 0, len(body), 0)
    return header + body + b"\n"


def parse_dahua(data: bytes) -> dict | None:
    start = data.find(b"{")
    if start < 0:
        return None
    try:
        payload = json.loads(data[start:].decode("utf-8", errors="replace").strip().rstrip("\x00"))
    except Exception:  # noqa: BLE001
        return None
    params = payload.get("params") or {}
    info = params.get("deviceInfo") or params
    ipv4 = info.get("IPv4Address") or {}
    ip = _valid_ip(ipv4.get("IPAddress") if isinstance(ipv4, dict) else info.get("IPAddress"))
    if not ip:
        return None
    model = info.get("DeviceType") or info.get("DetailType") or ""
    row = {"ip": ip, "label": f"Dahua {model}".strip(), "source": "DHDiscover",
           "vendor_hint": "dahua"}
    port = info.get("HttpPort")
    if isinstance(port, int) and port > 0:
        row["preferred_web_port"] = port
    if isinstance(ipv4, dict) and ipv4.get("SubnetMask"):
        row["subnet_mask"] = ipv4["SubnetMask"]
    return row


def _socket(local_ip: str | None, *, broadcast: bool = False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    if broadcast:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if local_ip:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
    return s


def _listen(sock, parse, deadline: float, found: dict) -> None:
    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            data, _addr = sock.recvfrom(65535)
        except (socket.timeout, OSError):
            return
        row = parse(data)
        if row and row["ip"] not in found:
            found[row["ip"]] = row


def _sadp(local_ip: str | None, timeout: float, found: dict) -> None:
    try:
        s = _socket(local_ip)
        try:
            # Replies go to the group: join it on this interface to receive them.
            s.bind(("", SADP_PORT))
            mreq = socket.inet_aton(SADP_GROUP) + socket.inet_aton(local_ip or "0.0.0.0")
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            s.close()
            s = _socket(local_ip)
            s.bind((local_ip or "", 0))
        try:
            s.sendto(sadp_probe_bytes(), (SADP_GROUP, SADP_PORT))
            _listen(s, parse_sadp, time.monotonic() + timeout, found)
        finally:
            s.close()
    except OSError:
        return


def _dahua(local_ip: str | None, timeout: float, found: dict) -> None:
    try:
        s = _socket(local_ip, broadcast=True)
        try:
            s.bind((local_ip or "", 0))
            probe = dahua_probe_bytes()
            for target in ((DH_BROADCAST, DH_PORT), (DH_GROUP, DH_PORT)):
                try:
                    s.sendto(probe, target)
                except OSError:
                    pass
            _listen(s, parse_dahua, time.monotonic() + timeout, found)
        finally:
            s.close()
    except OSError:
        return


def discover(timeout: float = 3.0, local_ips=None) -> list[dict]:
    """Every Hikvision/Dahua recorder that answers on any local interface. Never raises."""
    import threading
    if local_ips is None:
        try:
            import discover as _discover
            local_ips = _discover.local_ipv4s()
        except Exception:  # noqa: BLE001
            local_ips = []
    interfaces = list(dict.fromkeys(local_ips or [])) or [None]
    found: dict[str, dict] = {}
    threads = []
    for ip in interfaces[:6]:
        for fn in (_sadp, _dahua):
            t = threading.Thread(target=fn, args=(ip, timeout, found), daemon=True)
            t.start()
            threads.append(t)
    for t in threads:
        t.join(timeout + 1.0)
    return list(found.values())


if __name__ == "__main__":
    for row in discover():
        print(row)
