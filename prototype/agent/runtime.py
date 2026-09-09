"""Top-level agent intelligence runtime — the REAL composition the packaged agent uses.

The threaded loops in analytics_agent.py / watchlog_agent.py delegate their per-cycle work
here, so this is the single place that ties together:

  * the analytics engine (detection -> tracking -> primitives -> analytic events),
  * lease fencing (authoritative writes only when this agent holds the site lease),
  * evidence actions (dispatched when an exception rule fires, idempotent + restart-safe),
  * the archive background workload (bounded, lower priority than live monitoring).

It is fully injectable (cloud, engine, lease, actions, archive, snapshot, clock), so the whole
pipeline is exercised end to end with synthetic frames + a fake cloud in one test, instead of
only testing the modules in isolation.

Design invariants:
  * fenced(): an authoritative write happens only when authoritative() is true — feature OFF =>
    always true (single-agent, unchanged); ON => must hold the lease AND its generation must
    still be current, else the write is suppressed (fail-safe, no dual-active).
  * action dispatch is idempotent across restarts (a persisted (rule,dedupe)->timestamp guard +
    the rule cooldown), so an agent restart cannot re-request the same footage/still.
  * analytic events flow ONLY through the existing wl_ingest_analytic_events path (no second
    incident transport); operations incidents are derived server-side by the 0054 bridge.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _now():
    return time.time()


class AgentRuntime:
    # Minimum spacing between action dispatches for the same rule+camera+class, so a restart or a
    # repeated firing never re-requests the same footage/still. A configured cooldown widens it.
    ACTION_MIN_COOLDOWN = 120

    def __init__(self, *, cloud, state, engine, lease=None, actions=None, archive=None,
                 snapshot=None, dedup_path=None, log=lambda _m: None, clock=_now):
        self.cloud = cloud
        self.state = state
        self.engine = engine
        self.lease = lease                 # LeaseClient or None (multi-agent feature OFF)
        self.actions = actions             # ActionRuntime or None
        self.archive = archive             # ArchiveRuntime or None
        self.snapshot = snapshot           # snapshot(channel) -> bytes | None
        self.log = log
        self.clock = clock
        self.dedup_path = Path(dedup_path) if dedup_path else None
        self._fired = self._load_fired()   # {(rule_id, dedupe): last_dispatch_epoch}
        self._archive_busy = False         # one bounded archive task at a time
        self.suppressed_writes = 0         # observability: writes skipped by fencing

    # ---- lease / authority -------------------------------------------------
    def refresh_lease(self):
        if self.lease is not None:
            self.lease.refresh()

    def authoritative(self) -> bool:
        """May this agent make a single-authority write right now?
        Feature OFF -> always. ON -> hold the lease AND the generation must still be current."""
        if self.lease is None:
            return True
        return self.lease.is_authoritative() and self.lease.validate_generation()

    def fenced(self, name, write_fn):
        """Run an authoritative write only if authoritative; otherwise suppress (fail-safe)."""
        if not self.authoritative():
            self.suppressed_writes += 1
            self.log(f"{name}: standby/fenced — authoritative write suppressed")
            return None
        return write_fn()

    # ---- operations intelligence per frame ---------------------------------
    def _rule_index(self):
        idx = {}
        for cam in (self.engine.cameras or {}).values():
            for r in cam.get("rules", []):
                idx[str(r.get("id"))] = {**r, "_camera_id": cam.get("id"), "_channel": cam.get("channel")}
        return idx

    @staticmethod
    def _is_exception(rule) -> bool:
        return (rule.get("severity") in ("attention", "incident")
                or bool(rule.get("promote_incident"))
                or bool(rule.get("sensitive")) or bool(rule.get("review_required")))

    def on_frame(self, channel, detections, frame_size, when=None) -> list:
        """Detection -> engine -> analytic events. Dispatch actions for exception firings
        (idempotent + cooldown). Returns the events for the caller to spool/upload."""
        events = self.engine.process(channel, detections, frame_size, when)
        if events and (self.actions is not None):
            idx = self._rule_index()
            for ev in events:
                self._maybe_dispatch(ev, idx.get(str(ev.get("rule_id"))))
        return events

    def _maybe_dispatch(self, ev, rule):
        if not rule or not self._is_exception(rule):
            return
        acts = rule.get("actions") or []
        if not acts:
            return
        # Confidence gate — mirror the 0049 emitter EXACTLY (confidence_min IS NOT NULL
        # AND confidence IS NOT NULL AND confidence < confidence_min => gated out). A firing
        # the server would reject by confidence must not trigger evidence capture here either;
        # a missing threshold or missing confidence (e.g. an absence event) is NOT gated.
        cmin = rule.get("confidence_min")
        conf = (ev.get("metadata") or {}).get("confidence")
        if cmin is not None and conf is not None and float(conf) < float(cmin):
            return
        # COARSE key (rule + camera + object class), NOT the per-event dedupe (which carries a
        # timestamp). This is what makes action dispatch idempotent across a restart or a repeated
        # firing of the SAME condition: the same footage/still is not requested twice within the
        # cooldown window. Guarded by the rule cooldown, floored at ACTION_MIN_COOLDOWN.
        key = f"{ev.get('rule_id')}|{rule.get('_camera_id')}|{ev.get('object_class')}"
        cooldown = max(int(rule.get("cooldown_seconds") or 0), self.ACTION_MIN_COOLDOWN)
        now = self.clock()
        last = self._fired.get(key)
        if last is not None and (now - last) < cooldown:
            return                                   # idempotent within cooldown (survives restart)
        self._fired[key] = now
        self._persist_fired()
        try:
            self.actions.run(acts, channel=rule.get("_channel"), camera_id=rule.get("_camera_id"), incident=ev)
        except Exception as e:                       # noqa: BLE001 — an action failure never blocks the pipeline
            self.log(f"actions: dispatch failed for rule {ev.get('rule_id')}: {str(e)[:120]}")

    # ---- fenced analytic-event upload (existing transport only) -------------
    def upload_analytics(self, spool, batch=500) -> int:
        ids, rows = spool.take(batch)
        if not ids:
            return 0
        if not self.authoritative():
            self.suppressed_writes += 1
            self.log("analytics: standby/fenced — analytic-event upload suppressed")
            return 0
        res = self.cloud.call("wl_ingest_analytic_events", p_agent_id=self.state["agent_id"],
                              p_agent_key=self.state["agent_key"], p_events=rows) or {}
        spool.ack(ids)
        return int(res.get("inserted") or 0)

    # ---- archive background workload (bounded, lower priority) --------------
    def poll_archive(self, limit=1) -> list:
        """Claim + process at most one bounded archive scan per call. Never runs concurrently
        with itself, so it cannot starve live monitoring."""
        if self.archive is None or self._archive_busy:
            return []
        self._archive_busy = True
        try:
            return self.archive.poll_and_process(limit=limit)
        finally:
            self._archive_busy = False

    # ---- restart-safe action dedup persistence -----------------------------
    def _load_fired(self) -> dict:
        if not self.dedup_path or not self.dedup_path.exists():
            return {}
        try:
            data = json.loads(self.dedup_path.read_text(encoding="utf-8"))
            return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception:                            # noqa: BLE001
            return {}

    def _persist_fired(self):
        if not self.dedup_path:
            return
        # keep the guard bounded and recent (24h) so it can't grow without limit
        cutoff = self.clock() - 86400
        self._fired = {k: v for k, v in self._fired.items() if v >= cutoff}
        try:
            self.dedup_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.dedup_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._fired), encoding="utf-8")
            tmp.replace(self.dedup_path)
        except OSError:
            pass
