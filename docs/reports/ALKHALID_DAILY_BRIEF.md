# WatchLog Daily Office Intelligence — Al‑Khalid Main Site

**Status:** design + real‑data proof · **Date:** 2026‑09‑10 · Site `588cb40a` (tenant Al‑Khalid)

> The product for Al‑Khalid is **not** "202 person events." It is a daily brief that explains, in
> business language, what happened at the office — and that is honest about confidence. This doc
> specifies that brief, proves the computable parts on **today's real event stream**, and marks
> exactly what needs more signal.

The brief is delivered through the **existing** report pipeline (report runner → n8n → Evolution
WhatsApp; `report_recipients`/`report_deliveries`). One recipient is already configured for the
tenant — so this also finally lights up M1 contractual item 7 (daily WhatsApp summary), which has
delivered 0 messages to date.

---

## 1. Six focus areas (the first version)

1. **Opening / closing** — when the office effectively opened and closed.
2. **Staff‑presence estimate** — *estimated regular staff*, never named attendance.
3. **Visitors & dwell time** — journeys and how long people stayed.
4. **Restricted‑area / armory activity** — access episodes, after‑hours, camera health.
5. **After‑hours activity** — anything outside office hours.
6. **CCTV system health** — recorder/agent/clock/camera‑signal state.

## 2. The intelligence model: Events → Journeys → Insights

```
person @ Reception 14:03  ─┐
person @ Admin/Dir 14:04   ├─►  Journey: entered Reception → reached management area →
person @ Admin/Dir 14:18   │        stayed ~15m → returned → exited        (1 visitor)
person @ Reception 14:19  ─┘
                                  ►  Insight: "18 visitor journeys; 4 reached management;
                                              1 stayed after closing"
```

Raw per‑camera `person` counts must **not** be summed as people (the same person crosses several
cameras). Grouping needs a short‑window cross‑camera model (timing + direction + appearance
similarity + camera topology), with **confidence surfaced** ("18 unique — high on 15, moderate on 3").

## 3. Real proof from today's stream (honest coverage)

Computed live from `events` for site `588cb40a`, Asia/Karachi:

- **Coverage today: 14:18 → 15:28 (~70 min).** The agent only reconnected as v0.4.1 mid‑afternoon,
  so a full opening/closing brief is **not yet possible** — this window proves the engine, not a full day.
- person = **202**, vehicle = **1** (pre‑fix; none after the 15:00 SMD fix).
- Per camera / access episodes (>10 min gap = new episode):

  | Camera | events | episodes | window |
  | --- | --- | --- | --- |
  | Reception & Main Entrance | 68 | 1 (continuous) | 14:18–15:28 |
  | Director's Office | 64 | 1 | 14:18–15:28 |
  | Admin Manager & Director Entrance | 43 | 1 | 14:19–15:27 |
  | Armory Gate | 27 | **2 access windows** | 14:19–15:26 |
- After‑hours events: **none**. Peak hour: 14:00 (130), then 15:00 (72).

So today: episode detection, per‑area activity, after‑hours screening, and health all compute
correctly on real data. Headcount / unique visitors / staff‑vs‑visitor do **not** yet.

## 4. Metric catalog — feasibility in the next 24h

Assuming the agent runs a **full day** from tomorrow:

| Insight | Next 24h? | Source / gap |
| --- | --- | --- |
| First arrival · final departure | ✅ | min/max `device_ts` today |
| Approx office opening / closing | ✅ | state machine (§5) over sustained occupancy |
| Hourly entrance traffic | ✅ | histogram over Reception |
| Per‑area activity + access episodes | ✅ | proven above |
| Peak activity hour | ✅ | proven above |
| Armory‑gate access episodes | ✅ | proven above (2 today) |
| After‑hours activity | ✅ | proven above (none today) |
| Camera / NVR health, clock, agent state | ✅ | `camera_health` + recorder video‑loss + agent last_seen |
| False vehicle suppression | ✅ | SMD fix already applied |
| Estimated unique visitors (with confidence) | 🟡 | needs cross‑camera journey model (timing/direction/appearance) |
| Visitor dwell time / journeys | 🟡 | same model |
| Staff vs visitor | 🟡 | behavioral heuristic (§6), estimate only |
| Exact employee vs visitor · named attendance | ❌ | needs roster / access‑control / attendance integration |
| Armory *internal* activity | 🟡 | blocked on Ch5 video‑loss repair |

## 5. Opening / closing — state machine (not "first motion")

```
CLOSED ──first entrance──► OPENING ──sustained internal occupancy──► OPEN
OPEN ──final exits──► WINDING_DOWN ──quiet ≥N min, no verified indoor presence──► CLOSED
```
Report as *derived*: "Opened 08:51 (first arrival + sustained internal occupancy)", "Closed 18:39
(final departure + 20 min without verified indoor presence)".

## 6. Staff vs visitor — behavioral first, no face recognition

- **Probable regular staff:** enters via Reception, present for hours, recurs on internal cameras,
  follows the workday, appears across multiple days.
- **Probable visitor:** enters via Reception, present 5–30 min, may reach Ch2/Ch4, then exits.

Report "*estimated* regular staff: 9 / visitor journeys: 18" — never a certain named count. Named
attendance is a later, opt‑in roster/attendance integration (more defensible than face recognition).

## 7. Armory is reported differently (Ch3 gate, Ch5 room)

Not ordinary office cameras. Report: access‑episode count, first/last access, **after‑hours entry**,
unusually long presence, repeated short‑interval access, and **camera health/video‑loss/tamper**.
Today Ch3 Armory Gate = 2 access windows, no after‑hours; **Ch5 Armory = video‑loss (image
unavailable) → flagged for physical repair before analytics.**

## 8. Sample brief (shape — figures illustrative except where marked real)

```
WatchLog Daily Brief — Al‑Khalid Security Office · Wed 10 Sep

Office day     Opened 08:51 · Closed 18:39 · active 9h48m         [needs full‑day coverage]
People         ~18 visitor journeys · ~9 estimated staff · peak 14 @ 12:26   [needs journey model]
Visitors       13 short · 4 medium · 1 extended · median 12m · 3 reached management
Restricted     Armory Gate: 2 access episodes (REAL today) · after‑hours: none (REAL)
Security       1 camera‑health issue: Armory image unavailable (REAL) · vehicle false‑alarms: fixed (REAL)
System         Recorder connected · clock synchronized (REAL) · 4/5 monitored cameras OK (REAL)
Assessment     Normal activity. No after‑hours armory access. Restore the Armory camera.
```

## 9. Build phases

1. **v0 (data we have): counts, access episodes, hourly traffic, after‑hours screen, health, clock,
   vehicle‑suppression** — computable now; wire into the report runner → WhatsApp. Fires the first
   real daily delivery (M1 item 7). *Label unique‑visitor/staff as "coming next", don't fake them.*
2. **v1: cross‑camera journeys + unique‑visitor estimate with confidence** (short‑window model).
3. **v2: behavioral staff‑vs‑visitor estimate.**
4. **v3: opening/closing state machine tuned on a week of data.**
5. **v4: optional roster/attendance integration → named attendance.**

## 10. Honesty guardrails

- Every estimated figure carries a confidence and the word "estimated"; never claim named identity
  without an identity source.
- Coverage window is always stated; a partial‑day brief says so (today = 70 min).
- Armory internal activity stays "unavailable" until Ch5 video is restored.
