#!/usr/bin/env python3
"""Generator for 0077 recorder-capability KB batch 3 (8 high-priority PK-deployment models).
All OFFICIAL_DOCUMENTED; honest source caveats (mirror / substitution / translated) in notes."""
from pathlib import Path

EC = "OFFICIAL_DOCUMENTED"
CAPS = ["human_vehicle_classification","line_crossing","intrusion","face_detection","face_recognition",
        "anpr_lpr","people_counting","heatmap","video_loss","tamper","time_ntp_config"]

def A(loc,c=""):   return dict(v="supported",loc=loc,r=True,w=True,s="safe_write",c=c)
def BYCAM(c=""):   return dict(v="by_camera",loc="camera",r=None,w=None,s="na",c=c)
def UNS(c=""):     return dict(v="unsupported",loc="na",r=None,w=None,s="na",c=c)
def UNK(c=""):     return dict(v="unknown",loc=None,r=None,w=None,s="na",c=c)
def EV(c=""):      return dict(v="supported",loc="recorder",r=True,w=False,s="read",c=c)
def NTP():         return dict(v="supported",loc="recorder",r=True,w=True,s="safe_write",c="")

SOURCES = [
 ("DAHUA-XVR-S16","Dahua","DH-XVR5216AN-I3 datasheet (XVR5000-I3 WizSense)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5216AN-I3_datasheet_20240722.pdf","Rev002 2024","2024-07-22","official datasheet"),
 ("DAHUA-XVR-S17","Dahua","DH-XVR5232AN-I3 datasheet (XVR5000-I3 WizSense, HW V2.0)","https://materialfile.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR5232AN-I3_V2_datasheet_20241114.pdf","Rev002 2024","2024-11-14","official datasheet; facts gated to HW V2.0"),
 ("DAHUA-NVR-S24","Dahua","DHI-NVR4216-16P-I datasheet (NVR4000-I AI series)","https://www.dahuasecurity.com/","Rev001.001 2019","2019-02-26","MIRROR: model discontinued/purged from dahuasecurity.com; genuine manufacturer PDF via distributor mirror"),
 ("DAHUA-NVR-S25","Dahua","DHI-NVR5216-16P-I/L datasheet (NVR5000-I/L WizMind AcuPick)","https://www.dahuasecurity.com/products/All-Products/Network-Recorders/WizMind-Series/NVR5-IL/2HDD/NVR5216-16P-I/L","Rev001.001 2021","2021-09-28","TRANSLATED: readable copy was the Chinese-language edition; EN by-recorder/by-camera split cross-referenced"),
 ("HIK-DVR-S34","Hikvision","iDS-7208HQHI-M2/FA (via M1/FA datasheet)","https://pro-av.hikvision.com/","V4.71.000","2023-06-21","SUBSTITUTION: exact M2/FA not published; M1/FA (same platform, 1->2 HDD) used"),
 ("HIK-NVR-S44","Hikvision","DS-7616NI-K2/16P datasheet (Pro K NVR)","https://pro-av.hikvision.com/","V4.71.410","2023-09-20","official datasheet; all analytics camera-side"),
 ("HIK-NVR-S45","Hikvision","iDS-7716NXI-I4/16P/X(B) datasheet (DeepinMind NVR)","https://pro-av.hikvision.com/","V4.1.70","2019-08-13","official datasheet; 2019 baseline rev — newer revs may itemize more"),
 ("DAHUA-XVR-S18","Dahua","DH-XVR1B04H-I datasheet (Cooper-I WizSense)","https://material.dahuasecurity.com/uploads/cpq/prm-os-srv-res/smart/datasheetzipfiles/XVR1B04H-I_datasheet_20220530.pdf","V2 Rev002","2022-05-30","official datasheet/HTML spec"),
]

MODELS = [
 ("Dahua","DH-XVR5216AN-I3","XVR5000-I3","","DAHUA-XVR-S16",{
   "human_vehicle_classification":A("both","SMD Plus recorder 16ch + camera 24ch; AI Mode exclusive Face|IVS&SMD|SMD; IP extension disables"),
   "line_crossing":A("recorder","Perimeter recorder 2ch adv/4ch gen, 10 IVS/ch; needs AI Mode IVS&SMD"),
   "intrusion":A("recorder","Perimeter recorder 2ch adv/4ch gen; needs AI Mode IVS&SMD"),
   "face_detection":A("recorder","D:2 (12 face imgs/s/ch); needs AI Mode Face"),
   "face_recognition":A("recorder","D:2; DB 10 dbs/10000 imgs; needs AI Mode Face"),
   "anpr_lpr":UNS("exhaustive Intelligent Alarm: Face det/rec, Perimeter, SMD Plus"),
   "people_counting":UNS("exhaustive Intelligent Alarm"),"heatmap":UNS("exhaustive Intelligent Alarm"),
   "video_loss":EV("Anomaly Alarm: video loss + camera offline"),"tamper":EV("video tampering"),"time_ntp_config":NTP()}),
 ("Dahua","DH-XVR5232AN-I3","XVR5000-I3","HW2.0","DAHUA-XVR-S17",{
   "human_vehicle_classification":A("both","SMD Plus recorder 32ch + camera 48ch; Face conflicts with SMD/Perimeter; Encode-Enhancement disables AI"),
   "line_crossing":A("recorder","Perimeter recorder 2ch adv/8ch gen (8ch), 10 IVS/ch"),
   "intrusion":A("recorder","Perimeter recorder 2ch adv/8ch gen"),
   "face_detection":A("recorder","D:2 (12 face imgs/s/ch)"),
   "face_recognition":A("recorder","D:2; DB 10 dbs/10000 imgs; conflicts with SMD/Perimeter"),
   "anpr_lpr":UNS("exhaustive Intelligent Alarm"),"people_counting":UNS("exhaustive Intelligent Alarm"),"heatmap":UNS("exhaustive Intelligent Alarm"),
   "video_loss":EV("video loss + camera offline"),"tamper":EV("video tampering"),"time_ntp_config":NTP()}),
 ("Dahua","DHI-NVR4216-16P-I","NVR4000-I","","DAHUA-NVR-S24",{
   "human_vehicle_classification":A("recorder","Perimeter human/vehicle secondary recognition; metadata search"),
   "line_crossing":A("recorder","Perimeter recorder 4ch, 10 IVS rules; tripwire"),
   "intrusion":A("recorder","Perimeter recorder 4ch; intrusion"),
   "face_detection":A("recorder","face capture/detection with FD camera"),
   "face_recognition":A("recorder","2ch video / 8ch picture stream FR; DB 10 dbs/20000 imgs; 12 face pics/s"),
   "anpr_lpr":BYCAM("ANPR by Dahua ITC camera; recorder does list mgmt + search only"),
   "people_counting":BYCAM("AI-series application; camera-side"),
   "heatmap":UNK("not mentioned in datasheet"),
   "video_loss":EV("Video Detection: Video Loss"),"tamper":EV("Tampering"),"time_ntp_config":NTP()}),
 ("Dahua","DHI-NVR5216-16P-I/L","NVR5000-I/L","","DAHUA-NVR-S25",{
   "human_vehicle_classification":A("both","SMD Plus recorder 8ch + by camera; perimeter human/vehicle; AcuPick max 16ch"),
   "line_crossing":A("recorder","Perimeter recorder 4ch (up to 12ch total), 10 IVS rules"),
   "intrusion":A("recorder","Perimeter recorder 4ch; human/vehicle classification"),
   "face_detection":A("recorder","D:2"),
   "face_recognition":A("recorder","D:2 (16 face pics/s); 4ch video/16ch picture FR; DB 20 dbs/200000 imgs"),
   "anpr_lpr":BYCAM("Dahua ANPR = AI-by-camera (WizMind); recorder ingests"),
   "people_counting":BYCAM("Dahua People Counting = AI-by-camera"),
   "heatmap":BYCAM("Heat Map = AI-by-camera"),
   "video_loss":EV("WizMind NVR video-detection"),"tamper":EV("Tampering/Camera Masking"),"time_ntp_config":NTP()}),
 ("Hikvision","iDS-7208HQHI-M2/FA","Turbo AcuSense HQHI-M2/FA","","HIK-DVR-S34",{
   "human_vehicle_classification":A("recorder","MD2.0 all analog ch; face-comparison|MD2.0|perimeter mutually exclusive"),
   "line_crossing":A("recorder","deep-learning perimeter (line crossing + intrusion); mutually exclusive with MD2.0/face"),
   "intrusion":A("recorder","deep-learning perimeter; mutually exclusive with MD2.0/face"),
   "face_detection":A("recorder","face capture/comparison pipeline"),
   "face_recognition":A("recorder","1-ch face picture comparison (HD analog); up to 16 libs / 500 pics total"),
   "anpr_lpr":UNK("not documented"),"people_counting":UNK("not documented"),"heatmap":UNK("not documented"),
   "video_loss":UNK("datasheet silent — no exception row"),"tamper":UNK("datasheet silent"),"time_ntp_config":NTP()}),
 ("Hikvision","DS-7616NI-K2/16P","Pro K NVR","","HIK-NVR-S44",{
   "human_vehicle_classification":BYCAM("no recorder AI engine; camera special smart functions only"),
   "line_crossing":BYCAM("special camera smart functions: VCA (motion, line crossing, intrusion)"),
   "intrusion":BYCAM("special camera smart functions"),
   "face_detection":UNK("not listed; camera-VCA passthrough"),
   "face_recognition":UNS("K-series has no recorder-side facial-recognition engine"),
   "anpr_lpr":BYCAM("ANPR = camera smart function"),
   "people_counting":UNK("not documented; camera-dependent"),"heatmap":UNK("not documented; camera-dependent"),
   "video_loss":UNK("minimal datasheet — no exception row"),"tamper":UNK("datasheet silent"),"time_ntp_config":NTP()}),
 ("Hikvision","iDS-7716NXI-I4/16P/X","DeepinMind NVR","","HIK-NVR-S45",{
   "human_vehicle_classification":A("recorder","16-ch human/vehicle recognition; facial-analytics & human/vehicle mutually exclusive"),
   "line_crossing":A("recorder","recorder-side multiple VCA + human/vehicle analysis (not itemized by name)"),
   "intrusion":A("recorder","recorder-side VCA / human-vehicle analysis"),
   "face_detection":A("recorder","8-ch face capture"),
   "face_recognition":A("recorder","16-ch face comparison; 32 libraries / 100000 pictures; mutually exclusive with human/vehicle"),
   "anpr_lpr":UNK("not documented in this (B) rev"),"people_counting":UNK("not documented"),"heatmap":UNK("not documented"),
   "video_loss":UNK("datasheet silent"),"tamper":UNK("datasheet silent"),"time_ntp_config":NTP()}),
 ("Dahua","DH-XVR1B04H-I","Cooper-I","","DAHUA-XVR-S18",{
   "human_vehicle_classification":A("recorder","SMD Plus recorder 4ch; IP extension disables SMD"),
   "line_crossing":UNS("Cooper-I: only recorder AI is SMD Plus; no perimeter"),
   "intrusion":UNS("Cooper-I: no perimeter"),"face_detection":UNS("not on Cooper-I"),
   "face_recognition":UNS("not on Cooper-I"),"anpr_lpr":UNS("not on Cooper-I"),
   "people_counting":UNS("not on Cooper-I"),"heatmap":UNS("not on Cooper-I"),
   "video_loss":EV("Video loss"),"tamper":EV("Video tampering"),"time_ntp_config":NTP()}),
]

def q(s): return "null" if s is None else "'"+str(s).replace("'","''")+"'"
def b(v): return "null" if v is None else ("true" if v else "false")

rows=[]
for vendor,model,series,fw,src,caps in MODELS:
    for cap in CAPS:
        c=caps[cap]
        rows.append(f"('{vendor}','{model}',{q(series)},{q(fw)},'{cap}','{c['v']}','{EC}',{q(c['loc'])},{b(c['r'])},{b(c['w'])},{q(c['s'])},{q(c['c'])},array['{src}'])")

src_rows=",\n ".join(f"('{sid}','{v}','official',{q(dn)},{q(url)},{q(ver)},'{dt}'::date,{q(note)})" for sid,v,dn,url,ver,dt,note in SOURCES)

header="""-- =====================================================================
-- 0077 — Recorder Capability KB, batch 3 (8 high-priority models common in Pakistan CCTV).
-- All OFFICIAL_DOCUMENTED. Where a model could not be sourced cleanly on the official domain,
-- the provenance note records the caveat HONESTLY (mirror / substitution / translated); the
-- fact is still datasheet-grade, never field-verified. Datasheet silence -> 'unknown'; an
-- exhaustive Intelligent-Alarm list -> 'unsupported'; camera-side AI -> 'by_camera'.
-- Generated from a reviewed matrix (supabase/_gen_0077.py).
-- =====================================================================

insert into public.recorder_capability_sources (id, vendor, source_type, doc_name, url, doc_version, retrieved_date, notes) values
 %s
on conflict (id) do nothing;

insert into public.recorder_capabilities
 (vendor, model, series, firmware_applicability, capability, verdict, evidence_class, ai_location, read_supported, write_supported, safety_class, constraints, source_ids) values
 %s
on conflict (vendor, model, capability, firmware_applicability) do nothing;
"""%(src_rows,",\n ".join(rows))

out=Path(__file__).resolve().parent/"migrations"/"0077_recorder_capability_kb_batch3.sql"
out.write_text(header,encoding="utf-8")
print(f"wrote {out} ({len(MODELS)} models, {len(rows)} rows, {len(SOURCES)} sources)")
