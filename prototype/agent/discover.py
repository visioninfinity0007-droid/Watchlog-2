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
import subprocess
import sys
import time
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
    (8000,  "tcp",   "Hikvision SDK port"),
    (81,    "http",  "alternate web"),
    (82,    "http",  "alternate web"),
    (88,    "http",  "alternate web"),
    (8081,  "http",  "alternate web"),
    (443,   "https", "HTTPS web interface"),
    (8443,  "https", "alternate HTTPS"),
    (8888,  "http",  "common alternate web/API port"),
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


def _http_probe(host: str, port: int, tls: bool, path: str = "/") -> tuple:
    """One unauthenticated GET. Returns (status, server, title/auth realm, snippet)."""
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
        target = path if path.startswith("/") else "/" + path
        sock.sendall(f"GET {target} HTTP/1.1\r\nHost: {host}\r\n"
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


def probe_hikvision_isapi(host: str, open_ports) -> dict:
    """Read-only Hikvision integration probe with no credentials.

    The normal web home page is often a generic JavaScript shell and may not contain
    the word Hikvision. Querying the documented ISAPI identity path gives a much
    stronger signal: DeviceInfo XML means active, while a Digest/Basic challenge with
    a Hikvision-ish server/realm means active and awaiting credentials.
    """
    ports = [int(p) for p in (open_ports or [])]
    for port in (80, 443, 8080, 8443, 81, 82, 88, 8081, 8888):
        if port not in ports:
            continue
        tls = port in (443, 8443)
        status, server, title, snippet = _http_probe(
            host, port, tls, "/ISAPI/System/deviceInfo")
        blob = " ".join(str(x or "") for x in (server, title, snippet))
        vendor = _guess(blob)
        device_xml = "deviceinfo" in (snippet or "").lower()
        hik_clue = bool(vendor and "hikvision" in vendor.lower())
        if status == 200 and device_xml:
            return {"vendor_hint": "hikvision", "state": "active", "port": port}
        if status in (401, 403) and hik_clue:
            return {"vendor_hint": "hikvision", "state": "auth_required", "port": port}
        if status in (404, 405, 501) and hik_clue:
            return {"vendor_hint": "hikvision", "state": "unavailable", "port": port}
    return {"vendor_hint": None, "state": "unknown", "port": None}


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


# Every port the rest of the setup stack can actually talk to. This list must stay a
# superset of setup_backend._WEB_PORTS / vendor-signature ports.
SWEEP_PORTS = [80, 443, 8000, 8080, 8443, 81, 82, 88, 8081, 8888, 554, 37777, 37778, 34567]

# Field Build 74 exposed a release-blocking discovery failure: a Windows PC with several
# private/virtual adapters could queue 8 x /24 x many ports and leave the setup UI spinning
# for minutes. Discovery is now budgeted as a product interaction, not an unbounded scan.
#
# Phase 1 uses the four highest-value recorder/control ports with a patient enough timeout
# to survive cold ARP/neighbour learning. Phase 2 checks the remaining CCTV/web ports only
# while the global deadline remains. Physical adapters are ranked ahead of virtual/VPN
# adapters, and automatic scanning is capped at four /24s. Explicit subnet and manual-IP
# paths remain available for unusual topologies.
SWEEP_FAST_PORTS = [37777, 8000, 80, 443]
SWEEP_DEEP_PORTS = [port for port in SWEEP_PORTS if port not in SWEEP_FAST_PORTS]
RECORDER_SIGNATURE_PORTS = {37777, 37778, 8000, 34567}
SWEEP_TIMEOUT = 0.75
SWEEP_DEEP_TIMEOUT = 0.35
# Build 69 (field-proven, Chai Wala) ran 768 parallel probes. 256 only just fit the fast phase
# of eight /24s into the 32 s Build 74 deadline when absent hosts time out (8th network at
# ~22.6 s) and cut the alternate-port phase short; 512 reaches the 8th network at ~13 s and
# leaves the alternate-port phase time for the first networks (test_discovery_field_timing).
SWEEP_WORKERS = 512
MAX_AUTO_SUBNETS = 8
DISCOVERY_DEADLINE_SECONDS = 32.0


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
    # Reserved 240.0.0.0/4 is never a LAN host, and Python classifies it as "private",
    # so is_private alone would accept a subnet mask (255.255.255.0) scraped out of an
    # ipconfig dump and then sweep a nonsense /24.
    if addr.is_reserved:
        return None
    # Never auto-scan a public /24. CCTV interfaces should be RFC1918 or
    # link-local; manual IP entry remains available for unusual topologies.
    if not (addr.is_private or addr.is_link_local):
        return None
    return str(addr)


_VIRTUAL_ADAPTER_TOKENS = (
    "virtual", "vethernet", "hyper-v", "vmware", "virtualbox", "docker",
    "wsl", "tailscale", "wireguard", "vpn", "loopback", "bluetooth",
)


def _adapter_ipv4s() -> list[str]:
    """Reliable active-interface enumeration, physical NICs before virtual/VPN NICs.

    We do not discard virtual adapters completely because unusual CCTV deployments can
    legitimately use one. We rank them after physical Ethernet/Wi-Fi so the bounded
    automatic scan cannot spend its whole budget on Docker/Hyper-V/VPN /24s first.
    """
    if psutil is None:
        return []
    primary: list[str] = []
    secondary: list[str] = []
    try:
        stats = psutil.net_if_stats()
        for name, addresses in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            lower_name = str(name).lower()
            bucket = secondary if any(token in lower_name for token in _VIRTUAL_ADAPTER_TOKENS) else primary
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                ip = _usable_ipv4(address.address)
                if ip and ip not in primary and ip not in secondary:
                    bucket.append(ip)
    except Exception:                                    # noqa: BLE001
        return []
    return primary + secondary


def _command_ipv4s() -> list[str]:
    """Adapter enumeration that does not depend on psutil being packaged.

    psutil is an optional import and was never added to the frozen build, so the
    shipped installer silently lost multi-adapter discovery and swept only the
    default-route /24 — exactly the blind spot local_ipv4s() exists to close.
    Parse the OS's own interface dump as well. _usable_ipv4 rejects masks (255.x
    is not private) and public addresses; a default gateway shares its host's /24
    so an extra hit is harmless. Locale-independent: no label parsing.
    """
    kwargs = {"capture_output": True, "text": True, "timeout": 6}
    if sys.platform.startswith("win"):
        cmd = ["ipconfig"]
        # --windowed setup UI: never flash a console window.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        cmd = ["ip", "-4", "-o", "addr"]
    try:
        proc = subprocess.run(cmd, **kwargs)             # noqa: S603
    except Exception:                                    # noqa: BLE001
        return []
    found: list[str] = []
    for raw in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", proc.stdout or ""):
        ip = _usable_ipv4(raw)
        if ip and ip not in found:
            found.append(ip)
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

    # Runs even when psutil is present: a CCTV NIC that psutil misses (or a build
    # without psutil at all) must never silently shrink the swept network set.
    for ip in _command_ipv4s():
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


def _has_recorder_signature(found: dict[str, set[int]]) -> bool:
    """A generic router/web response is not proof that the recorder was found."""
    return any(bool(ports & RECORDER_SIGNATURE_PORTS) for ports in found.values())


def sweep(subnet: str | None = None, log=print,
          progress=lambda _message: None, _connect=None,
          _bases=None, _clock=None) -> list[tuple[str, list[int]]]:
    """Find recorder candidates without ever leaving setup spinning indefinitely.

    Automatic discovery is deliberately bounded. It scans up to the same eight local
    /24s covered by field-proven Build 69 (physical adapters first), then returns whatever it proved before the global
    deadline. The fast phase preserves multiple strong recorder candidates across those
    ranked LANs; once that phase proves native recorder signatures, only those recorder
    hosts are service-confirmed and the expensive deep broad scan is skipped. Manual IP
    and explicit-subnet paths are never removed.
    """
    bases, addresses = _bases if _bases is not None else _sweep_bases(subnet)
    connect_fn = _connect or socket.create_connection
    clock = _clock or time.monotonic          # injectable for a deterministic selftest
    if not bases:
        log("  could not work out this PC's network; enter the recorder IP manually")
        return []

    started = clock()
    deadline = started + DISCOVERY_DEADLINE_SECONDS
    if addresses:
        log(f"  this PC has local IPv4: {', '.join(addresses)}")
    log("  bounded sweep: " + ", ".join(f"{base}.1-254" for base in bases))

    def probe(args):
        ip, port, budget = args
        try:
            with connect_fn((ip, port), timeout=budget):
                return ip, port
        except Exception:                                # noqa: BLE001
            return None

    found: dict[str, set[int]] = {}

    def run_stage(targets, label: str):
        """Run one bounded batch. Socket timeouts make each stage finite."""
        if clock() >= deadline:
            return
        progress(label)
        with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
            for hit in pool.map(probe, targets):
                if hit:
                    found.setdefault(hit[0], set()).add(hit[1])
                if clock() >= deadline:
                    # Do not enqueue another discovery phase after this one. pool.map's
                    # already-running connects are individually bounded by their socket
                    # timeout, so leaving the context cannot turn into a minutes-long hang.
                    break

    # Phase 1: patient probes on the ports that prove most Hikvision/Dahua boxes.
    for index, base in enumerate(bases, 1):
        if clock() >= deadline:
            break
        progress(f"Checking local network {index}/{len(bases)} ({base}.x)…")
        hosts = [f"{base}.{h}" for h in range(1, 255)]
        run_stage(
            [(ip, port, SWEEP_TIMEOUT) for ip in hosts for port in SWEEP_FAST_PORTS],
            f"Checking local network {index}/{len(bases)} for recorder services…",
        )
        # Keep scanning the other ranked physical LANs in this fast phase so a PC
        # connected to more than one recorder network can still present every strong
        # candidate. The cap + timeout keep this finite.

    # Confirm every strong candidate with the complete port set, using the same
    # patient timeout. This recovers RTSP / alternate web ports without a broad scan.
    recorder_ips = [ip for ip, ports in found.items() if ports & RECORDER_SIGNATURE_PORTS]
    if recorder_ips and clock() < deadline:
        progress("Recorder found. Confirming its services…")
        remaining = [
            (ip, port, SWEEP_TIMEOUT)
            for ip in recorder_ips
            for port in SWEEP_PORTS
            if port not in found.get(ip, set())
        ]
        run_stage(remaining, "Recorder found. Confirming web and video services…")
        return [(ip, sorted(ports)) for ip, ports in
                sorted(found.items(), key=lambda kv: [int(x) for x in kv[0].split(".")])]

    # Phase 2: no native signature yet. Check alternate web/RTSP/vendor ports only
    # while the global UX budget remains. This keeps HTTPS/custom-port recorders
    # discoverable without allowing a fleet of virtual adapters to hang setup.
    for index, base in enumerate(bases, 1):
        if clock() >= deadline:
            break
        hosts = [f"{base}.{h}" for h in range(1, 255)]
        run_stage(
            [(ip, port, SWEEP_DEEP_TIMEOUT) for ip in hosts for port in SWEEP_DEEP_PORTS],
            f"Checking alternate CCTV ports on network {index}/{len(bases)}…",
        )

    recorder_ips = [ip for ip, ports in found.items() if ports & RECORDER_SIGNATURE_PORTS]
    if recorder_ips and clock() < deadline:
        progress("Recorder found. Confirming its services…")
        remaining = [
            (ip, port, SWEEP_TIMEOUT)
            for ip in recorder_ips
            for port in SWEEP_PORTS
            if port not in found.get(ip, set())
        ]
        run_stage(remaining, "Recorder found. Confirming web and video services…")

    elapsed = clock() - started
    log(f"  discovery finished in {elapsed:.1f}s; {len(found)} host(s) answered")
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


def fingerprint(host: str, open_ports) -> dict:
    """Fingerprint an already-discovered host without relying on ONVIF.

    Only ports already observed open are touched. HTTP/HTTPS banners, auth realms
    and titles are inspected read-only; binary vendor/RTSP ports remain evidence
    but are never spoken to as HTTP. This is the ONVIF-OFF discovery path.
    """
    ports = sorted({int(p) for p in (open_ports or [])})
    by_port = {p: (kind, note) for p, kind, note in PORTS}
    web = []
    vendor_guess = None
    for port in ports:
        spec = by_port.get(port)
        if not spec:
            continue
        kind, note = spec
        if kind not in ("http", "https"):
            continue
        result = scan_port(host, port, kind, note)
        web.append({
            "port": port,
            "kind": kind,
            "status": result.status,
            "server": result.server,
            "title": result.title,
            "vendor_guess": result.vendor_guess,
        })
        if not vendor_guess and result.vendor_guess:
            vendor_guess = result.vendor_guess
    return {
        "host": host,
        "ports": ports,
        "vendor_guess": vendor_guess,
        "rtsp": 554 in ports,
        "web": web,
    }


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
