# Confidence & Evidence Sufficiency (invariant)

Every WatchLog conclusion carries an explicit confidence. The model must **state uncertainty**, and
the application refuses to render a low-confidence conclusion as a fact.

## Two axes (kept separate)

1. **`confidence`** — a float `0.0–1.0` on every observation/activity/incident artifact.
2. **`tier`** — a coarse sufficiency label describing *how much evidence backs the conclusion*:

| tier | meaning | allowed language |
|---|---|---|
| `STRONG` | multiple quality signals agree, coverage is LIVE/RECOVERED over the window | may state the conclusion plainly |
| `MODERATE` | one good signal or several weak ones; some coverage | "likely / appears" + show evidence |
| `WEAK` | a single low-quality signal | "possible / unconfirmed" + require review |
| `DETECTION_GAP` | flagged but there is **no evidence to verify** (coverage UNVERIFIED, camera offline, snapshot missing) | "cannot verify" — never a positive claim, never raises severity |

`DETECTION_GAP` is a first-class outcome, not a failure. "We could not see this" is a truthful answer.

## `drivers` — every conclusion enumerates its evidence

An incident/activity MUST list the `drivers` that produced it (the specific observations, event IDs,
snapshots, coverage). No `drivers` ⇒ the conclusion is `WEAK` at best and cannot be `STRONG`.
(ShadowBroker correlation pattern, WatchLog-governed.)

## Correlation requires multiple quality signals

A composite/correlated conclusion (e.g. "same person at reception and the armory", a multi-camera
journey) is only allowed when **more than one quality signal** supports it, and it must carry the
uncertainty of the weakest link. A single re-id hop is `WEAK`/`MODERATE`, never `STRONG`.

## Coverage lowers confidence, never raises it

- `LIVE` coverage over the window: no penalty.
- `RECOVERED` (from NVR archive, re-analyzed): allowed, labelled as recovered, slight penalty.
- `UNVERIFIED`: the window is a `DETECTION_GAP`; conclusions about it are refused.

## Forbidden

- Upgrading `IDENTITY_UNVERIFIED` to `KNOWN_PERSON` without a real match.
- Turning a `DETECTION_GAP` into a negative claim ("nothing happened").
- Reporting a count as exact when the measurement is an estimate.
