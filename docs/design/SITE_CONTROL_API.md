# WatchLog Site Control API — Recorder Management command plane

**Status:** design / post‑M1 roadmap · **Author:** Vision Infinity engineering · **Date:** 2026‑09‑10

> Turns the manual NVR remediation done on the Al‑Khalid Dahua (SMD tuning, clock/DST/NTP,
> channel labels, health/video‑loss diagnosis) into a **first‑class, audited, permission‑gated
> capability** — so the portal, a support engineer, or an AI assistant can inspect / diagnose /
> configure / verify supported recorders **without ever opening an inbound port at the customer
> site and without any external system ever seeing the recorder credential.**

This is explicitly **post‑M1** and must not destabilise the gated M1 runtime. It is additive,
feature‑gated (per migration `0059` capability safety gate), and ships in read‑only form first.

---

## 1. Non‑negotiables (security model)

1. **The NVR stays private on the site LAN.** No inbound ports, no VPN, no port‑forward. The site
   agent remains **outbound‑only** (it already "never binds a socket").
2. **No external caller ever receives the recorder username/password.** The encrypted recorder
   credential lives only in the site agent (machine‑scoped DPAPI today). Callers send *intent*; the
   agent performs the action locally and returns only **before/after + verification**.
3. **Everything is a signed, audited command** with read‑before and read‑back‑after state.
4. **Three permission tiers** (Read / Recommend / Managed) — never unrestricted write.
5. **A hard deny‑list** of destructive/high‑risk operations that the control plane *cannot* express,
   regardless of tier or caller.

## 2. Architecture

```
Portal / Support console / AI assistant (Claude · ChatGPT · WatchLog assistant)
        │  (authenticated, site‑scoped, tier‑checked)
        ▼
WatchLog Management API  ──writes──►  Supabase command queue  (site_commands)
                                             │  (agent polls / realtime subscribe, outbound only)
                                             ▼
                                   specific WatchLog Site Agent  ── holds encrypted recorder cred
                                             │  lease/fence check (correct agent owns the site)
                                             ▼
                                   Dahua / Hikvision local API  (read · configure · verify)
                                             │
                                   result + before/after + audit  ──►  Supabase  ──►  caller
```

## 3. Maps onto primitives WatchLog already has

| Need | Existing WatchLog primitive |
| --- | --- |
| Which agent owns a site | `agents` (site‑scoped identity, `agent_key_hash`, enrollment) |
| Prevent two agents acting | `site_agent_leases` (lease + fencing token) |
| What an agent may do | capability advertise/enable split + `wl_platform_role`/`wl_platform_me` (mig `0059`) |
| Outbound‑only delivery | existing cloud sync / spool loop (agent already long‑polls cloud) |
| Recorder identity/model | `agents.device_vendor/device_model/device_driver` (e.g. `DH‑XVR1B08‑I`) |
| Audit surface | reuse the `*_transitions` / evidence‑transport patterns (mig `0058`) |

The new surface is **one queue table + one executor loop in the agent + a thin Management API**.

## 4. Command‑queue schema (new: `site_commands`)

```
id              uuid pk
tenant_id       uuid            -- RLS scope
site_id         uuid
agent_id        uuid            -- target agent (must match lease holder)
recorder_ref    text            -- which recorder at the site (future multi‑recorder)
action          text            -- from the capability catalog (§6)
params          jsonb           -- e.g. {"channel":2,"desired":{"human":true,"vehicle":false}}
tier            text            -- read | recommend | managed  (enforced server + agent side)
status          text            -- queued→claimed→preflight→executing→verified|failed|rolled_back
lease_fence     bigint          -- fencing token the claiming agent must still hold
result_before   jsonb           -- read‑before snapshot
result_after    jsonb           -- read‑back snapshot
verified        boolean
rollback_token  text            -- opaque handle to restore result_before
error           text
created_by      text            -- "Awais via AI session" / "support:…" / "portal:…"
created_at / claimed_at / completed_at   timestamptz
```

RLS: caller must be platform staff **or** tenant‑scoped with the right role; agent claims only rows
for its own `site_id` where it still holds `lease_fence`.

## 5. Command lifecycle (outbound‑only, verified, reversible)

```
created (Management API)
  → agent claims (atomic; checks lease + fence)
  → preflight        (reachability, model/driver match, tier allowed for this action)
  → read‑before      (snapshot exact config → result_before)
  → execute locally  (Dahua/Hikvision API, digest auth, local only)
  → read‑back        (re‑read → result_after)
  → verify           (after == desired?) → status=verified   else → auto rollback → rolled_back
  → signed result uploaded (before/after/verified/rollback_token + who/when)
```

Idempotent + reversible by construction. Mirrors the transactional installer discipline
(`wl-upgrade.ps1` preflight/commit/rollback) already proven on Windows.

## 6. Capability catalog

**Recorder layer** (agent → Dahua/Hikvision):

| action | tier | risk | notes |
| --- | --- | --- | --- |
| `inspect_recorder` / `get_recorder_health` | read | none | identity, model, channel map, video‑loss state |
| `get_recorder_clock` | read | none | current time, DST, NTP, timezone |
| `sync_recorder_time` | managed‑safe | low | DST‑off + set + NTP (the fix applied manually today) |
| `get_channels` / `rename_channel` | read / managed‑safe | low | OSD title sync (note: Dahua rejects `&` `'`) |
| `get_analytics_config` / `configure_smd` | read / managed‑safe | low‑med | SMD Human/Vehicle/sensitivity per channel |
| `get_ivs_rules` / `configure_ivs_rule` | read / recommend | med | **capability‑probed** — entry XVRs (XVR1B) have no IVS |
| `get_recording_status` / `get_storage_status` | read | none | HDD health, record mode |
| `request_snapshot` / `test_camera` | read | none | per‑channel frame + video‑loss/blind classification |
| `verify_configuration` / `restore_configuration` | read / managed | med | drift check; restore from `result_before` |

**WatchLog layer** (cloud‑side, not the recorder):

| action | tier | notes |
| --- | --- | --- |
| `set_camera_purpose` | managed | indoor/entrance/restricted → drives analytics expectations |
| `set_operating_schedule` | managed | office hours → powers after‑hours logic |
| `configure_watchlog_rule` / `set_incident_policy` | managed | incident/activity/after‑hours/evidence policy |

Recorder settings and WatchLog incident logic are **different layers**; the customer experiences
them as one product.

## 7. Permission tiers

- **Read** — automatic. Inspect recorder, channels, analytics, clock, health, capabilities. No writes.
- **Recommend** — AI/portal *proposes* diffs with rationale + confidence ("Vehicle enabled on an
  indoor camera → disable"). A human approves before anything is sent.
- **Managed** — a whitelisted set of **known‑safe** actions may auto‑apply within policy:
  clock/NTP correction, camera‑title sync, known‑safe SMD, WatchLog rule config. Always
  read‑back‑verified, always reversible, always audited.

**Hard deny‑list (never expressible by the control plane, any tier, any caller):** firmware update,
user/account changes, network config, deleting/formatting recordings or storage, factory reset,
password changes.

## 8. AI / external access surface

```
GET  /api/sites
GET  /api/sites/{site_id}/agents
GET  /api/agents/{agent_id}/recorders
GET  /api/recorders/{recorder_id}/configuration
POST /api/recorders/{recorder_id}/actions   → enqueues a site_command, returns command id
GET  /api/commands/{id}                      → status + before/after + rollback_token
```

The AI sends intent only, e.g. `{"action":"configure_smd","channel":2,"desired":{"human":true,"vehicle":false}}`
and receives `{"status":"verified","before":{…},"after":{…},"rollback_available":true}`. It never
sees credentials and can only act within its granted tier. A future MCP server wraps these same
endpoints so any assistant operates the **structured API**, not recorder web pages or screenshots.

## 9. Portal "Diagnose site" (same plane, human‑facing)

One button fans out read‑tier commands: recorder reachable? clock drift? recording on? disks healthy?
camera signal (video‑loss)? camera names? SMD config? analytics/purpose mismatch? event volume
abnormal? agent version? cloud connectivity? config drift? → returns e.g.:

```
3 issues found
• Reception & Main Entrance — Vehicle detection enabled on an indoor camera   [Fix safe]
• Armory — no usable image (video‑loss 2h18m)                                  [Field visit]
• Recorder clock drift 7m42s                                                   [Fix safe]
```

"Fix safe issues" enqueues only managed‑safe commands.

## 10. Phased build (each phase shippable, feature‑gated)

1. **P1 — Read plane.** `site_commands` table + agent executor (read actions only) + `inspect_recorder`,
   `get_recorder_health`, `get_recorder_clock`, `get_channels`, `get_analytics_config`,
   `request_snapshot`. Powers portal "Diagnose site" read‑only. *Lowest risk; highest immediate value.*
2. **P2 — Managed‑safe writes.** `sync_recorder_time`, `rename_channel`, `configure_smd` with
   read‑before/read‑back/rollback + full audit. (These are exactly today's manual fixes, productised.)
3. **P3 — Portal "Diagnose site / Fix safe issues"** + Recommend‑tier diffs with confidence.
4. **P4 — Hikvision driver** behind the same vendor‑neutral action interface (ISAPI).
5. **P5 — AI/MCP surface** exposing P1–P3 to Claude/ChatGPT/WatchLog assistant under tiers.

## 11. Guardrails vs M1

- Additive only; the M1‑gated agent runtime, installer and portal copy are untouched until this is
  separately built + CI/SG‑gated.
- Ships behind the `0059` capability gate: agents **advertise** the control capability; an owner/admin
  **enables** it per site. Advertise ≠ enable.
- Managed tier defaults **off** per site until the customer opts in.
