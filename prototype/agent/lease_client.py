"""Multi-agent single-authority lease client (wires the agent to migration 0052).

FEATURE-GATED OFF by default: with the site flag off the agent is trivially authoritative
(single-agent mode, generation 0) and never touches the lease — behaviour is unchanged.

When the site opts in, the agent must PROVE it holds the site lease before making any
authoritative write (health reconciliation, event ingestion). The rules, in priority order:

  * acquire/renew the lease each cycle; a granted 'primary' lease makes this agent authoritative
    and yields a monotonic fencing `generation`;
  * a 'standby' result (another live primary) is NOT authoritative;
  * before an authoritative write, `validate_generation()` re-confirms this generation is still
    the fenced authority — a superseded generation (after a takeover) is fenced out and stops;
  * FAIL SAFE: if authority cannot be PROVEN on an enabled site (lease call failed, or we were
    superseded), the agent is not authoritative and must not produce duplicate health/events.

This client holds no timers of its own; the agent loop calls refresh()/validate_generation()
on its existing cadence. It never opens an inbound socket and never blocks on the network path
beyond the single RPC.
"""

from __future__ import annotations


class LeaseClient:
    def __init__(self, cloud, agent_id, agent_key, feature_enabled=False,
                 lease_seconds=90, log=lambda _m: None):
        self.cloud = cloud
        self.agent_id = agent_id
        self.agent_key = agent_key
        self.lease_seconds = int(lease_seconds)
        self.log = log
        self.feature_enabled = bool(feature_enabled)
        # single-agent (feature off) -> authoritative; enabled -> must prove before acting.
        self.authority = not self.feature_enabled
        self.mode = "single_agent" if not self.feature_enabled else "unproven"
        self.generation = 0
        self.lease_expires_at = None
        self.last_error = None

    # -- configuration --------------------------------------------------------
    def set_feature_enabled(self, enabled) -> None:
        """The config poll can flip the site flag. Disabling restores single-agent authority;
        enabling drops authority until a lease is proven (never assume authority on enable)."""
        enabled = bool(enabled)
        if enabled == self.feature_enabled:
            return
        self.feature_enabled = enabled
        if not enabled:
            self.authority, self.mode, self.generation = True, "single_agent", 0
        else:
            self.authority, self.mode = False, "unproven"

    # -- lease lifecycle ------------------------------------------------------
    def refresh(self) -> bool:
        """Acquire or renew the lease. Returns whether this agent is authoritative now."""
        if not self.feature_enabled:
            self.authority, self.mode, self.last_error = True, "single_agent", None
            return True
        try:
            res = self.cloud.call("wl_agent_acquire_lease",
                                  p_agent_id=self.agent_id, p_agent_key=self.agent_key,
                                  p_lease_seconds=self.lease_seconds) or {}
            self.mode = res.get("mode") or "unproven"
            self.generation = int(res.get("generation") or 0)
            self.lease_expires_at = res.get("lease_expires_at")
            self.authority = bool(res.get("granted")) and self.mode == "primary"
            self.last_error = None
            if not self.authority:
                self.log(f"lease: standby (holder generation {self.generation}); "
                         "not authoritative, no authoritative writes")
        except Exception as e:                                       # noqa: BLE001 — fail safe
            self.authority, self.mode = False, "unproven"
            self.last_error = str(e)
            self.log(f"lease: authority unproven ({str(e).splitlines()[0][:100]}); "
                     "suppressing authoritative writes (fail-safe)")
        return self.authority

    def validate_generation(self) -> bool:
        """Re-confirm THIS generation is still the fenced authority right before an
        authoritative write. A superseded generation returns False and stops writing."""
        if not self.feature_enabled:
            return True
        if not self.authority:
            return False
        try:
            ok = bool(self.cloud.call("wl_agent_is_fenced_authority",
                                      p_agent_id=self.agent_id, p_generation=self.generation))
        except Exception as e:                                       # noqa: BLE001 — fail safe
            self.last_error = str(e)
            self.authority = False
            return False
        if not ok:
            self.authority, self.mode = False, "superseded"
            self.log(f"lease: generation {self.generation} superseded by a takeover; "
                     "stopping authoritative writes")
        return ok

    def is_authoritative(self) -> bool:
        return self.authority

    def release(self) -> None:
        if not self.feature_enabled:
            return
        try:
            self.cloud.call("wl_agent_release_lease",
                            p_agent_id=self.agent_id, p_agent_key=self.agent_key)
        except Exception:                                           # noqa: BLE001
            pass
        self.authority, self.mode, self.generation = False, "released", 0

    def status(self) -> dict:
        return {"feature_enabled": self.feature_enabled, "authoritative": self.authority,
                "mode": self.mode, "generation": self.generation,
                "lease_expires_at": self.lease_expires_at}
