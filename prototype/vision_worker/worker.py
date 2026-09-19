#!/usr/bin/env python3
"""WatchLog private snapshot vision worker.

Runs on WatchLog-controlled Coolify infrastructure. It claims only server-authorized
snapshot jobs from Supabase, sends each image to a private Ollama multimodal model,
stores structured visual findings, and creates a day rollup when the day's queue is
complete. Raw CCTV bytes are never written to Git and are not sent to third-party AI.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
VISION_MODEL = os.getenv("VISION_MODEL", "gemma3:4b")
WORKER_ID = os.getenv("WORKER_ID", socket.gethostname())[:120]
BATCH_SIZE = max(1, min(16, int(os.getenv("VISION_BATCH_SIZE", "4"))))
POLL_SECONDS = max(1, int(os.getenv("VISION_POLL_SECONDS", "5")))
AUTO_PULL = os.getenv("VISION_AUTO_PULL", "true").lower() in ("1", "true", "yes", "on")
ANALYSIS_VERSION = "snapshot-vision-v1"
SUMMARY_VERSION = "visual-day-v1"
PORT = int(os.getenv("PORT", "8640"))
MEDIA_ENDPOINT = os.getenv("MEDIA_ENDPOINT", "http://minio:9000").rstrip("/")
MEDIA_ACCESS_KEY = os.environ["WATCHLOG_MEDIA_ACCESS_KEY"]
MEDIA_SECRET_KEY = os.environ["WATCHLOG_MEDIA_SECRET_KEY"]
MEDIA_BUCKET = os.getenv("WATCHLOG_MEDIA_BUCKET", "watchlog-cctv")
MEDIA_RETENTION_DAYS = max(1, int(os.getenv("WATCHLOG_MEDIA_RETENTION_DAYS", "30")))

session = requests.Session()
session.headers.update({
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
})

s3 = boto3.client(
    "s3",
    endpoint_url=MEDIA_ENDPOINT,
    aws_access_key_id=MEDIA_ACCESS_KEY,
    aws_secret_access_key=MEDIA_SECRET_KEY,
    region_name="us-east-1",
)

state = {
    "ready": False,
    "model_ready": False,
    "processed": 0,
    "failed": 0,
    "last_error": None,
    "last_success_at": None,
}


def log(msg: str) -> None:
    print(f"[vision-worker] {msg}", flush=True)


def rpc(name: str, payload: dict, timeout: int = 60):
    r = session.post(f"{SUPABASE_URL}/rest/v1/rpc/{name}", json=payload, timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"{name}: HTTP {r.status_code} {r.text[:240]}")
    if not r.text:
        return None
    return r.json()


SNAPSHOT_SCHEMA = {
    "type": "object",
    "required": [
        "summary", "people_count", "people", "occupied", "activity",
        "restricted_area", "unusual", "unusual_reason", "quality"
    ],
    "properties": {
        "summary": {"type": "string"},
        "people_count": {"type": "integer", "minimum": 0},
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": "string"},
                    "activity": {"type": "string"},
                    "posture": {"type": "string"},
                    "upper_clothing": {"type": "string"},
                    "lower_clothing": {"type": "string"},
                    "carrying": {"type": "string"}
                },
                "required": ["position", "activity", "posture", "upper_clothing", "lower_clothing", "carrying"]
            }
        },
        "occupied": {"type": "boolean"},
        "activity": {"type": "string"},
        "restricted_area": {
            "type": "object",
            "required": ["relevant", "near_access_point", "entry_visible", "exit_visible"],
            "properties": {
                "relevant": {"type": "boolean"},
                "near_access_point": {"type": "boolean"},
                "entry_visible": {"type": "boolean"},
                "exit_visible": {"type": "boolean"}
            }
        },
        "unusual": {"type": "boolean"},
        "unusual_reason": {"type": "string"},
        "quality": {"type": "string", "enum": ["usable", "partly_obscured", "dark", "blurred", "unusable"]}
    }
}

DAY_SCHEMA = {
    "type": "object",
    "required": [
        "owner_summary", "overall", "first_activity", "last_activity",
        "areas", "restricted_area", "notable", "limitations"
    ],
    "properties": {
        "owner_summary": {"type": "string"},
        "overall": {"type": "string", "enum": ["routine", "attention", "serious"]},
        "first_activity": {"type": "string"},
        "last_activity": {"type": "string"},
        "areas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["area", "summary"],
                "properties": {
                    "area": {"type": "string"},
                    "summary": {"type": "string"}
                }
            }
        },
        "restricted_area": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["time", "summary"],
                "properties": {
                    "time": {"type": "string"},
                    "summary": {"type": "string"}
                }
            }
        },
        "notable": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}}
    }
}


def ensure_media_store() -> bool:
    try:
        try:
            s3.head_bucket(Bucket=MEDIA_BUCKET)
        except Exception:
            s3.create_bucket(Bucket=MEDIA_BUCKET)
        # MinIO supports the S3 lifecycle API. Keep the media mirror bounded.
        try:
            s3.put_bucket_lifecycle_configuration(
                Bucket=MEDIA_BUCKET,
                LifecycleConfiguration={
                    "Rules": [{
                        "ID": "watchlog-cctv-retention",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": MEDIA_RETENTION_DAYS},
                    }]
                },
            )
        except Exception as exc:
            log(f"media retention policy warning: {type(exc).__name__}")
        return True
    except Exception as exc:
        state["last_error"] = f"media store unavailable: {type(exc).__name__}"
        log(state["last_error"])
        return False


def media_key(job: dict) -> str:
    tz = job.get("timezone") or "Asia/Karachi"
    dt = datetime.fromisoformat(str(job["captured_at"]).replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
    ext = "png" if "png" in str(job.get("content_type") or "").lower() else "jpg"
    channel = str(job.get("channel") or "unknown").replace("/", "_")
    return f"{job['tenant_id']}/{job['site_id']}/{dt:%Y/%m/%d}/ch-{channel}/{job['event_id']}.{ext}"


def load_and_mirror_image(job: dict) -> str:
    existing_key = job.get("media_key")
    if existing_key:
        obj = s3.get_object(Bucket=job.get("media_bucket") or MEDIA_BUCKET, Key=existing_key)
        raw = obj["Body"].read()
        digest = hashlib.sha256(raw).hexdigest()
        expected = str(job.get("media_sha256") or "")
        if expected and digest != expected:
            raise RuntimeError("private media integrity check failed")
        return base64.b64encode(raw).decode("ascii")

    b64 = job.get("image_b64")
    if not b64:
        raise RuntimeError("snapshot bytes unavailable")
    raw = base64.b64decode(b64, validate=True)
    key = media_key(job)
    digest = hashlib.sha256(raw).hexdigest()
    s3.put_object(
        Bucket=MEDIA_BUCKET,
        Key=key,
        Body=io.BytesIO(raw),
        ContentLength=len(raw),
        ContentType=job.get("content_type") or "image/jpeg",
        Metadata={
            "sha256": digest,
            "site-id": str(job.get("site_id") or ""),
            "event-id": str(job.get("event_id") or ""),
        },
    )
    rpc("wl_vision_mark_media", {
        "p_event_id": int(job["event_id"]),
        "p_bucket": MEDIA_BUCKET,
        "p_key": key,
        "p_sha256": digest,
        "p_bytes": len(raw),
    }, timeout=30)
    job["media_bucket"] = MEDIA_BUCKET
    job["media_key"] = key
    job["media_sha256"] = digest
    job["media_bytes"] = len(raw)
    job["image_b64"] = None
    return base64.b64encode(raw).decode("ascii")


def ensure_model() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=8)
        r.raise_for_status()
        names = {m.get("name") for m in (r.json().get("models") or [])}
        if VISION_MODEL in names or any(n and n.split(":")[0] == VISION_MODEL.split(":")[0] for n in names):
            state["model_ready"] = True
            return True
        if not AUTO_PULL:
            state["last_error"] = f"vision model {VISION_MODEL} is not installed"
            return False
        log(f"pulling vision model {VISION_MODEL} (first start only)")
        p = requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": VISION_MODEL, "stream": False},
            timeout=3600,
        )
        p.raise_for_status()
        state["model_ready"] = True
        log(f"vision model ready: {VISION_MODEL}")
        return True
    except Exception as exc:
        state["last_error"] = f"ollama unavailable: {type(exc).__name__}"
        state["model_ready"] = False
        log(state["last_error"])
        return False


def ollama_chat(messages: list, schema: dict, timeout: int = 180) -> dict:
    payload = {
        "model": VISION_MODEL,
        "stream": False,
        "messages": messages,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 8192},
        "keep_alive": "30m",
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    content = ((r.json().get("message") or {}).get("content") or "").strip()
    out = json.loads(content)
    if not isinstance(out, dict):
        raise ValueError("model did not return an object")
    return out


def analyze_snapshot(job: dict) -> dict:
    camera = job.get("camera") or "camera"
    prompt = f"""
You are reviewing one CCTV still from {camera} for a professional office security report.

Describe only what is visibly supported by this single image.
Do NOT identify any person or guess a name. Do not infer ethnicity, religion, health,
criminality, or other sensitive traits. Do not guess that someone is staff merely from
appearance. Use neutral phrases such as "one person" or "two people".
Do not turn a person being present into an incident.
For restricted-area cameras, mark entry_visible/exit_visible only when the image itself
visibly supports an entry or exit, not merely because a person is near a door.
Keep summary and activity concise and factual.
Clothing descriptors are allowed only to help correlate adjacent CCTV frames.
If the image is too poor to support a conclusion, say so through quality.
""".strip()
    image_b64 = load_and_mirror_image(job)
    result = ollama_chat([
        {"role": "user", "content": prompt, "images": [image_b64]}
    ], SNAPSHOT_SCHEMA)

    # Application-level normalization: do not allow negative/absurd counts.
    try:
        result["people_count"] = max(0, min(50, int(result.get("people_count", 0))))
    except Exception:
        result["people_count"] = 0
    result["people"] = (result.get("people") or [])[:result["people_count"] or 0]
    result["summary"] = str(result.get("summary") or "Visual review completed.")[:500]
    result["activity"] = str(result.get("activity") or "")[:300]
    result["unusual_reason"] = str(result.get("unusual_reason") or "")[:300]
    return result


def summarize_day(day: dict) -> dict:
    tz = day.get("timezone") or "Asia/Karachi"
    frames = day.get("frames") or []
    compact = []
    for f in frames:
        compact.append({
            "time": f.get("captured_at"),
            "camera": f.get("camera"),
            "people_count": f.get("people_count"),
            "summary": f.get("summary"),
            "activity": f.get("activity"),
            "restricted_area": f.get("restricted_area"),
            "unusual": f.get("unusual"),
            "unusual_reason": f.get("unusual_reason"),
            "quality": f.get("quality"),
        })

    prompt = f"""
Prepare a concise owner-facing operational summary for {day.get('date')} in timezone {tz}.
The input is a chronological list of visual observations from CCTV snapshots.

Rules:
- Consolidate repeated adjacent frames into continuous activity; never count frames as people or visits.
- Do not invent identities. Say "a person", "people", or "appears to be the same person" only when
  clothing/location/timing make that visually plausible.
- A serious incident requires a concrete visible security/safety problem. Ordinary office presence,
  door approach, or restricted-area proximity alone is not a serious incident.
- Restricted-area entries/exits must be based on visible evidence, not camera alert labels.
- Report the first and last VISUALLY OBSERVED activity; never claim those are the actual office
  opening/closing unless the observations clearly cover those boundaries.
- Focus on what a business owner would want to know: overall day, meaningful occupancy, restricted
  area activity, unusual behaviour, and important limitations.
- Use Pakistan-friendly 12-hour times such as 2:15 PM.
- No technical terms about models, detections, confidence, databases, pipelines, or AI internals.

OBSERVATIONS:
{json.dumps(compact, separators=(',', ':'))}
""".strip()
    return ollama_chat([{"role": "user", "content": prompt}], DAY_SCHEMA, timeout=240)


def local_day(job: dict) -> str:
    tz = job.get("timezone") or "Asia/Karachi"
    dt = datetime.fromisoformat(str(job["captured_at"]).replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz)).date().isoformat()


def process_batch() -> int:
    jobs = rpc("wl_vision_claim_snapshots", {
        "p_limit": BATCH_SIZE,
        "p_worker_id": WORKER_ID,
    }, timeout=60) or []
    if not jobs:
        return 0

    touched = set()
    for job in jobs:
        event_id = int(job["event_id"])
        try:
            analysis = analyze_snapshot(job)
            rpc("wl_vision_complete_snapshot", {
                "p_event_id": event_id,
                "p_model": VISION_MODEL,
                "p_analysis_version": ANALYSIS_VERSION,
                "p_analysis": analysis,
            }, timeout=60)
            touched.add((job["site_id"], local_day(job)))
            state["processed"] += 1
            state["last_success_at"] = datetime.utcnow().isoformat() + "Z"
            state["last_error"] = None
            log(f"reviewed event {event_id} — {job.get('camera')}: {analysis.get('summary','')[:110]}")
        except Exception as exc:
            state["failed"] += 1
            state["last_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            try:
                rpc("wl_vision_fail_snapshot", {
                    "p_event_id": event_id,
                    "p_error": state["last_error"],
                }, timeout=30)
            except Exception:
                pass
            log(f"event {event_id} failed: {state['last_error']}")

    # Finalize a day only after every queued snapshot for that day has been analyzed.
    for site_id, day_text in touched:
        try:
            day = rpc("wl_vision_day_for_worker", {
                "p_site_id": site_id,
                "p_date": day_text,
            }, timeout=60) or {}
            if int(day.get("snapshots_total") or 0) > 0 and int(day.get("pending") or 0) == 0:
                summary = summarize_day(day)
                rpc("wl_vision_save_day_summary", {
                    "p_site_id": site_id,
                    "p_date": day_text,
                    "p_model": VISION_MODEL,
                    "p_summary_version": SUMMARY_VERSION,
                    "p_summary": summary,
                }, timeout=60)
                log(f"day summary ready for {day_text}: {summary.get('owner_summary','')[:140]}")
        except Exception as exc:
            log(f"day summary deferred for {day_text}: {type(exc).__name__}: {str(exc)[:160]}")
    return len(jobs)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404); self.end_headers(); return
        body = json.dumps(state).encode()
        self.send_response(200 if state["ready"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def serve_health():
    HTTPServer(("0.0.0.0", PORT), Health).serve_forever()


def main():
    threading.Thread(target=serve_health, daemon=True).start()
    while not ensure_media_store():
        time.sleep(20)
    while not ensure_model():
        time.sleep(20)
    state["ready"] = True
    log(f"ready — model={VISION_MODEL}, batch={BATCH_SIZE}")
    while True:
        try:
            n = process_batch()
            if n == 0:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            state["last_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            log(f"loop error: {state['last_error']}")
            time.sleep(max(POLL_SECONDS, 10))


if __name__ == "__main__":
    main()
