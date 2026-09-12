"""
Recorder discovery — what is actually at that address?

Exists because "no driver recognised the device" is a useless thing to
read while standing in front of a rack. It does not distinguish between
a wrong IP, a closed port, a web interface moved to 8080, an HTTPS-only
unit, or a recorder that speaks a protocol we do not support at all.
Those have four different fixes.

This scans the common recorder ports, reports exactly what answered, and
guesses the vendor from whatever it says about itself.

Outbound TCP connects only. Nothing is sent beyond an HTTP GET /.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    import psutil
except ImportError:                                      # source/dev fallback
    psutil = None

CONNECT_TIMEOUT = 1.5
READ_TIMEOUT = 3.0

# Ports worth trying, and why. Order is display order.
PORTS: list[tuple[int, str, str]] = [
    (80,    "http",  "standard web interface"),
    (8080,  "http",  "common alternate web port"),
    (8000,  "http",  "Hikvision SDK / alternate web"),
    (81,    "http",  "alternate web"),
    (88,    "http",  "alternate web"),
    (8081,  "http",  "alternate web"),
    (443,   "https", "HTTPS web interface"),
    (8443,  "https", "alternate HTTPS"),
    (37777, "tcp",   "Dahua SDK port"),
    (37778, "tcp",   "Dahua SDK (UDP twin)"),
    (34567, "tcp",   "Xiongmai / XMEye - NOT SUPPORTED"),
    (9000,  "tcp",   "Xiongmai variant - NOT SUPPORTED"),
    (554,   "tcp",   "RTSP video stream"),
]

VENDOR_HINTS = [
    (re.compile(r"hikvision|DS-|webs", re.I),        "Hikvision (or HiLook)"),
    (re.compile(r"dahua|DH-|NVR\d{4}", re.I),        "Dahua (or CP Plus / Imou)"),
    (re.compile(r"uniview|unv", re.I),               "Uniview"),
    (re.compile(r"tiandy", re.I),                    "Tiandy"),
    (re.compile(r"xiongmai|xmeye|netsurveillance", re.I),
                                                     "Xiongmai - NOT SUPPORTED"),
    (re.compile(r"boa|thttpd|lighttpd", re.I),       "generic embedded web server"),
]


@dataclass
class PortResult:
    port: int
    kind: str
    note: str
    open: bool
    status: int | None = None
    server: str | None = None
    title: str | None = None
    vendor_guess: str | None = None
    detail: str | None = None


def host_of(target: str) -> str:
    """Accept a bare IP, a host, or a full URL."""
    t = target.strip()
    if "://" in t:
        return urlparse(t).hostname or t
    return t.split("/")[0].split(":")[0]


def _http_probe(host: str, port: int, tls: bool) -> tuple:
    """One GET / with no auth. Returns (status, server, title, snippet)."""
    raw = b""
    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        if tls:
            ctx = ssl.create_default_context()
            # Recorders ship self-signed certs as a rule. We are reading a
            # banner to identify the box, not trusting it with anything.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.settimeout(READ_TIMEOUT)
        sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                     f"User-Agent: WatchLog-Discover\r\n"
                     f"Connection: close\r\n\r\n".encode())
        while len(raw) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        sock.close()
    except Exception:                                    # noqa: BLE001
        # raw is bytes; callers join the snippet as text, so never leak bytes
        return None, None, None, raw.decode("utf-8", "replace")

    text = raw.decode("utf-8", "replace")
    status = None
    m = re.match(r"HTTP/\d\.\d\s+(\d{3})", text)
    if m:
        status = int(m.group(1))
    server = None
    m = re.search(r"^Server:\s*(.+)$", text, re.I | re.M)
    if m:
        server = m.group(1).strip()
    # WWW-Authenticate often names the vendor's realm, e.g. realm="DS-7608"
    m = re.search(r"^WWW-Authenticate:\s*(.+)$", text, re.I | re.M)
    # Keep only the realm - the nonce and opaque are noise, and a realm
    # like "DS-7608NI" is often the clearest model hint the box gives.
    auth = None
    if m:
        rm = re.search(r'realm="([^"]*)"', m.group(1))
        auth = f"realm {rm.group(1)}" if rm else m.group(1).strip()[:60]
    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:60]
    return status, server, (title or auth), text[:400]


def _guess(*blobs) -> str | None:
    # Tolerate a bytes blob (e.g. a partial banner from a failed probe) so a
    # scan never dies with "sequence item: expected str instance, bytes found".
    parts = [(b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray))
              else str(b)) for b in blobs if b]
    joined = " ".join(parts)
    for rx, name in VENDOR_HINTS:
        if rx.search(joined):
            return name
    return None


def scan_port(host: str, port: int, kind: str, note: str) -> PortResult:
    res = PortResult(port=port, kind=kind, note=note, open=False)
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            res.open = True
    except Exception:                                    # noqa: BLE001
        return res

    if kind in ("http", "https"):
        status, server, title, snippet = _http_probe(host, port, kind == "https")
        res.status, res.server, res.title = status, server, title
        res.vendor_guess = _guess(server, title, snippet)
    return res


# A short list for sweeping a whole subnet - 254 hosts x 13 ports is slow
# and mostly pointless. These six catch every recorder we can support plus
# the two families we cannot, so an unsupported unit still gets named.
SWEEP_PORTS = [80, 8000, 8080, 554, 37777, 34567]
SWEEP_TIMEOUT = 0.4
MAX_AUTO_SUBNETS = 8


def _usable_ipv4(value: str | None) -> str | None:
    """Normalize a local IPv4 suitable for a bounded LAN sweep."""
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address):
        return None
    if addr.is_unspecified or addr.is_loopback or addr.is_multicast:
        return None
    # Never auto-scan a public /24. CCTV interfaces should be RFC1918 or
    # link-local; manual IP entry remains available for unusual topologies.
    if not (addr.is_private or addr.is_link_local):
        return None
    return str(addr)


def _adapter_ipv4s() -> list[str]:
    """Reliable active-interface enumeration when psutil is packaged."""
    if psutil is None:
        return []
    found: list[str] = []
    try:
        stats = psutil.net_if_stats()
        for name, addresses in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                ip = _usable_ipv4(address.address)
                if ip and ip not in found:
                    found.append(ip)
    except Exception:                                    # noqa: BLE001
        return []
    return found


def local_ipv4s() -> list[str]:
    """Every usable local IPv4, with the default-route interface first.

    0.4.2 could scan only the Wi-Fi/default-route /24 while the recorder sat on
    a separate CCTV Ethernet interface. Packaged setup now enumerates all active
    adapters, then falls back to hostname resolution if adapter enumeration is
    unavailable. UDP connect selects an interface but sends no packet.
    """
    found: list[str] = []

    def add(value):
        ip = _usable_ipv4(value)
        if ip and ip not in found:
            found.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:                                    # noqa: BLE001
        pass

    for ip in _adapter_ipv4s():
        add(ip)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except Exception:                                    # noqa: BLE001
        pass

    return found


def local_ipv4() -> str | None:
    """Backward-compatible preferred local IPv4 helper."""
    addresses = local_ipv4s()
    return addresses[0] if addresses else None


def _sweep_bases(subnet: str | None = None) -> tuple[list[str], list[str]]:
    """Resolve one explicit /24 or every distinct local /24 (bounded)."""
    if subnet:
        host = host_of(subnet)
        parts = host.split(".")
        if len(parts) < 3 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts[:3]):
            return [], []
        return [".".join(parts[:3])], []

    addresses = local_ipv4s()
    bases: list[str] = []
    for ip in addresses:
        base = ".".join(ip.split(".")[:3])
        if base not in bases:
            bases.append(base)
        if len(bases) >= MAX_AUTO_SUBNETS:
            break
    return bases, addresses


def sweep(subnet: str | None = None, log=print) -> list[tuple[str, list[int]]]:
    """
    Find recorders on the relevant local /24 network(s).

    Explicit subnet keeps the old single-/24 behavior. Automatic discovery scans
    each distinct active local /24 so a Wi-Fi + CCTV-Ethernet PC cannot hide the
    recorder merely because Windows routes internet traffic over Wi-Fi.
    """
    bases, addresses = _sweep_bases(subnet)
    if not bases:
        log("  could not work out this PC's network; enter the recorder IP manually")
        return []

    if addresses:
        log(f"  this PC has local IPv4: {', '.join(addresses)}")
    log("  sweeping " + ", ".join(f"{base}.1-254" for base in bases) + " on ports "
        + f"{', '.join(str(p) for p in SWEEP_PORTS)} ...")

    def probe(args):
        ip, port = args
        try:
            with socket.create_connection((ip, port), timeout=SWEEP_TIMEOUT):
                return ip, port
        except Exception:                                # noqa: BLE001
            return None

    targets = [(f"{base}.{h}", p)
               for base in bases
               for h in range(1, 255)
               for p in SWEEP_PORTS]
    found: dict[str, set[int]] = {}
    with ThreadPoolExecutor(max_workers=256) as pool:
        for hit in pool.map(probe, targets):
            if hit:
                found.setdefault(hit[0], set()).add(hit[1])
    return [(ip, sorted(ports)) for ip, ports in
            sorted(found.items(), key=lambda kv: [int(x) for x in kv[0].split(".")])]


def sweep_report(hits: list[tuple[str, list[int]]], log=print) -> None:
    if not hits:
        log("")
        log("  Nothing on this network answered on any recorder port.")
        log("  Either the recorder is on a different network to this PC,")
        log("  or it is switched off.")
        log("")
        return

    log("")
    log("  ADDRESS            OPEN PORTS        LOOKS LIKE")
    log("  -------            ----------        ----------")
    candidates = []
    for ip, ports in hits:
        ports = sorted(ports)
        guess = ""
        if 37777 in ports:
            guess = "Dahua-family recorder"
        elif 34567 in ports:
            guess = "Xiongmai recorder - NOT SUPPORTED"
        elif 554 in ports:
            guess = "something streaming video (camera or recorder)"
        elif ports == [80]:
            guess = "web device - could be the router"
        if 37777 in ports or 34567 in ports or 554 in ports:
            candidates.append((ip, ports, guess))
        log(f"  {ip:<18} {', '.join(str(p) for p in ports):<17} {guess}")

    log("")
    if candidates:
        log("  LIKELY RECORDER(S)")
        for ip, ports, guess in candidates:
            web = [p for p in ports if p in (80, 8000, 8080)]
            if web:
                log(f"    {ip} - set  nvr_url = http://{ip}:{web[0]}"
                    + ("  (port 80 is the default)" if web[0] == 80 else ""))
                if web[0] == 80:
                    log(f"           or simply  nvr_url = http://{ip}")
            else:
                log(f"    {ip} - recorder is there, but no web port is open.")
                log(f"           Enable HTTP on it: Main Menu > Network > Port")
        log("")
    else:
        log("  Nothing looks like a recorder. The devices above are probably")
        log("  the router and PCs.")
        log("")


def scan(target: str, log=print) -> list[PortResult]:
    host = host_of(target)
    log(f"\n  scanning {host} on {len(PORTS)} common recorder ports...\n")
    with ThreadPoolExecutor(max_workers=13) as pool:
        results = list(pool.map(
            lambda p: scan_port(host, p[0], p[1], p[2]), PORTS))
    return results


def report(host: str, results: list[PortResult], log=print) -> None:
    openp = [r for r in results if r.open]

    if not openp:
        log(f"  NOTHING is listening on {host} on any port we tried.\n")
        log("  That means the agent never even reached a device. Check:")
        log(f"    - is {host} the recorder's real address? Look at the")
        log("      recorder's own screen: Menu > Network, or the router's")
        log("      list of connected devices.")
        log("    - is this PC on the SAME network as the recorder? It must")
        log("      be the same LAN, not a different subnet or a guest wifi.")
        log(f"    - can you open  http://{host}  in a browser on this PC?")
        log("      If the browser cannot reach it either, it is a network")
        log("      problem, not a WatchLog problem.")
        log("    - is a firewall or antivirus blocking outbound LAN traffic?")
        return

    log("  PORT   STATE   WHAT ANSWERED")
    log("  ----   -----   -------------")
    for r in results:
        if not r.open:
            continue
        bits = []
        if r.status is not None:
            bits.append(f"HTTP {r.status}")
        if r.server:
            bits.append(r.server)
        if r.title:
            bits.append(f'"{r.title}"')
        log(f"  {r.port:<6} open    {' | '.join(bits) or r.note}")
        if r.vendor_guess:
            log(f"         {'':6}  looks like: {r.vendor_guess}")

    log("")
    web = [r for r in openp if r.kind in ("http", "https") and r.status]
    unsupported = [r for r in openp if r.port in (34567, 9000)]
    dahua_sdk = [r for r in openp if r.port in (37777, 37778)]
    rtsp = [r for r in openp if r.port == 554]

    # 37777 is Dahua's private binary SDK protocol, not HTTP. Its presence
    # identifies the vendor family with near certainty - but we cannot
    # talk to it, and pointing nvr_url at it will never work. What we need
    # is the same box's HTTP interface.
    if dahua_sdk and not web:
        log("  WHAT TO DO NEXT")
        log("    Port 37777 is open. That is Dahua's private SDK port, so")
        log("    this is almost certainly a Dahua-family recorder - Dahua")
        log("    itself, or CP Plus / Imou / another Dahua-based badge.")
        log("    Good news: that is a driver we already have.")
        log("")
        log("    But 37777 speaks a binary protocol, not HTTP. We need the")
        log("    same recorder's WEB interface, which did not answer on any")
        log("    port tried. Do one of these:")
        log("")
        log("      1. On the recorder: Main Menu > Network > Port. Read the")
        log("         'HTTP Port' value. If it is not 80, tell us the number.")
        log("      2. If HTTP is disabled there, enable it and save.")
        log("      3. Then set  nvr_url = http://<ip>:<that http port>")
        log("")
        log("    Do NOT set nvr_url to :37777 - it is not a web port and")
        log("    will never work.")
        log("")
        return

    if web:
        best = web[0]
        scheme = "https" if best.kind == "https" else "http"
        log("  WHAT TO DO NEXT")
        if best.port in (80, 443):
            log(f"    A web interface answered on the standard port, so the")
            log(f"    address is right. The driver still did not recognise it")
            log(f"    - most likely wrong username/password, or a model we")
            log(f"    have not seen. Check nvr_username / nvr_password first.")
        elif dahua_sdk:
            log(f"    The web interface is on port {best.port}, and port")
            log(f"    37777 is open too - that is Dahua's SDK port, so this")
            log(f"    is a Dahua-family recorder. Set this and re-probe:")
            log("")
            log(f"        nvr_url = {scheme}://{host}:{best.port}")
        else:
            log(f"    The web interface is on port {best.port}, not 80.")
            log(f"    Set this in watchlog.ini and run --probe again:")
            log("")
            log(f"        nvr_url = {scheme}://{host}:{best.port}")
    elif unsupported:
        log("  WHAT TO DO NEXT")
        log("    The only thing answering is a Xiongmai/XMEye-style port.")
        log("    These are the cheap unbranded recorders, and they are NOT")
        log("    supported - they speak a proprietary binary protocol and")
        log("    usually have broken or absent ONVIF. This needs a new")
        log("    driver; it is not a configuration problem.")
    else:
        log("  WHAT TO DO NEXT")
        log("    Something is listening but no web interface answered.")
        log("    The recorder's HTTP interface may be disabled - many units")
        log("    ship with it off. Enable it in the recorder's own menu")
        log("    under Network > Advanced / HTTP, then try again.")
    log("")
