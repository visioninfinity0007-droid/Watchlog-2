"""Recorder connection hardening (workstream 5).

Decides HOW to reach a recorder and WHAT a probe response means. It NEVER changes any
recorder configuration — every function here is read-only classification / URL building.

Field-proven status is tracked separately in vendor_capabilities.py: implementing a path
here does NOT make it field-proven. Everything added here is "supported but unverified"
until it runs against real hardware.

Covers: HTTP vs HTTPS (443 / 8443 / custom ports), the Hikvision SDK-port-vs-HTTP
confusion, explicit self-signed TLS trust, and local multi-NIC/subnet enumeration for
broader discovery.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl

# Ordered candidate (scheme, port) to try per vendor. https includes 443 and the common
# alternate 8443. ONVIF web services commonly sit on 80/8080/8000-web.
PORT_CANDIDATES = {
    "hikvision": [("http", 80), ("https", 443), ("https", 8443)],
    "dahua":     [("http", 80), ("https", 443), ("https", 8443)],
    "onvif":     [("http", 80), ("http", 8080), ("http", 8000)],
    "auto":      [("http", 80), ("https", 443), ("https", 8443), ("http", 8080)],
}

# Ports that are known NON-HTTP proprietary control/SDK ports. Probing these as HTTP is the
# classic field mistake (esp. Hikvision 8000). We recognise them so we never waste an HTTP
# probe on a binary SDK port, and can tell the operator plainly.
NON_HTTP_PORTS = {
    8000: "hikvision-sdk",     # Hikvision private SDK (binary) — NOT ISAPI/HTTP
    37777: "dahua-sdk",        # Dahua private SDK (binary)
    34567: "xiongmai",         # Xiongmai/Hisilicon proprietary — explicitly unsupported
}


def classify_port(port) -> str | None:
    """Return the known proprietary protocol on a port, or None if it may be an HTTP(S) port."""
    try:
        return NON_HTTP_PORTS.get(int(port))
    except (TypeError, ValueError):
        return None


def build_base_url(host: str, port=None, scheme: str = "http") -> str:
    """Build a recorder base URL, omitting the port when it is the scheme default."""
    scheme = "https" if scheme in ("https", "tls", "ssl") else "http"
    default = 443 if scheme == "https" else 80
    if port is None:
        port = default
    hostpart = str(host).strip().rstrip("/")
    if hostpart.startswith(("http://", "https://")):
        hostpart = hostpart.split("://", 1)[1]
    if ":" in hostpart and not hostpart.startswith("["):   # bare IPv6 literal
        hostpart = f"[{hostpart}]"
    return f"{scheme}://{hostpart}" + ("" if int(port) == default else f":{int(port)}")


def candidates(vendor: str, host: str, configured_port=None) -> list:
    """Ordered connection attempts. A configured port is tried first (both schemes for a custom
    port; https for 443/8443), each flagged if it is a known non-HTTP SDK port."""
    out = []
    if configured_port:
        sdk = classify_port(configured_port)
        schemes = ["https"] if int(configured_port) in (443, 8443) else ["http", "https"]
        for sc in schemes:
            out.append({"scheme": sc, "port": int(configured_port),
                        "base_url": build_base_url(host, configured_port, sc), "non_http": sdk})
    for sc, p in PORT_CANDIDATES.get((vendor or "auto").lower(), PORT_CANDIDATES["auto"]):
        out.append({"scheme": sc, "port": p, "base_url": build_base_url(host, p, sc), "non_http": None})
    seen, uniq = set(), []
    for c in out:
        key = (c["scheme"], c["port"])
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def requests_verify(trust_self_signed: bool = False):
    """The `verify=` value for requests over HTTPS: verify unless the operator has EXPLICITLY
    configured self-signed trust for this recorder. Verification is never silently disabled."""
    return not bool(trust_self_signed)


def tls_context(trust_self_signed: bool = False) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if trust_self_signed:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def classify_probe(status, headers, body) -> str:
    """Classify a probe RESPONSE into a driver hint. Read-only — nothing is changed on the device.
    'http-auth-required' means a reachable web recorder that needs credentials (a distinct, honest
    state from unreachable)."""
    h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    b = (body or "").lower()
    server = h.get("server", "")
    auth = h.get("www-authenticate", "")
    if "hikvision" in server or "app-webs" in server or "isapi" in b:
        return "hikvision-isapi"
    if "dahua" in server or "magicbox" in b or ("webs" in server and "dahua" in b):
        return "dahua-cgi"
    if "onvif" in b or "device_service" in b or "onvif" in server:
        return "onvif"
    if status in (401, 403) and ("digest" in auth or "basic" in auth):
        return "http-auth-required"
    if isinstance(status, int) and 200 <= status < 500:
        return "http-unknown-vendor"
    return "unknown"


def local_subnets(max_hosts: int = 1024) -> list:
    """Best-effort enumeration of this host's private IPv4 /24 subnets for multi-NIC/subnet
    discovery. Returns [(interface_ip, network_cidr)]. Skips loopback and public ranges, and
    subnets too large to scan politely."""
    addrs = set()
    try:
        hostname = socket.gethostname()
        for ai in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addrs.add(ai[4][0])
        addrs.add(socket.gethostbyname(hostname))
    except Exception:                                       # noqa: BLE001
        pass
    nets, seen = [], set()
    for ip in addrs:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not addr.is_private or addr.is_loopback:
            continue
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net in seen or net.num_addresses > max_hosts:
            continue
        seen.add(net)
        nets.append((ip, str(net)))
    return nets
