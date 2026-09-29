# Method: Counting People

**Customer status:** WatchLog reports people seen per camera and time, busy periods and presence at key areas. It does not count unique visitors.

**Status:**
- LIVE: recorder person alerts; reviewed camera coverage, whether reviewed manually or by the vision
  reviewer.
- PLANNED: the automatic per-observation counting service (server-side detector + zones). Its tool
  choice was measured on 28 Sep 2026 (see Evidence).

Internal method. Customer wording is in the last section and in `core/customer-vocabulary.yaml`.

## What WatchLog may count

| Measure | Meaning | Allowed on |
|---|---|---|
| People in view | People visible on one camera at one observed moment | any camera |
| Area occupancy | People inside a **confirmed** zone at one observed moment | cameras with owner/installer-confirmed zones |
| Presence rate + longest empty stretch | Share of observed moments with at least one person in a zone, and the longest run without anyone | counters, reception, guard posts, doors |
| Busy hours | Average and peak people-in-view per hour | any camera, per camera |
| Visible diners | Concurrent customers on dining-floor cameras | restaurant dining floors (see `site-types/restaurant.yaml`) |
| Vehicles in view | Vehicles visible at one observed moment | frontage, parking |

## What WatchLog may never claim from counting

- Unique visitors, footfall or distinct people. These need a validated entry-counting line, which no
  current site has.
- Who someone is, or whether they are staff or a customer, from appearance. See
  `person-recognition.md`.
- Productivity, attendance, sales or order volume.
- A zero during unmonitored time. Missing coverage is unknown.

## Procedure

1. **Coverage first.**
   - Resolve the business/service-day window. List the monitored periods from the agent's offline
     history.
   - Treat an offline period that was never closed as offline until events resume.
   - The first observation of a day marks when monitoring started, not when anyone arrived.
2. **Enumerate every observation** in the window, per camera, in time order, keeping source IDs.
   Never sample and extrapolate.
3. **Real cameras only.** Group rows by the physical view they show; check the tenant's
   `camera_attribution_evidence`.
4. **Detect people** per observation (detector threshold 0.4, the measured best).
5. **Zones only when confirmed.** A zone must be drawn and confirmed by the owner or installer on the
   current view. An unconfirmed zone produced a false "cash counter unattended 92%"; the truth was
   staffed 90-97%. Without a confirmed zone, report the whole camera view.
6. **Derive** per-hour average and peak, presence rate, and longest empty stretch (consecutive
   observations).
7. **Overlapping cameras.** Never add counts from cameras that see the same area. Report them
   separately, or de-duplicate with care.
8. **Resolution limits.**
   - A timing figure is no finer than the real capture interval (~5 min today).
   - Low-resolution and far-field views undercount small people.
   - State both when they matter.
9. **Accuracy.** Quote accuracy only against a human-counted sample of the same site's cameras.

## Evidence (measured 28 Sep 2026, WatchLog server, 4 vCPU)

Detector shoot-out on 13 real frames from 2 sites, 8 of them hand-counted:

| Detector | Time per image | People right | Vehicles right |
|---|---|---|---|
| RF-DETR Nano, ONNX **(chosen)** | 0.20 s | 6/8 | 8/8 |
| YOLOX-s | 0.15 s | 4/8 | 8/8 |
| YOLO11n (AGPL) | 0.07-0.19 s | 2-3/8 | 8/8 |

- RF-DETR misses only tiny far-field people.
- A 1,740-image real run took 431 s on 4 vCPU.
- Snapshot cadence of ~5 min per camera is enough for busy hours, presence and after-hours activity.
- It is not enough for queues, service time or table turnover: those need 5-10 s sampling on the
  relevant camera, or clips.

## Customer-facing claims

- "People seen" or "people on the floor", with the time: never "visitors" or "footfall".
- "Busiest around 9 to 10:30 PM", as a relative trend.
- "Someone was at the counter most of the evening; the longest time it was empty was about 20
  minutes, just after opening."
- Always say which hours were covered, and that uncovered time is unknown.
