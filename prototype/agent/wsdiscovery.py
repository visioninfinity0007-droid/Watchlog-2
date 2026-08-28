"""
ONVIF WS-Discovery — ask the network what recorders are on it.

Why this exists: sweeping 254 addresses on six ports is a guess. It finds
open ports and infers a recorder from the port number. WS-Discovery is
what the industry actually uses - it is how Hikvision's SADP tool and
Dahua's ConfigTool find devices with no IP address typed in, and how
every cloud VMS onboards an existing camera fleet.

How it works: one UDP Probe to the multicast group 239.255.255.250:3702.
Every ONVIF device in the broadcast domain answers directly, and its
answer contains XAddrs - the full service URL, including its own IP AND
the port its ONVIF service is on. No guessing.

    Probe  --multicast-->  every device on the LAN
    ProbeMatch  <--unicast--  "I am http://192.168.1.108:80/onvif/device_service"

On "does this listen?": the socket sends first and reads the replies to
its own request, like any UDP client. It accepts nothing unsolicited and
opens no inbound path from outside the LAN, so the outbound-only property
of the agent is unchanged.

Caveat worth knowing: ONVIF discovery must be enabled on the device, and
some recorders ship with it off. It is a fast, high-quality first attempt,
not a guarantee - which is why the port sweep stays as the fallback.

Stdlib only.
"""

from __future__ import annotations

import re
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

MULTICAST_ADDR = "239.255.255.250"
MULTICAST_PORT = 3702
LISTEN_SECONDS = 4.0

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{msg_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""

# A second probe for recorders. NVRs answer NetworkVideoDisplay or the
# generic Device type rather than NetworkVideoTransmitter, and asking only
# for transmitters finds cameras while missing the recorder itself.
PROBE_DEVICE = PROBE.replace("<d:Types>dn:NetworkVideoTransmitter</d:Types>",
                             "<d:Types>dn:Device</d:Types>")

_XADDRS = re.compile(r"<[^>]*XAddrs[^>]*>(.*?)</[^>]*XAddrs>", re.S | re.I)
_SCOPES = re.compile(r"<[^>]*Scopes[^>]*>(.*?)</[^>]*Scopes>", re.S | re.I)


@dataclass
class Found:
    ip: str
    port: int
    xaddr: str
    name: str | None = None
    hardware: str | None = None
    scopes: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return f"http://{self.ip}" if self.port == 80 else \
               f"http://{self.ip}:{self.port}"


def _scope_value(scopes: list[str], key: str) -> str | None:
    """ONVIF scopes look like onvif://www.onvif.org/name/MyCamera."""
    for s in scopes:
        marker = f"/{key}/"
        if marker in s:
            val = s.split(marker, 1)[1].strip().strip("/")
            if val:
                # Scope values are URL-encoded; a space is %20.
                return val.replace("%20", " ").replace("_", " ")
    return None


def _local_addresses() -> list[str]:
    """
    Every local IPv4 we might need to send from.

    A site PC often has more than one - ethernet to the camera network
    plus wifi to the office. Probing from only the default route misses
    the recorder exactly when it matters most.
    """
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:                                    # noqa: BLE001
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip)
    except Exception:                                    # noqa: BLE001
        pass
    return sorted(addrs) or ["0.0.0.0"]


def discover(timeout: float = LISTEN_SECONDS, log=lambda m: None) -> list[Found]:
    """Probe every local interface and collect what answers."""
    results: dict[str, Found] = {}

    for local_ip in _local_addresses():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # TTL 2 so the probe survives one hop, e.g. a managed switch,
            # without leaking across the wider network.
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                            struct.pack("b", 2))
            if local_ip != "0.0.0.0":
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(local_ip))
                sock.bind((local_ip, 0))
            sock.settimeout(0.5)
        except OSError as e:
            log(f"    (cannot probe from {local_ip}: {e})")
            continue

        log(f"    probing from {local_ip}")
        for probe in (PROBE, PROBE_DEVICE):
            body = probe.format(msg_id=uuid.uuid4()).encode("utf-8")
            for dest in ((MULTICAST_ADDR, MULTICAST_PORT),
                         ("255.255.255.255", MULTICAST_PORT)):
                try:
                    sock.sendto(body, dest)
                except OSError:
                    pass

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            found = _parse(data, addr[0])
            if found and found.xaddr not in results:
                results[found.xaddr] = found
        sock.close()

    return sorted(results.values(),
                  key=lambda f: [int(x) for x in f.ip.split(".")
                                 if x.isdigit()] or [0])


def _parse(data: bytes, sender_ip: str) -> Found | None:
    text = data.decode("utf-8", "replace")
    m = _XADDRS.search(text)
    if not m:
        return None

    # XAddrs can hold several space-separated URLs. Prefer one whose host
    # matches the packet's actual sender: devices routinely advertise a
    # stale or wrong address, and the sender IP is ground truth.
    urls = m.group(1).split()
    chosen = None
    for u in urls:
        try:
            if urlparse(u).hostname == sender_ip:
                chosen = u
                break
        except ValueError:
            continue
    chosen = chosen or (urls[0] if urls else None)
    if not chosen:
        return None

    try:
        parsed = urlparse(chosen)
        port = parsed.port or 80
    except ValueError:
        port = 80

    scopes: list[str] = []
    sm = _SCOPES.search(text)
    if sm:
        scopes = sm.group(1).split()

    return Found(
        ip=sender_ip,
        port=port,
        xaddr=chosen,
        name=_scope_value(scopes, "name"),
        hardware=_scope_value(scopes, "hardware"),
        scopes=scopes,
    )


def report(found: list[Found], log=print) -> None:
    if not found:
        log("  No device answered the ONVIF discovery probe.")
        log("  That does not mean there is no recorder - many ship with")
        log("  ONVIF discovery turned off. Falling back to a port scan.")
        return
    log("")
    log("  ONVIF discovery replies:")
    for f in found:
        label = " ".join(x for x in (f.name, f.hardware) if x) or "unnamed"
        log(f"    {f.base_url:<30} {label}")
