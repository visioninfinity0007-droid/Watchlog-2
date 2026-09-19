# Hikvision Recorder Capability Research — WatchLog Recorder Intelligence Knowledge Base

**Vendor:** Hikvision (Hangzhou Hikvision Digital Technology Co., Ltd.)
**Scope:** Native analytics + management/API capabilities of current, commercially-relevant Hikvision NVR/DVR recorders, normalized for WatchLog.
**Retrieved:** 2026-09-10 (all sources).
**Author/process:** Built only from official Hikvision datasheets, user manuals, and official ISAPI/SDK developer guides. One ISAPI guide was reached via a secondary host (see §Sources / §Conflicts). Secondary material is labelled; it is never treated as manufacturer truth.

## Evidence-class rules used here
- **OFFICIAL-DOCUMENTED** — stated in an official Hikvision document (datasheet / manual / API guide). This is the highest class in this file.
- **OFFICIAL-DOCUMENTED (secondary-host)** — the *document* is an official Hikvision publication, but the *copy retrieved* was hosted on a non-hikvision.com domain (mirror). Treat the endpoint facts as authoritative-by-document, host as secondary.
- **UNSUPPORTED** — a document explicitly says the feature is absent / "N/A".
- **UNKNOWN** — not found in an accessible official document (do **not** infer).
- **FIELD-VERIFIED — NOT USED.** WatchLog's Hikvision support is CODE-only; no unit here has been validated against real hardware. **Nothing in this file is FIELD-VERIFIED.**

## The single most important Hikvision nuance (read first)
Hikvision datasheets split every analytic into **"AI by Device / AI by NVR"** (runs on the recorder's own deep-learning engine) versus **"AI by Camera"** (requires an AcuSense / DeepinView / smart IP camera; the recorder only configures it and receives events). The **same analytic can be recorder-side on one model and camera-only on another**, and **channel counts differ across models in the same series** (e.g. recorder-side Perimeter Protection is 1-ch on DS-7616NXI-**K1** but 2-ch on DS-7616NXI-**K2/16P** — same K-series). Never generalize a capability from one model to its family.

Notation in the matrices below:
- `D:n` = recorder-side ("by Device/NVR"), n = documented channel capacity.
- `C:all` / `C` = camera-side only ("by Camera"); recorder configures/receives, camera does the AI.
- `D+C` = documented both ways. `—` = not listed on that model's datasheet (UNKNOWN unless stated UNSUPPORTED). `U` = UNKNOWN.
- Source refs `[S#]` map to the numbered list in §Sources. Every capability cell is traceable to a source.

---

## Table A — Identity & hardware baseline

| # | Exact model(s) | Series / family | Region (of datasheet) | Doc / firmware baseline | Type | Max ch (analog+IP) | AI-processing location | Src |
|---|---|---|---|---|---|---|---|---|
| 1 | DS-7616NXI-K1 (B) | K-series AcuSense NVR (NXI), 1U | International/global | Datasheet V4.74.000, 2023-02-09 | NVR | 16 IP (0 analog), no PoE | recorder **and** camera | [S1] |
| 2 | DS-7616NXI-K2/16P | K-series AcuSense NVR (NXI), PoE | International/global | Datasheet V4.74.000, 2023-02-09 | NVR | 16 IP + 16 PoE | recorder **and** camera | [S2] |
| 3 | DS-7716NXI-K4 (as DS-7716NXI-K4-UHK) | K-series AcuSense NVR (NXI), 1.5U (7700) | Pakistan (UHK regional variant) | Datasheet V4.74.000, 2023-05-06 | NVR | 16 IP, 4 SATA, 2 NIC | recorder **and** camera | [S3] |
| 4 | DS-7608NI-I2/8P, DS-7616NI-I2/16P, DS-7632NI-I2/16P | I-series NVR (DS-7600NI-I2/P) | International/global | Datasheet V4.63.000, 2023-04-17 | NVR | 8/16/32 IP + 8/16 PoE | **camera** (no AI-by-device table on this datasheet) | [S4] |
| 5 | iDS-7204HQHI-M1/FA(C), iDS-7208HQHI-M1/FA(C), iDS-7216HQHI-M1/FA | Turbo **AcuSense** DVR (iDS-7200HQHI-M1/FA) | UK/EU (English, international) | Datasheet V4.26.130, 2021-05-17 | DVR (tribrid: HDTVI/AHD/CVI/CVBS + IP) | 4/8/16 analog + up to 5/10/18 IP (more w/ enhanced IP) | recorder (analog) **and** camera | [S5] |
| 6 | DS-7204HGHI-K1 | Turbo HD DVR (plain K-series, non-AcuSense-branded) | International/global | Datasheet V4.70.160, 2023-08-22 | DVR (tribrid) | 4 analog + up to 5 IP | recorder (MD2.0 only) | [S6] |
| 7 | iDS-9616NXI-I8/X(C), iDS-9632NXI-I8/X(C), iDS-9664NXI-I8/X(C) | **DeepinMind** 96-series NVR | International/global | Datasheet V4.60.110, 2022-02-18 | NVR (RAID) | 16/32/64 IP | recorder (deep-learning engines) **and** camera | [S7] |
| 8 | iDS-7608NXI-M2/8P/X | **DeepinMind** M-series NVR (78/76 entry) | International/global | Datasheet V4.61.200, 2023-05-24 | NVR | 8 IP + 8 PoE | recorder (deep-learning engines) **and** camera | [S8] |

Notes:
- Model 4 (DS-7600NI-I2/P): this datasheet describes analytics only as *"Configurable special camera smart functions, such as VCA detection (motion, line crossing, intrusion, etc.), heat map, ANPR, and people counting"* — i.e. camera-side. It contains **no** "AI by Device" table, so recorder-side target classification is **UNKNOWN** for this datasheet version [S4].
- Model 6 (DS-7204HGHI-K1): "plain" Turbo DVR — still performs recorder-side **Motion Detection 2.0** human/vehicle classification, but nothing else (no perimeter, no face) [S6]. Good contrast to the AcuSense DVR (model 5).

---

## Table B — Analytics matrix (recorder-side `D:n` vs camera-side `C`)

| # Model | MD2.0 Human/Vehicle (SMD-equiv) | Line-crossing | Intrusion (field) | Region enter/exit | Loiter / Park | Face detect / capture | Face recognition / compare | ANPR / LPR | People counting | Heat map | Video structuralization | Throw-obj (misc AI) | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 DS-7616NXI-K1(B) | **D:all** (4MP; 8MP w/ enhanced SVC) | D:1-ch¹ | D:1-ch¹ | C (ISAPI)² | C (ISAPI)² | D:1-ch@8MP | D:4-ch (compare); 16 libs / 20,000 faces | C | C | C | — (camera only) | [S1] |
| 2 DS-7616NXI-K2/16P | **D:all** (4MP;8MP SVC) | D:2-ch¹ | D:2-ch¹ | C² | C² | D:1-ch@8MP | D:4-ch; 16 libs/20,000 | C | C | C | — | [S2] |
| 3 DS-7716NXI-K4 | **D:all** (4MP;8MP SVC) | D:2-ch¹ | D:2-ch¹ | C² | C² | D:1-ch@8MP | D:4-ch; 16 libs/20,000 | C | C | C | — | [S3] |
| 4 DS-7600NI-I2/P | U (camera-configured only) | C | C | C | C | C | C | C | C | C | — | [S4] |
| 5 iDS-7200HQHI-M1/FA | **D:all analog** (human/veh, default on) | D:2–4-ch¹ ³ | D:2–4-ch¹ ³ | C² | C² | D:1-ch (HD analog face compare) | D:1-ch; 16 libs/500 faces | C | C | C | — | [S5] |
| 6 DS-7204HGHI-K1 | **D:all analog** (human/veh) | — | — | — | — | — | — | — | — | — | — | [S6] |
| 7 iDS-9600NXI-I8/X | C (by camera) | **D:up to 16-ch**¹ | **D:up to 16-ch**¹ | C² | C² | **D:8-ch@8MP capture** | **D:16-ch compare; 32 libs/100,000 faces** | C (camera) | C (camera) | C (camera) | **D:8-ch** (video structuralization) | **D:8-ch** | [S7] |
| 8 iDS-7608NXI-M2/8P/X | C (by camera) | **D:12-ch**¹ | **D:12-ch**¹ | C² | C² | **D:8-ch (2MP→8MP)** | **D:8-ch compare @24 pic/s; 16 libs/100,000** | C (camera; plate/vehicle attrs) | C (camera) | C² | **D:6-ch** | **D:8-ch** | [S8] |

¹ On Hikvision recorders, **line-crossing + intrusion are bundled as "Perimeter Protection"** and share the stated channel budget; the number shown is the datasheet's recorder-side Perimeter capacity. DeepinMind adds video-structuralization/throwing-objects as separate engines.
² region-entrance/exiting, loitering, parking are documented at the **ISAPI/camera** level (behavior types `regionEntrance`, `regionExiting`, `loitering`, `parking` — see Table D [S9][S10]); they are **not** itemized as recorder-side engines on these recorder datasheets → treat recorder-side as UNKNOWN, camera-side as OFFICIAL-DOCUMENTED via ISAPI.
³ iDS-7200HQHI-M1/FA: Perimeter Protection human/vehicle = **up to 2-ch** on iDS-7204/7208, **up to 4-ch** on iDS-7216 [S5].

**Headline finding on AcuSense recorder-side vs camera-side:**
- On **AcuSense NVRs (K-series, models 1-3)** the recorder itself runs **Motion Detection 2.0 (all channels)** and **Perimeter Protection (1-2 ch)** and **face** with human/vehicle target filtering — but ANPR, heat map, people counting, region/loiter/park, and throwing-objects are **camera-only** [S1][S2][S3].
- On the **Turbo AcuSense DVR (model 5)** the recorder runs deep-learning MD2.0 + perimeter + face **on analog cameras that have no AI of their own** — this is the key value of recorder-side AcuSense for analog estates [S5].
- On **DeepinMind NVRs (models 7-8)** the recorder runs four heavy deep-learning engines (**face recognition, perimeter protection, video structuralization, throwing-objects**); but **ANPR, people counting and heat map are still camera-side**, despite DeepinMind being marketed as an "AI NVR" [S7][S8].
- On the **plain I-series NVR (model 4)** the datasheet lists **no** recorder-side AI at all — all analytics are camera-configured [S4].

---

## Table C — Per-model management / API, limits, and mutual exclusions

| # Model | Simultaneous-AI limit / mutual exclusion (verbatim intent) | Target/sensitivity filter | Protocol / API family (from datasheet) | Time/NTP | Storage | Src |
|---|---|---|---|---|---|---|
| 1 DS-7616NXI-K1(B) | "Facial recognition, motion detection 2.0 **or** perimeter protection cannot be enabled at the same time." (one of the three) | human/vehicle target classification; MD2.0 filters leaves/lights | **ISAPI; SDK; ONVIF (profile S/G)** | NTP (protocol list) | 1 SATA ≤10TB | [S1] |
| 2 DS-7616NXI-K2/16P | same one-of-three exclusion | human/vehicle | **ISAPI; SDK; ONVIF S/G** | NTP | 2 SATA ≤10TB | [S2] |
| 3 DS-7716NXI-K4 | same one-of-three exclusion | human/vehicle | **ISAPI; SDK; ONVIF S/G** | NTP | 4 SATA ≤10TB | [S3] |
| 4 DS-7600NI-I2/P | not stated (camera-configured) | camera-dependent | **UNKNOWN on datasheet** — no API row; protocol list = TCP/IP,…,NTP,RTSP,HTTP(S) (no ONVIF/ISAPI/SDK itemized). Platform ISAPI/SDK per [S9][S10] but not model-confirmed | NTP | 2 SATA | [S4] |
| 5 iDS-7200HQHI-M1/FA | "Face picture comparison, motion detection 2.0 and perimeter protection cannot be enabled at the same time. Enable one … makes the other two unavailable." Also: enhanced-IP mode may conflict with smart events | human/vehicle; MD2.0 filters leaves/lights | **ONVIF** listed; ISAPI/SDK **not** itemized on datasheet (UNKNOWN from datasheet) | NTP | 1 SATA ≤10TB | [S5] |
| 6 DS-7204HGHI-K1 | only MD2.0 exists | human/vehicle | **UNKNOWN on datasheet** — protocol list has NTP, HTTPS; **no ONVIF/ISAPI/SDK** listed | NTP | 1 SATA ≤4TB | [S6] |
| 7 iDS-9600NXI-I8/X | "Four engine modes … no more [than] two modes can be enabled at the same time." When two run, capacities drop (Perimeter 8-ch; Structuralization 2-ch + 4-ch face compare; Facial 4-ch capture + 8-ch compare; Throwing 4-ch) | human/vehicle; false-alarm reduction (branches/leaves/shadow/light/animals) | **UNKNOWN on datasheet** — no API row; NTP/RTSP/HTTP(S) in protocol list. Platform ISAPI/SDK per [S9][S10] | NTP | 8 SATA + eSATA, RAID 0/1/5/6/10 | [S7] |
| 8 iDS-7608NXI-M2/8P/X | "1 engine can run an intelligent algorithm, engine mode is adjustable" → **only one engine at a time** | human/vehicle; plate/vehicle attributes (ANPR by camera) | **ISAPI; SDK; ONVIF S/G** (explicit) | NTP | 2 SATA ≤14TB + eSATA | [S8] |

All 8 recorders list **NTP** in their network-protocol set (time sync configurable) [S1–S8]. Schedules, geometry/smart-plan (detection regions, arming schedules) are configured per-detector via the GUI/ISAPI (see Table D); they are not enumerated on datasheets.

---

## Table D — ISAPI / SDK: documented config read/write surface (category 4)

Endpoints and event-type tokens below are **OFFICIAL-DOCUMENTED** from Hikvision's Intelligent Security API developer guides. Caveat: the accessible guides are **camera-oriented** (General Application V2.0 lists DS-2CD cameras; Metadata V2.6 lists radar/PTZ cameras). They define the ISAPI protocol surface that Hikvision recorders also expose (NVRs proxy channels via `InputProxy`), but an **NVR-specific ISAPI guide was not obtained** — see §Conflicts. HTTP verbs: ISAPI resources are read via `GET`, written via `PUT/POST`, capability-probed via the `/capabilities` sibling.

| WatchLog function | Documented ISAPI resource(s) / token(s) | Doc | Class |
|---|---|---|---|
| System time (read/write) | `/ISAPI/System/time`, `/ISAPI/System/time/localTime`, `/ISAPI/System/time/timeZone`, `/ISAPI/System/time/capabilities` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| NTP config | `/ISAPI/System/time/ntpServers`, `/ntpServers/<ID>`, `/ntpServers/test` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Device identity | `/ISAPI/System/deviceInfo`, `/deviceInfo/capabilities` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Channel / input inventory & names (NVR) | `/ISAPI/ContentMgmt/InputProxy/channels`, `/channels/<ID>`, `/channels/<ID>/capabilities`, `/InputProxy/search` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Local video input + channel-name/OSD | `/ISAPI/System/Video/inputs/channels/<ID>`, `…/overlays/channelNameOverlay`, `…/overlays/dateTimeOverlay`, `…/overlays/text` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Smart/VCA config parent | `/ISAPI/Smart`, `/ISAPI/Smart/capabilities`; per-channel VCA: `/ISAPI/System/Video/inputs/channels/<ID>/VCAResource` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Line crossing | event/behavior token **`lineDetection`** ("line crossing") | [S9][S10][S11] | OFFICIAL-DOCUMENTED |
| Intrusion / field detection | token **`fieldDetection`** ("intrusion") | [S9][S10][S11] | OFFICIAL-DOCUMENTED |
| Region entrance / exiting | tokens **`regionEntrance`**, **`regionExiting`** | [S9][S10] | OFFICIAL-DOCUMENTED |
| Loitering / parking / gathering / fast-move | tokens **`loitering`**, **`parking`**, **`group`**, **`rapidMove`** | [S10] | OFFICIAL-DOCUMENTED |
| Object left/removed | tokens `attendedBaggage` / `unattendedBaggage` (object removal / unattended) | [S9] | OFFICIAL-DOCUMENTED |
| Face | tokens `faceSnap` (capture), `faceRecognition`, `faceContrast` (picture comparison) | [S9][S10] | OFFICIAL-DOCUMENTED |
| People counting | token `peopleCounting`; `/ISAPI/System/Video/inputs/channels/counting/collection` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Heat map | `/ISAPI/System/Video/inputs/channels/heatMap/collection` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Video loss / tamper / exception (event types) | `videoLoss`, `tamperDetection`, `shelteralarm`, `VMD`/`motionDetection`, `ROI` (in `<eventType opt=…>`); HDD/exception triggers `/ISAPI/Event/triggers/hdBadBlock`, `/highHDTemperature`, `/severeHDFailure` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Event linkage / arming schedule | `/ISAPI/Event/triggers`, `/Event/triggers/<ID>`, `/Event/schedules/<EventType>/<ID>`, `/Event/capabilities` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Real-time alarm & metadata stream | `/ISAPI/Event/notification/alertStream`; `/ISAPI/Streaming/channels/<ID>/metadata`, `/metadata/subscribeType`, `/metadata/capabilities` | [S9][S10][S11] | OFFICIAL-DOCUMENTED |
| Storage / HDD | `/ISAPI/ContentMgmt/Storage/hdd`, `/hdd/<ID>`, `/hdd/<ID>/format`, `/hdd/capabilities`, `/Storage/quota` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Recording control | `/ISAPI/ContentMgmt/record/control/manual/start|stop/tracks/<ID>`, `/record/tracks/<ID>` | [S9] | OFFICIAL-DOCUMENTED (secondary-host) |
| Snapshot (still image) | `/ISAPI/Streaming/channels/<ID>/picture` | [S9][S11] | OFFICIAL-DOCUMENTED |
| Playback / search / export | `/ISAPI/ContentMgmt/search`, `/ISAPI/ContentMgmt/download`, `/download/toUSB`; RTSP playback referenced | [S9][S11] | OFFICIAL-DOCUMENTED |
| SDK / ONVIF availability | Datasheets of models 1,2,3,8 explicitly list **"API: ONVIF (profile S/G); SDK; ISAPI"** | [S1][S2][S3][S8] | OFFICIAL-DOCUMENTED |

**Not confirmable (gap):** the exact per-detector Smart **write** resource path (commonly `/ISAPI/Smart/LineDetection/<ID>` and `/ISAPI/Smart/FieldDetection/<ID>`) could **not** be extracted verbatim from [S9]/[S10]; those guides expose the `lineDetection`/`fieldDetection` **tokens** and the `/ISAPI/Smart` parent, but the sub-resource path string was not present in the retrieved copies. Marked **UNKNOWN** pending an NVR ISAPI guide. Do not assert the sub-path as documented.

---

## Hikvision → WatchLog generic-capability terminology map

| Hikvision term (as printed) | WatchLog generic capability key |
|---|---|
| AcuSense human/vehicle target classification; "Motion Detection 2.0" human/vehicle | `human_vehicle_classification` |
| Motion Detection 2.0 (deep-learning motion w/ target + false-alarm filter) | `motion_detection_smart` (SMD-equivalent) |
| VMD / classic `motionDetection` | `basic_motion` |
| Line Crossing Detection / `lineDetection` | `line_crossing` |
| Intrusion Detection / Field Detection / `fieldDetection` | `intrusion` |
| Region Entrance / `regionEntrance` | `region_entrance` |
| Region Exiting / `regionExiting` | `region_exiting` |
| Loitering / `loitering` | `loitering` |
| Parking / `parking` | `parking` |
| Object removal / `attendedBaggage` / `unattendedBaggage` | `object_left_or_removed` |
| Face detection / human face capture / `faceSnap` | `face_detection` |
| Face Picture Comparison / `faceContrast` / `faceRecognition` | `face_recognition` |
| ANPR / vehicle plate / plate & vehicle attributes | `anpr_lpr` |
| People Counting / `peopleCounting` | `people_counting` |
| Heat Map / `heatMap` | `heatmap` |
| Video Structuralization (face/body/vehicle attribute extraction) | `object_attribute_extraction` |
| Throwing Objects from Building | `high_altitude_object_detection` |
| Video Loss / `videoLoss` | `video_loss` |
| Video Tampering / `tamperDetection` / `shelteralarm` | `tamper` |
| Exception (HDD full/error, net disconnect, illegal login) | `device_exception` |
| Perimeter Protection (umbrella: line-cross + intrusion + region) | `perimeter_protection` (composite → expands to the keys above) |
| `/ISAPI/System/time` + `/time/ntpServers` | `time_ntp_config` |
| `/ISAPI/ContentMgmt/InputProxy/channels` | `channel_inventory_api` |
| `/ISAPI/System/Video/inputs/channels/<ID>/overlays/channelNameOverlay` | `channel_naming_api` |
| `/ISAPI/ContentMgmt/Storage/hdd` | `storage_hdd_api` |
| `/ISAPI/Streaming/channels/<ID>/picture` | `snapshot_api` |
| `/ISAPI/ContentMgmt/search` + `/download` | `playback_export_api` |
| `/ISAPI/Event/notification/alertStream` + `/Streaming/…/metadata` | `event_stream_api` |
| `/ISAPI/Smart` + `/VCAResource` | `analytics_config_api` |

---

## Sources (official Hikvision unless noted)

| # | Document | Version / date | URL | Host |
|---|---|---|---|---|
| S1 | Datasheet — DS-7616NXI-K1 (B), 16-ch 1U K Series AcuSense 4K NVR | V4.74.000, 2023-02-09 | https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000026/OFR000042/M000058877/Data_Sheet/Datasheet-of-DS-7616NXI-K1_NVRB_V4.74.000_20230209.pdf | hikvision.com (official) |
| S2 | Datasheet — DS-7616NXI-K2/16P AcuSense NVR | V4.74.000, 2023-02-09 | https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000026/OFR000042/M000058884/Data_Sheet/Datasheet-of-DS-7616NXI-K2_16P_V4.74.000_20230209.pdf | hikvision.com (official) |
| S3 | Datasheet — DS-7716NXI-K4-UHK, 16-ch 1.5U K Series AcuSense 4K NVR (available model: DS-7716NXI-K4) | V4.74.000, 2023-05-06 | https://www.hikvision.com/content/dam/hikvision/en/support/regional-materials/pakistan-/DatasheetofDS-7716NXI-K4-UHK_V4.74.000_060523-.pdf | hikvision.com (official, Pakistan regional) |
| S4 | Datasheet — DS-7600NI-I2/P Series NVR (DS-7608/7616/7632NI-I2/…P) | V4.63.000, 2023-04-17 | https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000026/OFR000040/M000000574/Data_Sheet/Datasheet-of-DS-7600NI-I2_P_NVRD_V4.63.000_20230417.pdf | hikvision.com (official) |
| S5 | Datasheet — iDS-7200HQHI-M1/FA(C) Series Turbo AcuSense DVR | V4.26.130, 2021-05-17 | https://www.hikvision.com/content/dam/hikvision/uk/products/dvr/acusense/Datasheet-of-iDS-7200HQHI-M1_FA_V4.26.130_20210517.pdf | hikvision.com (official, UK) |
| S6 | Datasheet — DS-7204HGHI-K1 Turbo HD DVR | V4.70.160, 2023-08-22 | https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000132/S000000133/S000000138/OFR009576/M000018276/Data_Sheet/Datasheet-of_DS-7204HGHI-K1_V4.70.160_20230822.pdf | hikvision.com (official) |
| S7 | Datasheet — iDS-9600NXI-I8/X (C) DeepinMind Series NVR (iDS-9616/9632/9664NXI-I8/X) | V4.60.110, 2022-02-18 | https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000023/OFR000031/M000044744/Data_Sheet/Datasheet-of-iDS-9600NXI-I8_X_DeepinMind-NVRC_V4.60.110_20220218.pdf | hikvision.com (official) |
| S8 | Datasheet — iDS-7608NXI-M2/8P/X DeepinMind M Series NVR | V4.61.200, 2023-05-24 | https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000023/OFR000030/M000065350/Data_Sheet/Datasheet-of-iDS-7608NXI-M2_8P_X-DeepinMind-NVR_V4.61.200_20230524.pdf | hikvision.com (official) |
| S9 | Intelligent Security API (General Application) Developer Guide | Version 2.0, Sept 2019 (© Hangzhou Hikvision) | https://download.isecj.jp/catalog/misc/isapi.pdf | **Secondary host** (isecj.jp mirror); document is official Hikvision |
| S10 | Intelligent Security API (Metadata) Developer Guide | Version 2.6, Mar 2020 | https://enpinfo.hikvision.com/unzip/20201110210551_77443_doc/pdf.pdf | enpinfo.hikvision.com (official Hikvision domain) |
| S11 | Perimeter Protection NVR Integration Solution | undated (retrieved 2026-09-10) | https://www.hikvisioneurope.com/eu/portal/portal/Technology%20Partner%20Program/02-Solutioins%20of%20Hikvision%20product%20integration/Perimeter%20Protection%20NVR%20Integration%20Solution.pdf | hikvisioneurope.com (Hikvision Europe partner portal) |
| S12 | DeepinMind Series NVRs — product landing (family context) | n/a, retrieved 2026-09-10 | https://www.hikvision.com/en/products/IP-Products/Network-Video-Recorders/DeepinMind-Series/ | hikvision.com (official) |
| S13 | Turbo HD AcuSense DVR — product landing (family context) | n/a, retrieved 2026-09-10 | https://www.hikvision.com/en/products/Turbo-HD-Products/DVR/AcuSense-Series/ | hikvision.com (official) |
| S14 | K-Series NVRs with AcuSense — flyer (family context) | n/a, retrieved 2026-09-10 | https://www.hikvision.com/content/dam/hikvision/usa/marketing/materials/flyers/35-k-series-nvr-with-acusense-flyer.pdf | hikvision.com (official, USA) |

Retrieval method note: Hikvision datasheet/guide PDFs did not render through the standard fetch tool; the exact files above were fetched to a local scratch folder and read verbatim (read-only). Nothing was executed or committed.

---

## Conflicts, caveats & gaps log

1. **Recorder-side vs camera-side is model-specific, not family-wide.** Recorder-side Perimeter Protection = 1-ch (DS-7616NXI-K1) but 2-ch (DS-7616NXI-K2/16P and DS-7716NXI-K4) — identical K-series, different capacity [S1][S2][S3]. Never inherit a capability across a series.
2. **"AI NVR" ≠ every analytic runs on the NVR.** On DeepinMind (models 7-8), **ANPR, people counting and heat map are camera-side**, while face-recognition/perimeter/video-structuralization/throwing-objects run on the NVR [S7][S8]. The I-series NVR (model 4) has **no** recorder-side AI on its datasheet at all [S4].
3. **Mutual-exclusion rules differ per model** and must be encoded before WatchLog toggles anything: K-series NVR & Turbo AcuSense DVR = exactly **one** of {face, MD2.0, perimeter} [S1][S2][S3][S5]; DeepinMind-96 = **up to two** engines (reduced capacity) [S7]; DeepinMind-M (7608) = **one** engine only [S8]. Enhanced-IP mode on the DVR can also disable smart events [S5].
4. **API family is not itemized on every datasheet.** Only models 1,2,3,8 print an explicit "ISAPI; SDK; ONVIF" API row [S1][S2][S3][S8]. Models 4,5,6,7 list network protocols (incl. NTP) but **do not** itemize ISAPI/SDK (model 5 lists ONVIF; model 6 lists neither ONVIF nor ISAPI). Platform-level ISAPI/SDK ([S9][S10]) very likely applies, but is **UNKNOWN at model level** for 4/5/6/7 — do not assert.
5. **Accessible ISAPI guides are camera-oriented.** [S9] (General Application V2.0) lists DS-2CD cameras as related products; [S10] (Metadata V2.6) lists radar/PTZ cameras. They authoritatively define the ISAPI surface (System/time, NTP, Storage, InputProxy, Streaming picture, event tokens), but an **NVR/DVR-specific ISAPI guide was not obtained**. Consequences: (a) exact per-detector Smart **write** paths (`/ISAPI/Smart/LineDetection/<ID>` etc.) are **UNKNOWN**; (b) NVR channel-proxied smart config semantics need the NVR guide.
6. **Secondary host for [S9].** The General Application guide copy came from `isecj.jp` (a distributor mirror), not a hikvision.com domain. Content is treated as official-by-document; host flagged. Re-source from Hikvision's own portal before production reliance.
7. **Regional-variant suffix.** [S3] is the **-UHK** (Pakistan) regional datasheet; its "Available Model" resolves to **DS-7716NXI-K4**. Regional suffixes (e.g. -UHK, /P, -K, -I) can change bundled options — preserve them.
8. **Firmware applicability = datasheet baseline only.** Each row's capabilities are tied to the datasheet's stated doc/firmware version (e.g. V4.74.000). A deployed unit may run different firmware with different limits. **Not FIELD-VERIFIED** — WatchLog Hikvision support is CODE-only; treat every row as OFFICIAL-DOCUMENTED, never as validated against a physical recorder.
9. **DS-7600NI-I2/P vs "M/-I2 AcuSense".** The requested "DS-7600NI-M/-I2 AcuSense" line: the copy sourced here (DS-7600NI-**I2/P**, [S4]) documents only **camera-side** smart functions. A separate DS-7600NI-**M** (budget AcuSense) datasheet was **not** captured → its recorder-side AcuSense status is a **gap/UNKNOWN** here.
10. **Not itemized anywhere on datasheets:** per-detector sensitivity values, geometry/smart-plan region limits, and arming-schedule granularity. These are configured via GUI/ISAPI per channel (Table D) but have no datasheet-level numbers → UNKNOWN until pulled from an NVR user manual or live device.
