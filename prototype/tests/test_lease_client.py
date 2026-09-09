#!/usr/bin/env python3
"""Multi-agent lease client tests: feature gating, single authority, standby, fencing-
generation validation, failover after expiry, superseded-agent stops writes, fail-safe on
lease-call failure, and clean recovery. A FakeLeaseServer models the 0052 DB semantics so two
clients race realistically (the DB side itself is proven in e2e_multiagent_pg.py)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from lease_client import LeaseClient  # noqa: E402


class FakeLeaseServer:
    """In-memory model of migration 0052: one holder, monotonic fencing generation,
    lease expiry, takeover bumps the generation."""
    def __init__(self, enabled=True, lease_seconds=90):
        self.enabled = enabled
        self.lease_seconds = lease_seconds
        self.holder = None
        self.generation = 0
        self.expires_at = 0.0
        self.clock = 0.0

    def tick(self, dt):
        self.clock += dt

    def acquire(self, agent, lease_seconds):
        if not self.enabled:
            return {"granted": True, "generation": 0, "mode": "single_agent", "holder_agent_id": agent}
        expired = self.holder is None or self.clock >= self.expires_at
        if self.holder is not None and self.holder != agent and not expired:
            return {"granted": False, "generation": self.generation, "mode": "standby",
                    "holder_agent_id": self.holder, "lease_expires_at": self.expires_at}
        gen = self.generation if (self.holder == agent and not expired) else self.generation + 1
        self.holder, self.generation, self.expires_at = agent, gen, self.clock + lease_seconds
        return {"granted": True, "generation": gen, "mode": "primary", "holder_agent_id": agent,
                "lease_expires_at": self.expires_at}

    def is_authority(self, agent, generation):
        if not self.enabled:
            return True
        return self.holder == agent and self.generation == generation and self.clock < self.expires_at

    def release(self, agent):
        if self.holder == agent:
            self.holder, self.expires_at = None, self.clock


class FakeCloud:
    def __init__(self, server, fail=False):
        self.server, self.fail = server, fail

    def call(self, fn, **kw):
        if self.fail:
            raise RuntimeError("network down")
        if fn == "wl_agent_acquire_lease":
            return self.server.acquire(kw["p_agent_id"], kw["p_lease_seconds"])
        if fn == "wl_agent_is_fenced_authority":
            return self.server.is_authority(kw["p_agent_id"], kw["p_generation"])
        if fn == "wl_agent_release_lease":
            self.server.release(kw["p_agent_id"]); return {"released": True}
        raise KeyError(fn)


class LeaseTests(unittest.TestCase):
    def test_feature_off_is_single_agent_authority(self):
        c = LeaseClient(cloud=None, agent_id="A", agent_key="k", feature_enabled=False)
        self.assertTrue(c.refresh())
        self.assertTrue(c.is_authoritative())
        self.assertEqual("single_agent", c.mode)
        self.assertEqual(0, c.generation)
        self.assertTrue(c.validate_generation())     # no cloud call needed when off

    def test_primary_standby_and_renew(self):
        s = FakeLeaseServer()
        A = LeaseClient(FakeCloud(s), "A", "k", feature_enabled=True, lease_seconds=90)
        B = LeaseClient(FakeCloud(s), "B", "k", feature_enabled=True, lease_seconds=90)
        self.assertTrue(A.refresh())
        self.assertEqual(("primary", 1), (A.mode, A.generation))
        self.assertFalse(B.refresh())                 # A holds -> B standby
        self.assertEqual("standby", B.mode)
        s.tick(10)
        self.assertTrue(A.refresh())                  # renew, generation unchanged
        self.assertEqual(1, A.generation)

    def test_failover_and_fencing(self):
        s = FakeLeaseServer(lease_seconds=90)
        A = LeaseClient(FakeCloud(s), "A", "k", feature_enabled=True, lease_seconds=90)
        B = LeaseClient(FakeCloud(s), "B", "k", feature_enabled=True, lease_seconds=90)
        A.refresh()
        self.assertTrue(A.validate_generation())      # A(gen1) authoritative
        s.tick(120)                                   # A's lease expires
        self.assertTrue(B.refresh())                  # B takes over
        self.assertEqual(("primary", 2), (B.mode, B.generation))
        # fencing: A's stale generation is fenced out -> A stops authoritative writes; no dual-active
        self.assertFalse(A.validate_generation())
        self.assertFalse(A.is_authoritative())
        self.assertEqual("superseded", A.mode)
        self.assertTrue(B.validate_generation())

    def test_fail_safe_when_lease_unproven(self):
        s = FakeLeaseServer()
        down = FakeCloud(s, fail=True)
        A = LeaseClient(down, "A", "k", feature_enabled=True, lease_seconds=90)
        self.assertFalse(A.refresh())                 # cannot prove authority -> suppress
        self.assertFalse(A.is_authoritative())
        self.assertFalse(A.validate_generation())

    def test_clean_recovery_after_release(self):
        s = FakeLeaseServer(lease_seconds=90)
        A = LeaseClient(FakeCloud(s), "A", "k", feature_enabled=True, lease_seconds=90)
        B = LeaseClient(FakeCloud(s), "B", "k", feature_enabled=True, lease_seconds=90)
        A.refresh()
        s.tick(120); B.refresh()                      # B takes over (gen 2)
        B.release()                                    # B leaves
        self.assertTrue(A.refresh())                  # A re-acquires
        self.assertEqual(("primary", 3), (A.mode, A.generation))

    def test_enable_drops_authority_until_proven(self):
        c = LeaseClient(cloud=FakeCloud(FakeLeaseServer()), agent_id="A", agent_key="k", feature_enabled=False)
        self.assertTrue(c.is_authoritative())         # single-agent
        c.set_feature_enabled(True)                    # site turns multi-agent ON
        self.assertFalse(c.is_authoritative())         # must prove a lease first
        self.assertTrue(c.refresh())                   # now proven
        c.set_feature_enabled(False)                   # back to single-agent
        self.assertTrue(c.is_authoritative())


if __name__ == "__main__":
    unittest.main(verbosity=1)
