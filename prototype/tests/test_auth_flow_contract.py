#!/usr/bin/env python3
"""Static contract for production-safe WatchLog Supabase Auth flows."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal" / "app"

signup = (PORTAL / "signup" / "page.js").read_text(encoding="utf-8")
login = (PORTAL / "login" / "page.js").read_text(encoding="utf-8")
forgot = (PORTAL / "forgot-password" / "page.js").read_text(encoding="utf-8")
confirm = (PORTAL / "auth" / "confirm" / "page.js").read_text(encoding="utf-8")
reset = (PORTAL / "auth" / "reset" / "page.js").read_text(encoding="utf-8")

assert "emailRedirectTo: confirmRedirect()" in signup
assert "window.location.origin" in signup
assert "/auth/confirm/" in signup
assert ".auth.resend" in signup

assert ".auth.resend" in login
assert "/forgot-password/" in login
assert "/auth/confirm/" in login

assert "resetPasswordForEmail" in forgot
assert "redirectTo: resetRedirect()" in forgot
assert "/auth/reset/" in forgot

assert "otp_expired" in confirm
assert "getSession" in confirm
assert "otp_expired" in reset
assert "PASSWORD_RECOVERY" in reset
assert "updateUser({ password })" in reset

# Production auth redirects must derive from the deployed browser origin. A
# localhost Site URL may remain in Supabase's development allow-list, but it
# must never be hard-coded into customer-facing portal auth code.
for path in [
    PORTAL / "signup" / "page.js",
    PORTAL / "login" / "page.js",
    PORTAL / "forgot-password" / "page.js",
    PORTAL / "auth" / "confirm" / "page.js",
    PORTAL / "auth" / "reset" / "page.js",
]:
    text = path.read_text(encoding="utf-8").lower()
    assert "localhost" not in text, f"hard-coded localhost auth redirect in {path}"

print("auth flow contract: PASS")
