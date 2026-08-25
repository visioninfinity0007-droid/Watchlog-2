"""
Server-side HTTP Digest authentication (RFC 2617), for the device
simulators.

Real Hikvision and Dahua recorders both challenge with Digest, and the
auth handshake is a genuine source of integration bugs — wrong realm
handling, qop/nc/cnonce mishandling, or a client that silently falls back
to Basic and gets a 401 forever. Simulating it with Basic auth instead
would skip exactly the part most likely to break.

Simulator use only. Nothing here runs in the agent.
"""

from __future__ import annotations

import hashlib
import os
import re

ALGORITHM = "MD5"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def make_nonce() -> str:
    return _md5(os.urandom(16).hex())


def challenge(realm: str, nonce: str) -> str:
    return (f'Digest realm="{realm}", qop="auth", algorithm={ALGORITHM}, '
            f'nonce="{nonce}", opaque="{_md5(realm)}"')


def _parse(header: str) -> dict[str, str]:
    """Pull key=value pairs out of an Authorization header."""
    out: dict[str, str] = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', header):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def verify(auth_header: str | None, method: str, realm: str,
           users: dict[str, str], nonce: str) -> bool:
    """
    True if the Authorization header proves knowledge of a valid password.

    Deliberately does NOT enforce nonce freshness or nc replay: a
    simulator that rejects replays makes flaky tests, and replay
    protection is the device's problem, not the agent's.
    """
    if not auth_header or not auth_header.lower().startswith("digest "):
        return False

    p = _parse(auth_header[len("Digest "):])
    user = p.get("username", "")
    if user not in users:
        return False

    ha1 = _md5(f"{user}:{realm}:{users[user]}")
    ha2 = _md5(f"{method}:{p.get('uri','')}")

    if p.get("qop") in ("auth", "auth-int"):
        expected = _md5(f"{ha1}:{p.get('nonce', nonce)}:{p.get('nc','')}:"
                        f"{p.get('cnonce','')}:{p['qop']}:{ha2}")
    else:
        expected = _md5(f"{ha1}:{p.get('nonce', nonce)}:{ha2}")

    return expected == p.get("response", "")


class DigestMixin:
    """
    Mix into a BaseHTTPRequestHandler subclass.

    Set `realm`, `users` and `nonce` on the class. Call `authed()` at the
    top of do_GET; it sends the 401 challenge itself and returns False
    when the request should not proceed.
    """

    realm = "WatchLog-Sim"
    users: dict[str, str] = {"admin": "admin"}
    nonce = make_nonce()

    def authed(self) -> bool:
        if not self.users:                       # auth disabled
            return True
        if verify(self.headers.get("Authorization"), self.command,
                  self.realm, self.users, self.nonce):
            return True
        body = b"<html><body>401 Unauthorized</body></html>"
        self.send_response(401)
        self.send_header("WWW-Authenticate", challenge(self.realm, self.nonce))
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False
