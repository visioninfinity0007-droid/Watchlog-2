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

import re
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse

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
        return None, None, None, raw

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


def _guess(*blobs: str | None) -> str | None:
    joined = " ".join(b for b in blobs if b)
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

    if web:
        best = web[0]
        scheme = "https" if best.kind == "https" else "http"
        log("  WHAT TO DO NEXT")
        if best.port in (80, 443):
            log(f"    A web interface answered on the standard port, so the")
            log(f"    address is right. The driver still did not recognise it")
            log(f"    - most likely wrong username/password, or a model we")
            log(f"    have not seen. Check nvr_username / nvr_password first.")
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
