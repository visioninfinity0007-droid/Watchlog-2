#!/usr/bin/env python3
"""Generator for 0066 recorder-capability KB seed (18 entry models, all OFFICIAL_DOCUMENTED).
Emits static SQL matching the 0061 seed pattern. Run once; the .sql is the checked-in artifact."""
from pathlib import Path

EC = "OFFICIAL_DOCUMENTED"
CAPS = ["human_vehicle_classification", "line_crossing", "intrusion", "face_detection",
        "face_recognition", "anpr_lpr", "people_counting", "heatmap",
        "video_loss", "tamper", "time_ntp_config"]


def A(loc, c=""):   # analytic supported
    return dict(v="supported", loc=loc, r=True, w=True, s="safe_write", c=c)
def BYCAM(c=""):
    return dict(v="by_camera", loc="camera", r=None, w=None, s="na", c=c)
def UNS(c=""):
    return dict(v="unsupported", loc="na", r=None, w=None, s="na", c=c)
def UNK(c=""):
    return dict(v="unknown", loc=None, r=None, w=None, s="na", c=c)
def EV(c=""):       # current-state event read (video_loss/tamper)
    return dict(v="supported", loc="recorder", r=True, w=False, s="read", c=c)
def NTP():
    return dict(v="supported", loc="recorder", r=True, w=True, s="safe_write", c="")
def SUPP_UNKLOC(c=""):   # documented supported, location not stated
    return dict(v="supported", loc=None, r=True, w=None, s="na", c=c)


SOURCES = [
 ("DAHUA-XVR-S10","Dahua","DH-XVR1B04-I datasheet (Cooper-I)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR1B04-I_V2_datasheet_20230302.pdf","V2 Rev002 2023","2023-03-02"),
 ("DAHUA-XVR-S11","Dahua","DH-XVR1B16-I datasheet (Cooper-I)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR1B16-I_datasheet_20220530.pdf","Rev002 2022","2022-05-30"),
 ("DAHUA-XVR-S12","Dahua","DH-XVR4104HS-I datasheet (XVR4000-I)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR4104HS-I_datasheet_20220905.pdf","Rev002 2022","2022-09-05"),
 ("DAHUA-XVR-S13","Dahua","DH-XVR4108HS-I datasheet (XVR4000-I)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR4108HS-I_datasheet_20220530.pdf","Rev002 2022","2022-05-30"),
 ("DAHUA-XVR-S14","Dahua","DH-XVR5104H-I3 datasheet (XVR5000-I3, HW v3.0)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5104H-I3_V3_datasheet_20240717.pdf","V3 Rev002 2024","2024-07-17"),
 ("DAHUA-XVR-S15","Dahua","DH-XVR5108H-I3 datasheet (XVR5000-I3, HW v3.0)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5108H-I3_V3_datasheet_20240717.pdf","V3 Rev002 2024","2024-07-17"),
 ("DAHUA-NVR-S20","Dahua","DHI-NVR4104HS-P-4KS2 datasheet (NVR4000-4KS2 Lite)","https://www.dahuasecurity.com/asset/upload/uploads/soft/20190115/DHI-NVR4104_4108HS-P-4KS2_datasheet_20190115.pdf","Rev001.001 2016","2019-01-15"),
 ("DAHUA-NVR-S21","Dahua","DHI-NVR4108HS-P-4KS2 datasheet (shared 2-model doc)","https://www.dahuasecurity.com/asset/upload/uploads/soft/20190115/DHI-NVR4104_4108HS-P-4KS2_datasheet_20190115.pdf","Rev001.001 2016","2019-01-15"),
 ("DAHUA-NVR-S22","Dahua","DHI-NVR2104HS-P-I2 datasheet (NVR2-I2 WizSense)","https://material.dahuasecurity.com/uploads/soft/20231018/NVR2104HS-P-I2_datasheet.pdf","Rev002 2022","2023-10-18"),
 ("DAHUA-NVR-S23","Dahua","DHI-NVR2108HS-8P-I2 datasheet (NVR2-I2 WizSense)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/NVR2108HS-8P-I2_datasheet_20220614.pdf","Rev002 2022","2022-06-14"),
 ("HIK-DVR-S30","Hikvision","DS-7104HGHI-K1 datasheet (Turbo HD DVR)","https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000132/S000000133/S000000138/OFR009576/M000018278/Data_Sheet/Datasheet-of_DS-7104HGHI-K1_V4.70.160_20230822.pdf","V4.70.160","2023-08-22"),
 ("HIK-DVR-S31","Hikvision","DS-7108HGHI-K1 datasheet (Turbo HD DVR)","https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000132/S000000133/S000000138/OFR009576/M000018279/Data_Sheet/Datasheet-of_DS-7108HGHI-K1_V4.70.160_20230822.pdf","V4.70.160","2023-08-22"),
 ("HIK-DVR-S32","Hikvision","iDS-7204HQHI-M1/S datasheet (Turbo AcuSense)","https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000132/S000000133/S000000821/OFR000174/M000007976/Data_Sheet/Datasheet-of-iDS-7204HQHI-M1_S_V4.71.000_20240110.pdf","V4.71.000","2024-01-10"),
 ("HIK-DVR-S33","Hikvision","iDS-7208HQHI-M1/S datasheet (Turbo AcuSense)","https://assets.hikvision.com/prd/public/all/doc/m000007977/Datasheet-of-iDS-7208HQHI-M1_S_V4.71.000_20230621.pdf","V4.71.000","2023-06-21"),
 ("HIK-NVR-S40","Hikvision","DS-7604NI-K1(B) datasheet (DS-7600NI-K1(B) series)","https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000026/OFR000040/M000000575/Data_Sheet/Datasheet-of_DS-7600NI-K1-B-NVR_3.4.96_20171025.pdf","V3.4.96","2017-10-25"),
 ("HIK-NVR-S41","Hikvision","DS-7608NI-K1(B) datasheet (shared series doc)","https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000007/S000000026/OFR000040/M000000575/Data_Sheet/Datasheet-of_DS-7600NI-K1-B-NVR_3.4.96_20171025.pdf","V3.4.96","2017-10-25"),
 ("HIK-NVR-S42","Hikvision","DS-7616NI-Q2(D) datasheet (Q-series Pro NVR)","https://assets.hikvision.com/prd/public/all/doc/m000000579/Datasheet-of-DS-7616NI-Q2-NVRD_V4.71.200_20221031.pdf","V4.71.200","2022-10-31"),
 ("HIK-NVR-S43","Hikvision","DS-7108NI-Q1/M(D) datasheet (Value 7 Series NVR)","https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000132/S000000007/S000000032/OFR000061/M000000621/Data_Sheet/Datasheet-of-DS-7108NI-Q1_M_Value-7-Series-NVR_V4.73.200_20230404.pdf","V4.73.200","2023-04-04"),
]

# (vendor, model, series, firmware, src, {cap: cell})
DVR_SILENT = "no intelligent-alarm row; datasheet silent on IVS/Face/Perimeter"
NO_EVENT_TABLE = "datasheet has no exception/event table"

MODELS = []

def cooper(model, src, hv_c, silent):
    caps = {"human_vehicle_classification": A("recorder", hv_c)}
    for c in ("line_crossing", "intrusion", "face_detection", "face_recognition", "anpr_lpr", "people_counting", "heatmap"):
        caps[c] = UNS(silent) if model == "DH-XVR1B04-I" else UNK(silent)
    caps["video_loss"] = EV(); caps["tamper"] = EV(); caps["time_ntp_config"] = NTP()
    return caps

MODELS.append(("Dahua","DH-XVR1B04-I","Cooper-I","","DAHUA-XVR-S10",
    cooper("DH-XVR1B04-I","DAHUA-XVR-S10","SMD Plus recorder D:4; AI disabled if IP extension enabled",
           "Intelligent Alarm row = SMD Plus only (exhaustive)")))
MODELS.append(("Dahua","DH-XVR1B16-I","Cooper-I","","DAHUA-XVR-S11",
    cooper("DH-XVR1B16-I","DAHUA-XVR-S11","SMD Plus recorder D:8 on 16-analog box; disabled if IP extension", DVR_SILENT)))
MODELS.append(("Dahua","DH-XVR4104HS-I","XVR4000-I","","DAHUA-XVR-S12",
    cooper("DH-XVR4104HS-I","DAHUA-XVR-S12","SMD Plus recorder D:4; disabled if IP extension", DVR_SILENT)))
MODELS.append(("Dahua","DH-XVR4108HS-I","XVR4000-I","","DAHUA-XVR-S13",
    cooper("DH-XVR4108HS-I","DAHUA-XVR-S13","SMD Plus recorder D:8; disabled if IP extension", DVR_SILENT)))

def i3(model, src, hv_c, face_det_c, face_rec_c):
    return {
      "human_vehicle_classification": A("recorder", hv_c),
      "line_crossing": A("recorder","Perimeter recorder 2ch adv/4ch gen, <=10 IVS/ch; needs AI Mode IVS&SMD"),
      "intrusion": A("recorder","Perimeter recorder 2ch adv/4ch gen; needs AI Mode IVS&SMD"),
      "face_detection": A("recorder", face_det_c),
      "face_recognition": A("recorder", face_rec_c),
      "anpr_lpr": UNS("Intelligent Alarm list exhaustive: Face det/rec, Perimeter, SMD Plus"),
      "people_counting": UNS("Intelligent Alarm list exhaustive"),
      "heatmap": UNS("Intelligent Alarm list exhaustive"),
      "video_loss": EV(), "tamper": EV(), "time_ntp_config": NTP()}

MODELS.append(("Dahua","DH-XVR5104H-I3","XVR5000-I3","HW3.0","DAHUA-XVR-S14",
    i3("DH-XVR5104H-I3","DAHUA-XVR-S14",
       "SMD recorder D:4 + camera 6; AI Mode exclusive Face|IVS&SMD|SMD; IP extension disables",
       "AI by recorder D:1; needs AI Mode Face","D:1; DB 10 dbs/10000 imgs; needs AI Mode Face")))
MODELS.append(("Dahua","DH-XVR5108H-I3","XVR5000-I3","HW3.0","DAHUA-XVR-S15",
    i3("DH-XVR5108H-I3","DAHUA-XVR-S15",
       "SMD recorder D:8 + camera 12; AI Mode exclusive; IP extension disables",
       "D:2; needs AI Mode Face","D:2; DB 10/10000; needs AI Mode Face")))

def lite4ks2(model, src):
    return {
      "human_vehicle_classification": UNK("no recorder-side AI; SMD not documented on this Lite gen"),
      "line_crossing": BYCAM("camera IVS recorded by NVR, not recorder-computed"),
      "intrusion": BYCAM("camera IVS recorded by NVR, not recorder-computed"),
      "face_detection": BYCAM("facial detection from IP cameras only"),
      "face_recognition": UNK("datasheet silent"),
      "anpr_lpr": UNK("datasheet silent"),
      "people_counting": BYCAM("business analytics from cameras"),
      "heatmap": BYCAM("business analytics from cameras"),
      "video_loss": EV("legacy Video Loss wording"),
      "tamper": EV("Tampering"), "time_ntp_config": NTP()}

MODELS.append(("Dahua","DHI-NVR4104HS-P-4KS2","NVR4000-4KS2 Lite","","DAHUA-NVR-S20", lite4ks2("DHI-NVR4104HS-P-4KS2","DAHUA-NVR-S20")))
MODELS.append(("Dahua","DHI-NVR4108HS-P-4KS2","NVR4000-4KS2 Lite","","DAHUA-NVR-S21", lite4ks2("DHI-NVR4108HS-P-4KS2","DAHUA-NVR-S21")))

def i2(model, src, hv_c, face_det_c, face_rec_c):
    return {
      "human_vehicle_classification": A("both", hv_c),
      "line_crossing": A("both","Perimeter AI-by-NVR D:1 + by-camera 4"),
      "intrusion": A("both","Perimeter AI-by-NVR D:1 + by-camera 4"),
      "face_detection": A("both", face_det_c),
      "face_recognition": A("both", face_rec_c),
      "anpr_lpr": UNK("datasheet silent"),
      "people_counting": UNK("datasheet silent"),
      "heatmap": UNK("datasheet silent"),
      "video_loss": EV("Camera offline anomaly + video loss general alarm"),
      "tamper": UNK("explicit tamper not itemized; privacy masking only"),
      "time_ntp_config": NTP()}

MODELS.append(("Dahua","DHI-NVR2104HS-P-I2","NVR2-I2 WizSense","","DAHUA-NVR-S22",
    i2("DHI-NVR2104HS-P-I2","DAHUA-NVR-S22","SMD Plus AI-by-NVR D:4 + by-camera 4",
       "AI-by-NVR D:1 + by-camera 4","AI-by-NVR D:1; DB 10 dbs/5000 imgs")))
MODELS.append(("Dahua","DHI-NVR2108HS-8P-I2","NVR2-I2 WizSense","","DAHUA-NVR-S23",
    i2("DHI-NVR2108HS-8P-I2","DAHUA-NVR-S23","SMD Plus AI-by-NVR D:4 + by-camera 6",
       "AI-by-NVR D:1 + by-camera 5","AI-by-NVR D:1; DB 10/5000; by-camera 5")))

def hghi(model, src):
    caps = {"human_vehicle_classification": A("recorder","Motion Detection 2.0 deep-learning human/vehicle, all analog ch")}
    for c in ("line_crossing","intrusion","face_detection","face_recognition","anpr_lpr","people_counting","heatmap"):
        caps[c] = UNS("smart section exhaustive = MD2.0 only; no perimeter/face")
    caps["video_loss"] = UNK(NO_EVENT_TABLE); caps["tamper"] = UNK(NO_EVENT_TABLE); caps["time_ntp_config"] = NTP()
    return caps

MODELS.append(("Hikvision","DS-7104HGHI-K1","Turbo HD DVR HGHI-K1","","HIK-DVR-S30", hghi("DS-7104HGHI-K1","HIK-DVR-S30")))
MODELS.append(("Hikvision","DS-7108HGHI-K1","Turbo HD DVR HGHI-K1","","HIK-DVR-S31", hghi("DS-7108HGHI-K1","HIK-DVR-S31")))

def hqhi(model, src, perim_c):
    return {
      "human_vehicle_classification": A("recorder","MD2.0 all analog ch; mutually exclusive with Perimeter"),
      "line_crossing": A("recorder", perim_c),
      "intrusion": A("recorder", perim_c),
      "face_detection": A("recorder","Facial Detection & Capture"),
      "face_recognition": UNS("/S variant is detection-only; no face recognition"),
      "anpr_lpr": UNK("smart section lists only MD2.0/Perimeter/Facial; silent on ANPR"),
      "people_counting": UNK("smart section lists only MD2.0/Perimeter/Facial; silent"),
      "heatmap": UNK("smart section lists only MD2.0/Perimeter/Facial; silent"),
      "video_loss": UNK(NO_EVENT_TABLE), "tamper": UNK(NO_EVENT_TABLE), "time_ntp_config": NTP()}

MODELS.append(("Hikvision","iDS-7204HQHI-M1/S","Turbo AcuSense HQHI-M1/S","","HIK-DVR-S32",
    hqhi("iDS-7204HQHI-M1/S","HIK-DVR-S32","Perimeter recorder D:2; MD2.0 and Perimeter cannot both be enabled")))
MODELS.append(("Hikvision","iDS-7208HQHI-M1/S","Turbo AcuSense HQHI-M1/S","","HIK-DVR-S33",
    hqhi("iDS-7208HQHI-M1/S","HIK-DVR-S33","Perimeter recorder D:4 (vs 2 on the 7204); MD2.0 and Perimeter mutually exclusive")))

def k1b(model, src):
    caps = {"human_vehicle_classification": UNK("no recorder AI engine; camera VCA generic, not itemized")}
    for c in ("line_crossing","intrusion","face_detection","face_recognition","anpr_lpr","people_counting","heatmap"):
        caps[c] = UNK("generic camera VCA, specific types not itemized — do not infer")
    caps["video_loss"] = UNK("no exception/event table (sparse 2017 datasheet)")
    caps["tamper"] = UNK("no exception/event table (sparse 2017 datasheet)")
    caps["time_ntp_config"] = NTP()
    return caps

MODELS.append(("Hikvision","DS-7604NI-K1(B)","DS-7600NI-K1(B)","","HIK-NVR-S40", k1b("DS-7604NI-K1(B)","HIK-NVR-S40")))
MODELS.append(("Hikvision","DS-7608NI-K1(B)","DS-7600NI-K1(B)","","HIK-NVR-S41", k1b("DS-7608NI-K1(B)","HIK-NVR-S41")))

MODELS.append(("Hikvision","DS-7616NI-Q2","Q-series Pro NVR","","HIK-NVR-S42", {
    "human_vehicle_classification": A("recorder","Motion Detection 2.0 Human/Vehicle Analysis recorder D:4 (not AcuSense)"),
    "line_crossing": BYCAM("configurable special camera smart functions; camera-side"),
    "intrusion": BYCAM("configurable special camera smart functions; camera-side"),
    "face_detection": UNK("not listed; silent"), "face_recognition": UNK("not listed; silent"),
    "anpr_lpr": UNK("not listed; silent"), "people_counting": UNK("not listed; silent"),
    "heatmap": UNK("not listed; silent"),
    "video_loss": UNK(NO_EVENT_TABLE), "tamper": UNK(NO_EVENT_TABLE), "time_ntp_config": NTP()}))

MODELS.append(("Hikvision","DS-7108NI-Q1/M","Value 7 Series NVR","","HIK-NVR-S43", {
    "human_vehicle_classification": UNK("no recorder AI engine / no MD2.0 row"),
    "line_crossing": SUPP_UNKLOC('documented "supports line crossing" but camera-vs-recorder location NOT stated'),
    "intrusion": SUPP_UNKLOC('documented "supports intrusion detection" but location NOT stated'),
    "face_detection": UNK("silent"), "face_recognition": UNK("silent"), "anpr_lpr": UNK("silent"),
    "people_counting": UNK("silent"), "heatmap": UNK("silent"),
    "video_loss": UNK(NO_EVENT_TABLE), "tamper": UNK(NO_EVENT_TABLE), "time_ntp_config": NTP()}))


def q(s):
    return "null" if s is None else "'" + str(s).replace("'", "''") + "'"

def b(v):
    return "null" if v is None else ("true" if v else "false")

rows = []
for vendor, model, series, fw, src, caps in MODELS:
    for cap in CAPS:
        cell = caps[cap]
        rows.append(
            f"('{vendor}','{model.replace(chr(39), chr(39)*2)}',{q(series)},{q(fw)},'{cap}',"
            f"'{cell['v']}','{EC}',{q(cell['loc'])},{b(cell['r'])},{b(cell['w'])},{q(cell['s'])},"
            f"{q(cell['c'])},array['{src}'])")

src_rows = ",\n ".join(
    f"('{sid}','{vend}','official',{q(dn)},{q(url)},{q(ver)},'{dt}'::date,'multi-model KB batch 2')"
    for sid, vend, dn, url, ver, dt in SOURCES)

header = """-- =====================================================================
-- 0066 — Recorder Capability KB, batch 2 (the broad multi-model loader 0061 deferred).
--
-- 18 entry Dahua/Hikvision XVR/NVR/DVR models, every fact OFFICIAL_DOCUMENTED from the
-- exact-model datasheet (none FIELD_VERIFIED). Silence is recorded as verdict 'unknown'
-- (never laundered into 'unsupported'); a datasheet whose Intelligent-Alarm list is
-- exhaustive yields 'unsupported'. Two hard rules stay structural:
--   * an 'unknown' verdict can never be read as unsupported,
--   * OFFICIAL documentation is never emitted as FIELD_VERIFIED.
-- Deliberate contrast preserved WITHIN a series: DH-XVR1B04-I lists an exhaustive
-- SMD-only alarm row (perimeter/face -> 'unsupported'), while its sibling DH-XVR1B16-I
-- has no such row (-> 'unknown'). The model must never generalize one across the other.
-- Generated from a reviewed capability matrix (supabase/_gen_0066.py).
-- =====================================================================

insert into public.recorder_capability_sources (id, vendor, source_type, doc_name, url, doc_version, retrieved_date, notes) values
 %s
on conflict (id) do nothing;

insert into public.recorder_capabilities
 (vendor, model, series, firmware_applicability, capability, verdict, evidence_class, ai_location, read_supported, write_supported, safety_class, constraints, source_ids) values
 %s
on conflict (vendor, model, capability, firmware_applicability) do nothing;
""" % (src_rows, ",\n ".join(rows))

out = Path(__file__).resolve().parent / "migrations" / "0066_recorder_capability_kb_batch2.sql"
out.write_text(header, encoding="utf-8")
print(f"wrote {out}  ({len(MODELS)} models, {len(rows)} capability rows, {len(SOURCES)} sources)")
