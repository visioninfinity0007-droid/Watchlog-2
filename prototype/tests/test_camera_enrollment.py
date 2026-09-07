#!/usr/bin/env python3
"""End-to-end camera-enrollment tests — driven by the REAL client field failure.

Root cause (forensic, 0.3.4 field archive + production read-only checks): setup skipped
`wl_enroll` whenever a local `agent_state.json` existed, ignored the freshly-entered site
code, and ran `wl_sync_cameras` against a stale/defunct agent -> generic "cameras could not
be added". Production evidence: the client's code was never consumed and the target site had
0 agents / 0 cameras.

These tests pin the corrected identity+sync flow with a faithful in-memory cloud:
  * a supplied UNUSED code is honoured first (enroll -> correct site binding, incl. moving a
    PC from site A to site B),
  * a SPENT/invalid code falls back to the existing identity only if it still authenticates
    (heartbeat) — never reuse a defunct agent,
  * camera sync is idempotent (server ON CONFLICT) and never lies about success,
  * failures raise a classified AgentSyncError (never the misleading "site linked" catch-all).
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import credential_store            # noqa: E402
import watchlog_agent as core      # noqa: E402
import setup_backend as sb         # noqa: E402

DEVICE = types.SimpleNamespace(vendor="Dahua", model="NVR", driver="dahua")
EIGHT = [{"channel": str(i), "name": f"Channel{i}"} for i in range(1, 9)]


class FakeCloud:
    """Faithful stand-in for the four SECURITY DEFINER RPCs (wl_enroll / wl_heartbeat /
    wl_sync_cameras / wl_agent_bootstrap_analytics + wl_sync_capabilities)."""
    def __init__(self):
        self.codes = {}      # code -> {"site_id","tenant_id","used_by"}
        self.agents = {}     # agent_id -> {"key","site_id","tenant_id"}
        self.cameras = {}    # site_id -> {channel: cam_uuid}
        self.sites = set()   # existing site ids (for FK simulation)
        self.drop = set()    # channels the server "fails" to create (partial-result simulation)
        self._n = 0

    def add_code(self, code, site_id, tenant_id="ten-1"):
        self.codes[code] = {"site_id": site_id, "tenant_id": tenant_id, "used_by": None}
        self.sites.add(site_id)

    def add_agent(self, agent_id, key, site_id, tenant_id="ten-1", site_exists=True):
        self.agents[agent_id] = {"key": key, "site_id": site_id, "tenant_id": tenant_id}
        if site_exists:
            self.sites.add(site_id)

    def _err(self, status, code, message):
        return core.CloudError("rpc", status, code, message)

    def call(self, fn, **p):
        if fn == "wl_enroll":
            c = self.codes.get(p["p_code"])
            if not c or c["used_by"] is not None:
                raise self._err(400, "22023", "enrollment code rejected: unknown, already used, or expired")
            self._n += 1
            aid, key = f"agent-{self._n}", f"key-{self._n}"
            c["used_by"] = aid
            self.agents[aid] = {"key": key, "site_id": c["site_id"], "tenant_id": c["tenant_id"]}
            return {"agent_id": aid, "agent_key": key, "site_id": c["site_id"],
                    "tenant_id": c["tenant_id"], "server_time": "t"}
        a = self.agents.get(p.get("p_agent_id"))
        authed = a is not None and a["key"] == p.get("p_agent_key")
        if fn == "wl_heartbeat":
            if not authed:
                raise self._err(403, "28000", "agent not recognised")
            return {"ok": True, "server_time": "t"}
        if fn == "wl_sync_cameras":
            if not authed:
                raise self._err(403, "28000", "agent not recognised")
            if a["site_id"] not in self.sites:      # dangling site (deleted) -> FK violation
                raise self._err(400, "23503", 'insert or update on table "cameras" violates foreign key constraint')
            book = self.cameras.setdefault(a["site_id"], {})
            for cam in (p.get("p_cameras") or []):
                ch = str(cam.get("channel") or "").strip()
                if ch and ch not in self.drop:                        # self.drop simulates a partial create
                    book.setdefault(ch, f"cam-{a['site_id']}-{ch}")   # ON CONFLICT (site,channel) idempotent
            return dict(book)
        if fn in ("wl_agent_bootstrap_analytics", "wl_sync_capabilities"):
            if not authed:
                raise self._err(403, "28000", "agent not recognised")
            return {"ok": True}
        raise self._err(404, "PGRST202", f"Could not find the function {fn}")


def _mem_state(monkeypatch):
    """Platform-independent identity round-trip. The real core.save_state/load_state split
    the bearer key into the Windows DPAPI store (only on os.name=='nt'); that storage is
    covered by the Windows Security Gate. These logic tests inject a simple in-memory store
    so they run identically on Windows and the Linux CI runner."""
    store = {}
    monkeypatch.setattr(core, "save_state", lambda path, state: store.__setitem__(str(path), dict(state)))
    monkeypatch.setattr(core, "load_state", lambda path: (dict(store[str(path)]) if str(path) in store else None))
    return store


# --- 1. fresh site + successful camera creation -----------------------------
def test_fresh_enroll_and_create_cameras(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-FRESH01", "site-A")
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-FRESH01", DEVICE)
    assert ident["site_id"] == "site-A" and cloud.codes["WL-FRESH01"]["used_by"] == ident["agent_id"]
    mapping = sb.sync_cameras(cloud, ident, EIGHT)
    assert len(mapping) == 8


# --- 2. site already linked + camera retry succeeds -------------------------
def test_retry_after_enroll_reuses_identity(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-RETRY01", "site-B")
    sp = tmp_path / "agent_state.json"
    first = sb.establish_identity(cloud, sp, "WL-RETRY01", DEVICE)      # consumes code
    # user presses Retry: same (now consumed) code, existing valid agent -> reuse, no re-enroll
    again = sb.establish_identity(cloud, sp, "WL-RETRY01", DEVICE)
    assert again["agent_id"] == first["agent_id"]
    assert len(cloud.agents) == 1                                       # NOT a duplicate agent
    assert len(sb.sync_cameras(cloud, again, EIGHT)) == 8


# --- 3. one camera already exists + reconciliation does not duplicate -------
def test_reconcile_does_not_duplicate(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-RECON01", "site-C")
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-RECON01", DEVICE)
    sb.sync_cameras(cloud, ident, EIGHT[:1])       # channel 1 already created
    mapping = sb.sync_cameras(cloud, ident, EIGHT)  # full set
    assert len(mapping) == 8 and len(cloud.cameras["site-C"]) == 8


# --- 4. partial previous creation + Retry completes remaining ---------------
def test_partial_then_retry_completes(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-PART01", "site-D")
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-PART01", DEVICE)
    sb.sync_cameras(cloud, ident, EIGHT[:3])       # 3 created
    mapping = sb.sync_cameras(cloud, ident, EIGHT)  # rest reconciled
    assert len(mapping) == 8


# --- 5. cloud sync fails -> classified error, never a false success ---------
def test_sync_failure_is_not_false_success(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-FK01", "site-E")
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-FK01", DEVICE)
    cloud.sites.discard("site-E")                  # site deleted -> FK violation on insert
    try:
        sb.sync_cameras(cloud, ident, EIGHT)
        assert False, "must not report success when the cloud rejects the cameras"
    except sb.AgentSyncError as e:
        assert e.category == "CAMERA_SYNC_CONSTRAINT_FAILED"


# --- 6. recorder returned no channels -> CAMERA_ENUMERATION_FAILED ----------
def test_no_channels_is_enumeration_failure(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-EMPTY01", "site-F")
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-EMPTY01", DEVICE)
    try:
        sb.sync_cameras(cloud, ident, [])
        assert False
    except sb.AgentSyncError as e:
        assert e.category == "CAMERA_ENUMERATION_FAILED"


# --- 7. camera auth failure -> classified (not a recorder-password error) ---
def test_sync_auth_failure_classified(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud()
    ident = {"agent_id": "ghost", "agent_key": "nope", "site_id": "site-G", "tenant_id": "ten-1"}
    try:
        sb.sync_cameras(cloud, ident, EIGHT)
        assert False
    except sb.AgentSyncError as e:
        assert e.category == "CAMERA_SYNC_AUTH_FAILED"
        assert "password" not in str(e).lower()     # must NOT blame the recorder credential


# --- 8. THE CLIENT REGRESSION: stale defunct agent + fresh unused code ------
def test_stale_state_reenrolls_with_supplied_code(tmp_path, monkeypatch):
    """Reproduces the field failure. Old agent_state.json on disk whose agent no longer
    authenticates; the operator enters a fresh unused site code. Before the fix, setup skipped
    enroll and synced against the stale agent (28000). After the fix, the supplied code enrolls
    a new correctly-bound identity and cameras are created."""
    store = _mem_state(monkeypatch)
    cloud = FakeCloud()
    cloud.add_code("WL-NEWSITE1", "site-REAL")                 # fresh, UNUSED
    # stale local identity for a defunct agent (not present in cloud) + a leftover key
    sp = tmp_path / "agent_state.json"
    core.save_state(sp, {"agent_id": "stale-old", "agent_key": "stale-key",
                         "tenant_id": "old-ten", "site_id": "site-OLD"})
    assert store[str(sp)]["agent_id"] == "stale-old"             # precondition: stale identity present
    ident = sb.establish_identity(cloud, sp, "WL-NEWSITE1", DEVICE)
    assert ident["site_id"] == "site-REAL"                      # bound to the NEW code's site
    assert ident["agent_id"] != "stale-old"
    assert cloud.codes["WL-NEWSITE1"]["used_by"] == ident["agent_id"]   # code now consumed
    assert store[str(sp)]["agent_id"] == ident["agent_id"]     # new identity persisted (overwrote stale)
    assert len(sb.sync_cameras(cloud, ident, EIGHT)) == 8


# --- 9. pressing Retry many times stays idempotent --------------------------
def test_retry_idempotent_many_times(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-IDEMP01", "site-H")
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-IDEMP01", DEVICE)
    for _ in range(4):
        ident = sb.establish_identity(cloud, sp, "WL-IDEMP01", DEVICE)
        assert len(sb.sync_cameras(cloud, ident, EIGHT)) == 8
    assert len(cloud.agents) == 1 and len(cloud.cameras["site-H"]) == 8


# --- 10. valid agent for site A must not hijack a site-B code (binding) -----
def test_site_binding_rebinds_to_new_code(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-SITEA01", "site-A"); cloud.add_code("WL-SITEB01", "site-B")
    sp = tmp_path / "agent_state.json"
    a = sb.establish_identity(cloud, sp, "WL-SITEA01", DEVICE)      # enrolled to site A (valid)
    # operator now sets this PC up for site B with a fresh code -> must rebind, not reuse site A
    b = sb.establish_identity(cloud, sp, "WL-SITEB01", DEVICE)
    assert b["site_id"] == "site-B" and b["agent_id"] != a["agent_id"]


# --- 11. code invalid/expired AND no valid identity -> actionable, not reuse -
def test_bad_code_and_no_identity_is_enroll_required(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud()                              # no codes, no agents
    sp = tmp_path / "agent_state.json"
    try:
        sb.establish_identity(cloud, sp, "WL-UNKNOWN9", DEVICE)
        assert False
    except sb.AgentSyncError as e:
        assert e.category == "ENROLL_REQUIRED"


# --- 12. spent code but stale defunct identity -> do NOT reuse defunct agent -
def test_spent_code_and_defunct_identity_errors(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud()
    cloud.codes["WL-SPENT001"] = {"site_id": "site-Z", "tenant_id": "ten-1", "used_by": "someone-else"}
    sp = tmp_path / "agent_state.json"
    core.save_state(sp, {"agent_id": "stale-old", "agent_key": "stale-key",
                         "tenant_id": "old-ten", "site_id": "site-OLD"})
    try:
        sb.establish_identity(cloud, sp, "WL-SPENT001", DEVICE)
        assert False, "must not silently reuse a defunct agent when the code is already used"
    except sb.AgentSyncError as e:
        assert e.category == "ENROLL_REQUIRED"


# --- 13. partial cloud result (created < discovered) -> raise, never false success -
def test_partial_result_raises_not_silent(tmp_path, monkeypatch):
    _mem_state(monkeypatch)
    cloud = FakeCloud(); cloud.add_code("WL-PARTIAL1", "site-P")
    cloud.drop = {"7", "8"}                          # cloud confirms only channels 1..6 of 8
    sp = tmp_path / "agent_state.json"
    ident = sb.establish_identity(cloud, sp, "WL-PARTIAL1", DEVICE)
    try:
        sb.sync_cameras(cloud, ident, EIGHT)
        assert False, "a partial camera result must raise, not silently return success"
    except sb.AgentSyncError as e:
        assert e.category == "CAMERA_SYNC_PARTIAL"
    # once the cloud confirms all channels, a retry reconciles to full success
    cloud.drop = set()
    assert len(sb.sync_cameras(cloud, ident, EIGHT)) == 8


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
