# Capability-aware UX, AI config skill, and calibration tooling

Design for three related pieces that sit on top of the now-built capability model
(`recorder_capabilities`, `wl_recorder_capability`/`wl_recorder_profile`), Site Control
(`wl_site_command_*`), and the intelligence pipeline (`incident_policies`,
`wl_daily_intelligence`). Grounded in shipped tables/RPCs; anything not yet built is marked
**PROPOSED**. Nothing here changes a customer recorder automatically.

---

## Item 23 — Capability-aware UX

**Rule:** the portal must never offer an analytic the attached recorder can't do, and must
never present an `unknown` verdict as either supported or unsupported. It reads the model,
it does not guess.

Per camera/recorder the portal resolves `wl_recorder_profile(vendor, model)` and renders each
capability by its **verdict × evidence_class**:

| verdict | evidence_class | UI treatment |
|---|---|---|
| `supported` | `FIELD_VERIFIED` | offer it, badge **Verified on your device** |
| `supported` | `OFFICIAL_DOCUMENTED` | offer it, badge **Per datasheet — not yet field-tested** |
| `by_camera` | any | show as **Camera-side only** — configured on the camera, not the recorder |
| `unsupported` | FIELD/OFFICIAL | show greyed **Not available on this model**, with the reason (`constraints`) |
| `unknown` | `UNKNOWN` | **Unconfirmed** — never a toggle; a "Check my device" (Site Control read) CTA |

Hard UI rules (mirror the two structural rules in 0061): an `unknown` is never rendered as a
red "unsupported", and an `OFFICIAL_DOCUMENTED` capability is never labelled "verified". A
mutual-exclusion `constraints` string (e.g. Dahua AI-Mode `Face|IVS&SMD|SMD`, or Hik "MD2.0
and Perimeter cannot both be enabled") is surfaced as a radio group, not independent toggles.
Recorder jargon (SMD/CGI/NTP/IVS) stays behind an **Advanced** disclosure; the default copy is
plain ("detect people and vehicles", "keep the clock correct"). **Status: PROPOSED** (portal
work); the entire data backing exists.

---

## Item 10 — AI configuration skill

A guided, **capability-gated** flow that lets the operator (or an AI assistant acting for
them) turn a business goal into a safe recorder change, always through the existing command
plane — **AI never touches recorder credentials**, exactly as `SITE_CONTROL_API.md` requires.

Flow: goal → resolve capability → propose → human approve → agent applies transactionally → verify.

1. **Goal** in plain language ("stop the false vehicle alerts on the indoor cameras", "make
   the armory camera high-sensitivity after hours").
2. **Resolve** with `wl_recorder_capability` for the exact model. If verdict ≠ `supported`
   with `write=true` and `safety_class='safe_write'`, the skill **refuses and explains**
   (offering the software-defined analytic alternative — an `incident_policy` via item 21 —
   when the recorder can't do it natively, e.g. line-crossing on a Cooper-I).
3. **Propose** via `wl_site_command_propose_write` — which itself enforces the gate
   (verdict=supported ∧ write ∧ `safe_write` ∧ `FIELD_VERIFIED`). The proposal shows the
   before/after diff.
4. **Approve**: a human clicks approve (`wl_site_command_approve`). Nothing applies without it.
5. **Apply**: the site agent executes the transactional read→backup→diff→apply→read-back→
   verify→rollback path (`agent/site_control.py execute_write`).
6. **Confirm** back to the operator with the verified new state, and register the change as
   the drift baseline (item 9 `config_drift`) so a later manual reversal is flagged.

The skill's whole safety story is that it can only ever emit *proposals* against the gate; the
deny-list (firmware/users/network/delete-recordings/format/factory-reset) is unreachable from
it. **Status: PROPOSED** (skill packaging); every backing RPC + the agent executor are built
and tested.

---

## Item 25 — Report calibration tooling

Every promotion threshold is already **data, not code** — so calibration is editing rows, not
shipping a build:

- **Incident thresholds** — `incident_policies` (`after_hours_only`, `min_dwell_seconds`,
  `match_purpose_ilike`, severity). Authored per-site through item 21's tenant-guarded
  `wl_upsert_incident_policy`. Calibration = tune these against a real week.
- **Episode grouping** — `wl_derive_episodes(..., p_gap_seconds=600)`; **journey hop** —
  `wl_derive_journeys(..., p_hop_gap_seconds=300)`. Both are parameters, tunable per site.
- **After-hours window** — currently the fixed 08:00–19:00 in `wl_promote_incidents`.
  **PROPOSED:** move to a per-site `business_hours` (open/close, working days) row so a site
  with a night shift isn't drowned in "after-hours" incidents. This is the one calibration
  input that still needs a schema addition; everything else is already tunable.
- **Calibration loop (PROPOSED tooling):** a report-runner dry-run that, given a date range,
  replays `wl_daily_intelligence` under candidate thresholds and shows incident-count deltas,
  so a threshold is chosen against evidence before it goes live — never guessed.

Calibration needs **real field data** (a representative week from a live, upgraded agent), so
the loop is designed now and exercised once the site is online — that dependency is a field
gate, not a build gap.
