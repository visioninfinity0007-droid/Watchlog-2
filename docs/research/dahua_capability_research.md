# Dahua Recorder Capability Research — WatchLog Recorder Intelligence Knowledge Base

**Purpose.** A normalized, source-backed capability dataset for Dahua NVR/DVR/XVR recorders, built from OFFICIAL Dahua sources, so WatchLog can decide — per exact model/firmware — which native analytics and management APIs a recorder supports BEFORE any AI is allowed to recommend or apply a configuration.

**Scope of this pass.** 8 commercially-relevant current families/models (seed unit first), plus the Dahua HTTP-API/CGI/SDK surface. Region of all sources: Dahua International / English (`dahuasecurity.com` global + `material.dahuasecurity.com`). Regional-suffix variants (e.g. `/ANZ`, `CEEN`, `-EI` sub-regions) MAY differ — see Gaps §G7.

**Evidence classes used here**
- `OFFICIAL-DOCUMENTED` — stated in an official Dahua datasheet, product page, or API portal (the only class assigned in the tables below).
- `UNSUPPORTED` — official doc gives explicit/exhaustive evidence the feature is absent for that model.
- `UNKNOWN` — the available official doc does not address it (never guessed).
- `FIELD-VERIFIED` — reserved for physical hardware tests. **Not assigned anywhere in this document.** The one field cross-reference (seed unit) is quarantined in §6 and clearly labelled; it is WatchLog test data, not manufacturer truth.

**Cell legend.** `Yes (N ch)` = documented, with recorder-side channel budget · `by-cam` = supported ONLY via a capable Dahua camera, not recorder-side · `No` = documented absent (excluded from that model's exhaustive Intelligent-Alarm / AI spec) · `UNKNOWN` = official doc silent · `—` = not applicable.

> The capability matrix is normalized across four joinable tables (§1–§4), all keyed on **exact model**. Each model's source refs are in Table 1; every capability cell is traceable to those refs unless a cell carries its own `[Sn]`.

---

## 1. Identity & hardware (keyed on model)

| # | Vendor | Exact model | Series / family | Recorder type | Max analog ch | Max IP ch (default+conv) | Max IP res | AI-processing location | Firmware / HW applicability | Source(s) |
|---|--------|-------------|-----------------|---------------|---------------|--------------------------|-----------|------------------------|-----------------------------|-----------|
| M1 | Dahua | **DH-XVR1B08-I** | Cooper-I Series (XVR1B) | XVR (Penta-brid DVR) | 8 (BNC) | 10 (2+8) | 6 MP | Recorder | HW Version 2.0; datasheet Rev 002.000 © 2024 | S1, S2 |
| M2 | Dahua | **DH-XVR5108HS-I3** | XVR5000-I3 Series | XVR (Penta-brid DVR) | 8 (BNC) | 12 (4+8) | 6 MP | Recorder | datasheet Rev 002.000 © 2022 | S3 |
| M3 | Dahua | **DH-XVR5216AN-I3** | XVR5000-I3 Series | XVR (Penta-brid DVR) | 16 (BNC) | 24 | 6 MP | Recorder | datasheet 2023-01-13 | S4 |
| M4 | Dahua | **DH-XVR5108HS-4KL-I3** | XVR5000-4KL-I3 Series | XVR (Penta-brid DVR) | 8 (BNC) | 16 (8+8) | 8 MP | Recorder | HW Version 2.0; datasheet V2 2023-07-20 | S5 |
| M5 | Dahua | **DH-XVR7208A-4K-I3** | XVR7000 I3 Series (WizSense Pro, 4K) | XVR (Penta-brid DVR) | 8 (BNC, 4K) | 16 (8+8) | UNKNOWN | Recorder | product page (ONVIF 24.06) | S6 |
| M6 | Dahua | **DHI-NVR2208-8P-I2** | NVR2-I2 Series (WizSense, entry) | NVR (IP, 8-PoE) | 0 | 8 | UNKNOWN | Recorder + camera | product listing; PDF only on secondary mirrors (see G3) | S9 |
| M7 | Dahua | **DHI-NVR4208-8P-I** | NVR4000-I Series (WizSense) | NVR (IP, 8-PoE) | 0 | 8 | 12 MP (record) | Recorder + camera | datasheet Rev 001.001 © 2021 | S7 |
| M8 | Dahua | **DHI-NVR5216-16P-EI** | NVR5000-EI Series (WizSense/WizMind-class) | NVR (IP, 16-PoE) | 0 | 16 | 32 MP | Recorder + camera | datasheet S0 2024-06-25 | S8 |

Notes: M1 product page (S2) markets it as "…WizSense Digital Video Recorder" and flags **EOL**; the Cooper-I datasheet (S1) is the authoritative spec. M5 IP-max-MP not stated on the product page → UNKNOWN. M6/M7/M8 are pure IP NVRs (no analog/BNC), so "video loss" is expressed as "camera offline" (see Table 3).

---

## 2. Analytics matrix (recorder-side unless marked `by-cam`)

| # | Model | Human | Vehicle | SMD (Plus) | Line-cross / tripwire | Intrusion | Loiter | Parking | Face detect | Face recog | ANPR/LPR | People counting | Heatmap | Other AI |
|---|-------|-------|---------|------------|----------------------|-----------|--------|---------|-------------|-----------|----------|-----------------|---------|----------|
| M1 | DH-XVR1B08-I | Yes (via SMD) | Yes (via SMD) | **Yes (4 ch)** | **No** | **No** | No | No | **No** | **No** | No | No | AI Coding (8 ch); Smart Dual Light |
| M2 | DH-XVR5108HS-I3 | Yes | Yes | Yes (8 ch) | Yes (Perimeter: 1–2 ch, ≤10 IVS/ch) | Yes (same) | UNKNOWN | UNKNOWN | Yes (1 ch, 8 img/s) | Yes (1 ch) | No | No | AI Coding |
| M3 | DH-XVR5216AN-I3 | Yes | Yes | Yes (16 ch) | Yes (Perimeter: 2–4 ch, ≤10 IVS/ch) | Yes (same) | UNKNOWN | UNKNOWN | Yes (2 ch, 12 img/s) | Yes (2 ch) | No | No | AI Coding |
| M4 | DH-XVR5108HS-4KL-I3 | Yes | Yes | Yes (8 ch) | Yes (Perimeter: 2–8 ch, ≤10 IVS/ch) | Yes (same) | UNKNOWN | UNKNOWN | Yes (2 ch, 12 img/s) | Yes (2 ch) | No | No | Scheduled AI; AI Coding |
| M5 | DH-XVR7208A-4K-I3 | Yes | Yes | Yes (8 ch) | Yes (Perimeter: 2–8 ch, ≤10 IVS/ch) | Yes (same) | UNKNOWN | UNKNOWN | Yes (2 ch, 12 img/s) | Yes (2 ch) | No | No | EPTZ; QuickPick 2.0; Video Quality Analytics; Privacy Protection |
| M6 | DHI-NVR2208-8P-I2 | Yes | Yes | Yes (4 ch) | Yes (Perimeter: 1 ch) | Yes (1 ch) | UNKNOWN | UNKNOWN | Yes (1 ch) | Yes (1 ch) | `by-cam` | `by-cam` | AI-by-camera passthrough |
| M7 | DHI-NVR4208-8P-I | Yes | Yes | Yes (SMD Plus) | Yes (Perimeter: 4 ch, ≤10 IVS/ch) | Yes (same) | UNKNOWN | UNKNOWN | Yes (2 ch video / 8 ch pic, w/ FD cam) | Yes (2 ch, 12 img/s) | **`by-cam`** | **`by-cam`** | Fisheye dewarp; Stranger Mode; Heat Map `by-cam` |
| M8 | DHI-NVR5216-16P-EI | Yes | Yes | Yes (8 ch rec / all `by-cam`) | Yes (4 ch rec / all `by-cam`, ≤10 IVS/ch) | Yes (same) | UNKNOWN | `by-cam` (vehicle density) | Yes (2 ch rec / 16 `by-cam`) | Yes (2 ch rec / 16 `by-cam`) | **`by-cam`** (all ch, plate DB 20k) | **`by-cam`** | **`by-cam`** | AcuPick (16 ch); metadata `by-cam`; stereo analysis `by-cam`; crowd distribution `by-cam` |

**Key structural findings**
- **SMD Plus classifies Human AND Vehicle on every model here** (it is Dahua's recorder-side "secondary filtering for human and motor vehicle"). This is the analytic WatchLog can most reliably assume across the current Dahua range.
- **Line-crossing (tripwire) and intrusion are the same feature on Dahua: "Perimeter Protection" = IVS rules** (`≤10 IVS/ch`), doing "Human/Vehicle secondary recognition for tripwire and intrusion" (S7). They are present on the WizSense XVR-I3/7000 and the WizSense NVRs — but **absent on the Cooper-I seed unit M1**.
- **On NVRs, the recorder-side vs camera-side split is decisive.** M7/M8 do Face + Perimeter + SMD Plus **by recorder** (small channel budgets), but **ANPR, people counting, heatmap, video metadata, stereo analysis, crowd distribution, vehicle density are `by-cam` only** — they REQUIRE a capable WizSense/WizMind camera; the recorder cannot originate them. WatchLog must never promise these from an NVR alone.

---

## 3. Limits, constraints & event/alarm surface

| # | Model | Per-channel AI budget / limits | Target & sensitivity filters | Schedules | Geometry / smart-plan model | **Mutual exclusions (critical)** | VideoLoss / tamper |
|---|-------|-------------------------------|------------------------------|-----------|-----------------------------|----------------------------------|--------------------|
| M1 | DH-XVR1B08-I | SMD Plus ≤4 analog ch; AI Coding 8 ch | SMD: Human/Vehicle target + sensitivity | Arming schedule (UNKNOWN detail) | No IVS geometry (SMD is zone/whole-scene) | **"After IP extension is enabled, the AI Function (SMD) will be disabled."** [S1] | Video Loss + Video Tampering (Anomaly Alarm) [S1] |
| M2 | DH-XVR5108HS-I3 | SMD 8 / Perimeter 1–2 / Face 1 ch | Human/Vehicle; face attributes (gender, age, glasses, expression, mask, beard) | Arming schedule | IVS lines/zones drawn per ch; "AI Mode" | **AI Mode is exclusive: `SMD` \| `IVS&SMD` \| `Face`.** Perimeter needs `IVS&SMD`; Face Recog needs `Face`. Adding IP ch beyond default **disables IVS/SMD/FACE.** [S3] | Video loss; tampering (General Alarm) [S3] |
| M3 | DH-XVR5216AN-I3 | SMD 16 / Perimeter 2–4 / Face 2 ch | as M2 | Arming schedule | as M2 | Same AI-Mode exclusion (`SMD`\|`IVS&SMD`\|`Face`) [S4] | Video loss; tampering |
| M4 | DH-XVR5108HS-4KL-I3 | SMD 8 / Perimeter 2–8 / Face 2 ch | as M2 | **Scheduled AI** (multiple AI per ch by time-frame) | IVS lines/zones per ch | **"Face Recognition conflicts with SMD Plus and Perimeter Protection."** + IP-extension disables IVS/SMD/FACE. [S5] | Video loss; tampering [S5] |
| M5 | DH-XVR7208A-4K-I3 | SMD 8 / Perimeter 2–8 / Face 2 ch | as M2 | UNKNOWN (Scheduled AI likely) | IVS lines/zones per ch | **"After IP extension is enabled, EPTZ, Video Quality Analytics, Scene Changing, Face Recognition, Face Detection, SMD, Perimeter Protection, QuickPick 2.0 and Privacy Protection cannot be used."** [S6] | UNKNOWN (analog XVR → expected) |
| M6 | DHI-NVR2208-8P-I2 | AI-by-NVR: Face 1 / Perimeter 1 / SMD 4 ch | Human/Vehicle | UNKNOWN | IVS lines/zones per ch | AI-by-NVR budgets are alternatives ("or") — sum limited [S9] | camera offline (IP NVR) |
| M7 | DHI-NVR4208-8P-I | Perimeter 4 ch (≤10 IVS); Face 2 ch video / 8 ch picture (needs FD cam); 12 img/s | Human/Vehicle secondary recognition; face DB + Stranger Mode threshold | Record schedule: Continuous/MD/Alarm/IVS | IVS per ch; deep-learning module | Recorder-AI budget shared; ANPR/PC only `by-cam` [S7] | **Video Loss** + Tampering + Scene Change (Video Detection) [S7] |
| M8 | DHI-NVR5216-16P-EI | Rec: Face 2 / Perimeter 4 / SMD 8 ch. Cam: Perimeter all-ch 16 tgt/s, Face 16 ch, ANPR all-ch 8 tgt/s, SMD all-ch 32 tgt/s | Human/Vehicle; rich metadata attrs; face DB (20 DB / 20k img / 2.5 GB); plate DB 20k + block/allow list | Record modes incl. intelligent; AcuPick | IVS per ch; AI-by-recorder vs AI-by-camera planes | **"AI enabled" halves bandwidth** (384→200 Mbps) & decoding budget; recorder-AI channel caps as listed [S8] | **camera offline** (no analog video-loss on IP NVR); storage error; cybersecurity exception [S8] |

**Config-safety takeaways for WatchLog's apply-gate**
1. **IP-channel extension is destructive to AI on every XVR here** — enabling IP cameras beyond the default silently disables SMD (M1) or SMD/IVS/Face (M2–M5). Never enable IP extension as a side effect of another change.
2. **XVR "AI Mode" is a single exclusive selector** (`SMD` / `IVS&SMD` / `Face`) on the I3 XVRs (M2/M3) — you cannot have SMD-only *and* Face simultaneously; switching mode reconfigures the whole recorder's analytics plane. M4 states the exclusion as plain text ("Face Recognition conflicts with SMD Plus and Perimeter Protection").
3. **This directly explains the seed unit's field behaviour** (§6): M1 has no IVS at all, so a CGI write to `VideoAnalyseRule` has nothing to bind to.

---

## 4. Management / config API surface (per official Interoperability lines)

All rows `OFFICIAL-DOCUMENTED` at the **API-family** level (each model's datasheet/product page lists these under *Interoperability*). Endpoint-level CGI names are per the Dahua HTTP API specification (S11, see caveat G4) and are given once in §5, not repeated per model.

| # | Model | Config read | Config write | Recording/storage API | Snapshot | Playback / export | Time / NTP | Protocol / API family (official) |
|---|-------|-------------|--------------|-----------------------|----------|-------------------|-----------|----------------------------------|
| M1 | DH-XVR1B08-I | CGI (configManager) | CGI (configManager) | CGI (RecordMode / storageDevice) | CGI (snapshot.cgi) | Instant/general/event/tag/smart; USB+network backup [S1]; clip-export-via-CGI UNKNOWN | NTP (global.cgi / Locales+NTP) | **ONVIF 22.12 (T/S/G); CGI; SDK** [S1] |
| M2 | DH-XVR5108HS-I3 | CGI | CGI | CGI | CGI | smart playback (face+motion) [S3] | NTP | **ONVIF 21.06; CGI Conformant** (SDK not listed — see G5) [S3] |
| M3 | DH-XVR5216AN-I3 | CGI | CGI | CGI | CGI | smart playback | NTP | **ONVIF; CGI** [S4] |
| M4 | DH-XVR5108HS-4KL-I3 | CGI | CGI | CGI | CGI | smart playback | NTP | **ONVIF 22.12 (T/S/G); CGI; SDK** [S5] |
| M5 | DH-XVR7208A-4K-I3 | CGI | CGI | CGI | CGI | UNKNOWN detail | NTP | **ONVIF 24.06 (T/S/G); CGI; SDK** [S6] |
| M6 | DHI-NVR2208-8P-I2 | CGI | CGI | CGI | CGI | UNKNOWN detail | NTP | **ONVIF; CGI; SDK** (typical NVR2-I2) [S9] |
| M7 | DHI-NVR4208-8P-I | CGI | CGI | CGI (10 TB/HDD ×2 SATA) | CGI | Time/Alarm/MD/Exact search; USB+network [S7]; clip-export-via-CGI UNKNOWN | NTP | **ONVIF (Profile S); SDK; CGI** [S7] |
| M8 | DHI-NVR5216-16P-EI | CGI | CGI | CGI (disk group; iSCSI; N+M) | CGI | Instant/general/event/tag/smart; USB+network [S8] | NTP | **ONVIF 23.12 (T/S/G); CGI; SDK** [S8] |

---

## 5. Dahua HTTP-API / CGI / SDK reference (endpoint level)

**Official at family level (S1–S8 Interoperability lines):** every recorder here exposes **CGI (HTTP CGI API)**, **SDK (Dahua NetSDK)**, and **ONVIF** (Profile S/T/G, versions 21.06→24.06). Dahua's public API entry is `dahuasecurity.com/api/` (JS landing page) and the developer docs/SDK live behind the **Dahua Partner Alliances** portal `depp.dahuasecurity.com` (login-gated — **not accessed**, per rules) [S10].

**Endpoint map (per Dahua HTTP API spec S11; corroborated by WatchLog's own field probe S12).** These are the read/write CGIs WatchLog's driver targets; the specific names are documented in the manufacturer HTTP-API spec but the freely-openable copy is secondary-hosted/partner-gated (see G4), so treat endpoint-level detail as OFFICIAL-per-spec-name, host-caveated:

| Function | Endpoint (CGI) | R/W | WatchLog generic |
|----------|----------------|-----|------------------|
| Identity | `magicBox.cgi?action=getSystemInfo` / `getDeviceType` / `getSoftwareVersion` / `getSerialNo` | R | device_identity |
| Device time | `global.cgi?action=getCurrentTime` / `setCurrentTime` | R/W | time |
| Timezone/DST | `configManager.cgi?...&name=Locales` | R/W | time_locale |
| NTP | `configManager.cgi?...&name=NTP` | R/W | ntp |
| Channel names | `configManager.cgi?...&name=ChannelTitle` | R/W | channel_title |
| Basic motion | `configManager.cgi?...&name=MotionDetect` | R/W | motion |
| SMD Human/Vehicle | `configManager.cgi?...&name=SmartMotionDetect` | R/W | human_vehicle_classification |
| IVS / Perimeter | `configManager.cgi?...&name=VideoAnalyseRule` (Class=`CrossLineDetection`/`CrossRegionDetection`) | R/W | line_crossing / intrusion |
| Camera tamper | `configManager.cgi?...&name=CoverDetect` | R/W | tamper |
| Record mode | `configManager.cgi?...&name=RecordMode` | R/W | recording_mode |
| Storage/HDD | `storageDevice.cgi?action=getDeviceAllInfo` | R | storage_health |
| Event stream | `eventManager.cgi?action=attach&codes=[...]&heartbeat=5` | R (push) | event_subscription |
| Snapshot | `snapshot.cgi?channel=N` | R | snapshot |
| Clip / playback download | RTSP + NetSDK `mediaFileFind` (CGI clip-export not consistently documented) | R | clip_export → **UNKNOWN via CGI** |

---

## 6. Field cross-reference — SEED UNIT (NOT part of the official dataset)

> Quarantined on purpose. The lines below are **WatchLog physical-hardware test results (evidence class FIELD-VERIFIED)**, supplied by the engagement, on the first field unit **DH-XVR1B08-I (M1)**. They are shown only so the official research can be reconciled against reality. **They are not manufacturer truth and carry none of this document's OFFICIAL-DOCUMENTED sourcing.**

- Field-proven read+write: **SMD Human/Vehicle** (read+write), **SMD sensitivity** (write), **ChannelTitle** (write), **time/NTP** (write), and current **VideoLoss** (read).
- Field-proven absent-on-CGI-path: **IVS Tripwire/Intrusion did NOT expose a usable rule** on the validated CGI path.
- **Reconciliation:** this matches the OFFICIAL M1 spec (S1) exactly — Cooper-I lists Intelligent Alarm = *SMD Plus only*, no Perimeter/IVS. The absence is a real product-scope limit of the Cooper-I family, not a firmware or driver gap. (Probe tooling: `tools/dahua_probe.ps1`; driver: `prototype/agent/drivers/dahua.py`.)

---

## 7. Sources (numbered)

All retrieved **2026-09-10**. S1–S10 are OFFICIAL Dahua. S11 is a SECONDARY-hosted copy of an official manufacturer spec (labelled). S12 is WatchLog-internal / field (labelled).

1. **[S1] OFFICIAL** — DH-XVR1B08-I datasheet, *"XVR1B08-I_V2_datasheet_20240229.pdf"*, Cooper-I Series, Rev 002.000 © 2024. https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR1B08-I_V2_datasheet_20240229.pdf
2. **[S2] OFFICIAL** — DH-XVR1B08-I product page (Cooper-I Series; marked EOL). https://www.dahuasecurity.com/products/All-Products/HDCVI-Recorders/Cooper-I-Series/XVR1B08-I=V2
3. **[S3] OFFICIAL** — DH-XVR5108HS-I3 datasheet, *"XVR5108HS-I3_datasheet_20220530.pdf"*, XVR5000-I3 Series, Rev 002.000 © 2022. https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5108HS-I3_datasheet_20220530.pdf
4. **[S4] OFFICIAL** — DH-XVR5216AN-I3 datasheet, *"XVR5216AN-I3_datasheet_20230113.pdf"*, XVR5000-I3 Series. https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5216AN-I3_datasheet_20230113.pdf
5. **[S5] OFFICIAL** — DH-XVR5108HS-4KL-I3 datasheet, *"XVR5108HS-4KL-I3_V2_datasheet_20230720.pdf"*, XVR5000-4KL-I3 Series (HW v2.0). https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5108HS-4KL-I3_V2_datasheet_20230720.pdf
6. **[S6] OFFICIAL** — DH-XVR7208A-4K-I3 product page, WizSense I3 4K Series (ONVIF 24.06). https://www.dahuasecurity.com/products/All-Products/HDCVI-Recorders/WizSense-Series/I3-Series/4K-series/XVR7208A-4K-I3
7. **[S7] OFFICIAL** — DHI-NVR4208-8P-I datasheet, *"NVR4208-8P-I_datasheet_20210113.pdf"*, NVR4000-I Series, Rev 001.001 © 2021. https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/NVR4208-8P-I_datasheet_20210113.pdf
8. **[S8] OFFICIAL** — DHI-NVR5216-16P-EI datasheet, *"NVR5216-16P-EI_S0_datasheet_20240625.pdf"*, NVR5000-EI Series. https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/NVR5216-16P-EI_S0_datasheet_20240625.pdf
9. **[S9] OFFICIAL (product listing; see G3)** — DHI-NVR2208-8P-I2, NVR2-I2 Series product page. https://www.dahuasecurity.com/products/All-Products/Network-Recorders/WizSense-Series/NVR2-I2-Series/2HDD/NVR2208-8P-I2
10. **[S10] OFFICIAL (portal)** — Dahua API interface `https://www.dahuasecurity.com/api/` ; Dahua Partner Alliances integration/SDK portal `https://depp.dahuasecurity.com/integration/guide/download/sdk` (login-gated, not accessed).
11. **[S11] SECONDARY (copy of official manufacturer spec)** — "Dahua HTTP API" specification, endpoint-level CGI reference. Freely-openable copies are secondary-hosted (e.g. community-wiki copy of *"DAHUA HTTP API FOR IPC v1.40"*, `wiki.dno-it.ru`) or login-gated (Scribd). Used ONLY to name endpoints in §5; not treated as manufacturer-authoritative because the copy is not officially hosted.
12. **[S12] WATCHLOG-INTERNAL / FIELD (labelled, not manufacturer)** — `tools/dahua_probe.ps1` (read-only field probe) and `prototype/agent/drivers/dahua.py` (`EVENT_CODE_MAP`, `capabilities()`). Basis for the terminology map (§8) and the §6 field cross-reference.

---

## 8. Dahua → WatchLog generic-capability terminology map

Aligned to WatchLog's existing driver vocabulary (`prototype/agent/drivers/dahua.py` `EVENT_CODE_MAP` + `base.py` `capabilities()` keys) [S12]. "Extension" = generic key WatchLog does not yet emit but this dataset implies it should.

| Dahua term / event code / config name | WatchLog generic capability | Notes |
|----------------------------------------|-----------------------------|-------|
| SMD Plus (Smart Motion Detection) / `SmartMotionDetect` | `human_vehicle_classification` (`smd`) | Recorder-side human+vehicle secondary filtering |
| `SmartMotionHuman` | `person` (human) | SMD sub-classification |
| `SmartMotionVehicle` | `vehicle` | SMD sub-classification |
| `VideoMotion` / `MotionDetect` | `motion` | Basic motion (no classification) |
| Perimeter Protection · IVS `CrossLineDetection` (Tripwire) | `line_crossing` | Geometry (line) required |
| Perimeter Protection · IVS `CrossRegionDetection` (Intrusion) | `intrusion` | Geometry (zone) required |
| IVS `LeftDetection` | `object_left` | Abandoned object |
| IVS `TakenAwayDetection` | `object_removed` | Removed object |
| `VideoLoss` | `video_loss` | Analog XVR only; NVR uses camera-offline |
| `VideoBlind` / `CoverDetect` / Tampering | `tamper` | |
| `AlarmLocal` | `alarm_input` | Physical alarm-in |
| `StorageNotExist` / `StorageFailure` | `disk_error` | |
| `StorageLowSpace` / disk full | `disk_full` | Degraded, not fault |
| `FaceDetection` | `face` | Recorder or camera |
| Face Recognition (DB compare) | `face_recognition` *(extension)* | Distinct from detection; needs face DB |
| ANPR / License Plate / Traffic | `license_plate` *(extension)* | **`by-cam` on all NVRs here** |
| People Counting | `people_counting` *(extension)* | **`by-cam`** |
| Heat Map | `heatmap` *(extension)* | **`by-cam`** |
| Video Metadata (human/motor/non-motor attrs) | `object_metadata` *(extension)* | **`by-cam`** |
| Stereo Analysis / Crowd Distribution / Vehicle Density | `crowd_analytics` *(extension)* | **`by-cam`** |
| `ChannelTitle` | `channel_title` (config) | Write-verified in field (M1) |
| `Locales` + `NTP` + `global.cgi getCurrentTime` | `time` / `ntp` (config) | |
| `RecordMode` | `recording_mode` (config) | |
| ONVIF / CGI / SDK (NetSDK) | `protocol_family` | Per-model Interoperability line |

---

## 9. Conflicts & gaps log

- **G1 — M1 SMD channel count (RESOLVED, conflict logged).** The WebFetch paraphrase of the M1 product page (S2) reported "8-channel SMD". The authoritative datasheet (S1), in BOTH the feature bullet and the spec table, says **"SMD Plus by Recorder: 4 channels"**; the "8" belongs to **AI Coding (8-ch)**, a different feature. Dataset uses **SMD Plus = 4 ch**. Cause: product-page summarizer conflated AI-Coding channels with SMD channels.
- **G2 — M1 "Perimeter" mention is generic marketing, not a model capability.** S1's *Smart Dual Light* paragraph says white light triggers on "the SMD Plus/Perimeter Protection function of AI-enabled XVR". This is series-level copy; M1's own *Intelligent Alarm* spec lists **SMD Plus only**. Do NOT infer IVS/Perimeter for Cooper-I. (Confirmed by §6 field cross-reference.)
- **G3 — M6 (NVR2208-8P-I2) provenance caveat.** The official product page did not render server-side on fetch (JS 404); AI-budget figures ("AI-by-NVR: 1 ch face / 1 ch perimeter / 4 ch SMD Plus; 10 DB / 5,000 img") come from the official product-catalog search summary (S9). The full datasheet PDF was found ONLY on secondary mirrors (sintrabaltic.eu, bezpeka.club) — **not opened / not relied on**. Re-verify against the official PDF before using M6 to gate a config.
- **G4 — Endpoint-level HTTP-API doc is not officially openable.** The manufacturer "Dahua HTTP API" spec is real and official, but freely-reachable copies are partner-login-gated (`depp.dahuasecurity.com`) or on login-gated third parties (Scribd). Not bypassed. §5 endpoint NAMES are therefore OFFICIAL-per-spec but host-caveated (S11) and corroborated by WatchLog's own field probe (S12). Family-level support (CGI/SDK/ONVIF) IS fully official (S1–S8).
- **G5 — M2 lists "CGI Conformant" and omits SDK.** The 2022 XVR5108HS-I3 datasheet (S3) *Interoperability* line reads "ONVIF 21.06; CGI Conformant" with no SDK entry, whereas newer XVR/NVR datasheets (S1, S5–S8) list "CGI; SDK". Likely a datasheet-template evolution rather than a true SDK absence, but **not confirmed** — leave SDK for M2 as UNKNOWN.
- **G6 — Clip/export via CGI is UNKNOWN across the board.** No datasheet documents a per-incident clip-download CGI; the documented playback/export paths are the local GUI, RTSP, and NetSDK. Matches WatchLog `get_clip()` being intentionally unimplemented. Do not assume CGI clip pull.
- **G7 — Region / firmware drift not covered.** All sources are Dahua International/EN. Regional AI variants exist (e.g. `NVR4208-8P-AI/ANZ`, `-EI` sub-region datasheets) and channel budgets/firmware gates can differ by region and firmware. Preserve the exact suffix; do not generalize an International datasheet to a regional SKU without its own doc.
- **G8 — Loiter/parking/queue/dwell unaddressed.** None of the 8 datasheets enumerate loitering, parking-violation, queue/dwell as recorder-side analytics (these are typically camera-side IVS on WizMind cameras). Marked UNKNOWN, not "No", because the specs are not exhaustive on secondary IVS sub-types.
- **G9 — NVR "video loss".** IP NVRs (M6–M8) express signal loss as **"camera offline"**, not analog "video loss"; only the analog XVRs (M1–M5) list true Video Loss. WatchLog's `video_loss` mapping must branch on recorder type.
