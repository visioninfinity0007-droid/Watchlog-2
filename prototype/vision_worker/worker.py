#!/usr/bin/env python3
"""WatchLog private snapshot vision worker.

Runs on WatchLog-controlled Coolify infrastructure. It claims only server-authorized
snapshot jobs from Supabase, sends each image to a private Ollama multimodal model,
stores structured visual findings, and creates a day rollup when the day's queue is
complete. Raw CCTV bytes are never written to Git and are not sent to third-party AI.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
import threading
import time

import boto3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# WatchLog AI harness rules for owner-visible text, compiled from ai-harness/core/customer-vocabulary.yaml
# by prototype/scripts/compile_harness_brief.py. The worker refuses to run without them, so an image
# worker can never be deployed outside the harness.
_HARNESS_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness_rules.generated.json")
with open(_HARNESS_RULES_PATH, encoding="utf-8") as _fh:
    _HARNESS = json.load(_fh)
HARNESS_OWNER_TEXT_RULES = _HARNESS["owner_text_rules"]
HARNESS_VOCABULARY_LINE = "Never write: " + ", ".join(r["id"].replace("_", " ") for r in _HARNESS["customer_vocabulary"]) + "."
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
ANALYSIS_VERSION = "snapshot-vision-v3-restaurant"
SUMMARY_VERSION = "visual-day-v2-context"
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


def heartbeat(worker_state: str | None = None) -> None:
    try:
        rpc("wl_vision_worker_heartbeat", {
            "p_worker_id": WORKER_ID,
            "p_state": worker_state or ("ready" if state["ready"] else "starting"),
            "p_model": VISION_MODEL,
            "p_media_backend": "coolify_private_minio",
            "p_processed": int(state["processed"]),
            "p_failed": int(state["failed"]),
            "p_last_success_at": state["last_success_at"],
            "p_detail": {
                "model_ready": bool(state["model_ready"]),
                "last_error": state["last_error"],
                "batch_size": BATCH_SIZE,
            },
        }, timeout=15)
    except Exception as exc:
        log(f"heartbeat warning: {type(exc).__name__}")


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
        "business": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "operational_state": {"type": "string"},
                "queue_pressure": {
                    "type": "string",
                    "enum": ["not_applicable", "none", "light", "moderate", "heavy", "unclear"]
                },
                "safety_concern": {"type": "boolean"},
                "safety_reason": {"type": "string"},
                "visible_smoke_or_flame": {"type": "boolean"},
                "visible_fall_or_accident": {"type": "boolean"}
            }
        },
        "restaurant": {
            "type": "object",
            "properties": {
                "schema_version": {"type": "string"},
                "visible_customers": {"type": "integer", "minimum": 0},
                "staff_count": {"type": "integer", "minimum": 0},
                "occupied_tables": {"type": "integer", "minimum": 0},
                "served_tables": {"type": "integer", "minimum": 0},
                "service_interaction_observed": {"type": "boolean"},
                "kitchen_load": {"type": "number", "minimum": 0, "maximum": 1},
                "handoff_load": {"type": "number", "minimum": 0, "maximum": 1},
                "counter_active": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "tables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table_key": {"type": "string"},
                            "occupied": {"type": "boolean"},
                            "customer_count": {"type": "integer", "minimum": 0},
                            "food_present": {"type": "boolean"},
                            "drinks_present": {"type": "boolean"},
                            "service_interaction_observed": {"type": "boolean"},
                            "staff_present": {"type": "boolean"},
                            "clearing_state": {"type": "boolean"},
                            "combined_group": {"type": "string"},
                            "visibility_quality": {"type": "number", "minimum": 0, "maximum": 1},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                        },
                        "required": ["table_key"]
                    }
                },
                "analytics_quality": {
                    "type": "object",
                    "properties": {
                        "visibility_quality": {"type": "number", "minimum": 0, "maximum": 1},
                        "people_count_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "table_tracking_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "glare_level": {"type": "number", "minimum": 0, "maximum": 1},
                        "overexposure_level": {"type": "number", "minimum": 0, "maximum": 1},
                        "occlusion_level": {"type": "number", "minimum": 0, "maximum": 1},
                        "obstruction_level": {"type": "number", "minimum": 0, "maximum": 1},
                        "camera_angle_adequacy": {"type": "number", "minimum": 0, "maximum": 1},
                        "lighting_uniformity": {"type": "number", "minimum": 0, "maximum": 1},
                        "issues": {"type": "array", "items": {"type": "string"}},
                        "blocked_regions": {"type": "array", "items": {"type": "string"}},
                        "recommended_actions": {"type": "array", "items": {"type": "string"}}
                    }
                }
            }
        },
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


def _business_context(job: dict) -> tuple[dict, dict]:
    ctx = job.get("business_context") or {}
    camera_ctx = ctx.get("camera_context") or {}
    channel = str(job.get("channel") or "")
    per_camera = camera_ctx.get(channel) or {}
    return ctx, per_camera


def _as_int(value):
    try:
        n = int(value)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _as_bool(value):
    if value is True or value is False:
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _as_01(value):
    try:
        n = float(value)
        return n if 0 <= n <= 1 else None
    except (TypeError, ValueError):
        return None


def _clean_text(value, limit=220):
    text = str(value or "").strip()
    return text[:limit] if text else None


def _normalize_restaurant(result: dict, ctx: dict) -> None:
    analytics = ctx.get("restaurant_analytics") or {}
    if analytics.get("enabled") is not True:
        result.pop("restaurant", None)
        return

    raw = result.get("restaurant") if isinstance(result.get("restaurant"), dict) else {}
    camera_role = str(analytics.get("camera_role") or "")
    supplied = raw.get("tables") if isinstance(raw.get("tables"), list) else []
    supplied_by_key = {
        str(row.get("table_key")): row for row in supplied
        if isinstance(row, dict) and row.get("table_key")
    }

    tables = []
    configured = analytics.get("tables") if isinstance(analytics.get("tables"), list) else []
    if camera_role == "dining_floor":
        for cfg in configured:
            if not isinstance(cfg, dict):
                continue
            key = str(cfg.get("table_key") or "")
            if not key:
                continue
            row = supplied_by_key.get(key) or {}
            service_action = _as_bool(row.get("service_interaction_observed"))
            if service_action is None:
                # Compatibility with the existing DB column: this field means a visibly
                # defensible table-service action, never inferred employment/attendance.
                service_action = _as_bool(row.get("staff_present"))
            tables.append({
                "table_key": key,
                "occupied": _as_bool(row.get("occupied")),
                "customer_count": _as_int(row.get("customer_count")),
                "food_present": _as_bool(row.get("food_present")),
                "drinks_present": _as_bool(row.get("drinks_present")),
                "service_interaction_observed": service_action,
                "staff_present": service_action,
                "clearing_state": _as_bool(row.get("clearing_state")),
                "combined_group": _clean_text(row.get("combined_group"), 80),
                "visibility_quality": _as_01(row.get("visibility_quality")),
                "confidence": _as_01(row.get("confidence")),
            })

    quality = raw.get("analytics_quality") if isinstance(raw.get("analytics_quality"), dict) else {}
    service_seen = _as_bool(raw.get("service_interaction_observed"))
    if service_seen is None and camera_role == "dining_floor":
        service_seen = any(row.get("service_interaction_observed") is True for row in tables)

    result["restaurant"] = {
        "schema_version": "restaurant-vision-v3",
        "visible_customers": _as_int(raw.get("visible_customers")) if camera_role == "dining_floor" else None,
        "staff_count": _as_int(raw.get("staff_count")),
        "occupied_tables": _as_int(raw.get("occupied_tables")) if camera_role == "dining_floor" else None,
        "served_tables": _as_int(raw.get("served_tables")) if camera_role == "dining_floor" else None,
        "service_interaction_observed": service_seen,
        "kitchen_load": _as_01(raw.get("kitchen_load")) if camera_role == "kitchen" else None,
        "handoff_load": _as_01(raw.get("handoff_load")) if camera_role == "service_handoff" else None,
        "counter_active": _as_bool(raw.get("counter_active")) if camera_role == "cash_counter" else None,
        "confidence": _as_01(raw.get("confidence")),
        "analytics_quality": {
            "visibility_quality": _as_01(quality.get("visibility_quality")),
            "people_count_confidence": _as_01(quality.get("people_count_confidence")) if camera_role == "dining_floor" else None,
            "table_tracking_confidence": _as_01(quality.get("table_tracking_confidence")) if camera_role == "dining_floor" else None,
            "glare_level": _as_01(quality.get("glare_level")),
            "overexposure_level": _as_01(quality.get("overexposure_level")),
            "occlusion_level": _as_01(quality.get("occlusion_level")),
            "obstruction_level": _as_01(quality.get("obstruction_level")),
            "camera_angle_adequacy": _as_01(quality.get("camera_angle_adequacy")),
            "lighting_uniformity": _as_01(quality.get("lighting_uniformity")),
            "issues": [_clean_text(x, 180) for x in (quality.get("issues") or [])[:8] if _clean_text(x, 180)],
            "blocked_regions": [_clean_text(x, 180) for x in (quality.get("blocked_regions") or [])[:8] if _clean_text(x, 180)],
            "recommended_actions": [_clean_text(x, 220) for x in (quality.get("recommended_actions") or [])[:8] if _clean_text(x, 220)],
        },
        "tables": tables,
    }


def analyze_snapshot(job: dict) -> dict:
    camera = job.get("camera") or "camera"
    purpose = str(job.get("camera_purpose") or "general")
    ctx, per_camera = _business_context(job)
    site_type = str(job.get("site_type") or ctx.get("site_type") or "business")
    role = str(per_camera.get("role") or purpose or "general area")
    watch_for = per_camera.get("watch_for") or []
    safety_note = str(per_camera.get("safety_note") or "")
    ai_note = str(ctx.get("ai_context_note") or "")
    watch_text = "; ".join(str(x) for x in watch_for[:12]) or "routine activity and meaningful exceptions"
    restaurant = ctx.get("restaurant_analytics") or {}
    restaurant_enabled = restaurant.get("enabled") is True
    restaurant_role = str(restaurant.get("camera_role") or "")
    restaurant_tables = restaurant.get("tables") if isinstance(restaurant.get("tables"), list) else []
    restaurant_contract = ""
    if restaurant_enabled:
        restaurant_contract = f"""
RESTAURANT ANALYTICS CONTRACT
- Camera analytics role: {restaurant_role}
- Configured dining tables for this view: {json.dumps(restaurant_tables, ensure_ascii=False)}
- Return a top-level restaurant object using the supplied role. Use null/omit a value when it is not visually defensible.
- visible_customers means simultaneous visible diners in this view, never unique footfall or a daily customer total.
- For a dining_floor camera, return one table row for EVERY configured table_key above, even when uncertain.
- For each table row, service_interaction_observed is true only when a person is visibly performing a defensible table-service action at/for that occupied table (approach, serve, clear, interact). It does NOT establish employment, identity, attendance or productivity.
- Also mirror service_interaction_observed into staff_present for backward-compatible storage; interpret that legacy field only as visible service action presence.
- If adjacent movable tables are visibly joined for one party, keep the underlying table keys and give them the same short combined_group.
- food_present means food is visibly present; never infer order correctness, payment, food quality or a completed order.
- staff_count, when populated, means the number of people visibly performing role-appropriate service actions at this moment, not unique staff or shift headcount.
- Never infer gender, age, ethnicity, relationship status or any other customer demographic from appearance.
- For kitchen/service_handoff/cash_counter roles, only populate the role-supported restaurant fields.
- Include analytics_quality scores only for visible image/geometry quality; do not invent equipment faults.
""".strip()

    prompt = f"""
You are reviewing one CCTV still from camera "{camera}" at a {site_type} site for an owner-facing
WatchLog operational and security report.

BUSINESS CONTEXT
- Camera purpose: {purpose}
- Camera role: {role}
- What this camera should help observe: {watch_text}
- Site guidance: {ai_note or "Use the camera role and visible evidence only."}
- Camera safety guidance: {safety_note or "None beyond the general rules below."}

GENERAL RULES
- Describe only what is visibly supported by this single image.
- "summary" and "unusual_reason" may be shown to the owner: describe the scene itself, never the
  image, snapshot or how it was captured. {HARNESS_VOCABULARY_LINE}
- Do NOT identify any person or guess a name. Do not infer ethnicity, religion, health,
  criminality, employment status, or other sensitive traits from appearance.
- Use neutral phrases such as "one person" or "two people"; do not call someone staff,
  waiter, customer, manager, or delivery personnel unless the image itself clearly supports
  the activity and the wording is still appropriately qualified.
- people_count is only the number visibly present in this frame. Never turn it into unique
  customers, visits, sales, orders, revenue, conversion, or footfall.
- Do not turn ordinary presence into an incident.
- For restricted/access cameras, entry_visible/exit_visible are true only when an actual
  crossing is visibly supported by this image.
- queue_pressure must be "not_applicable" unless the camera role actually shows a queue,
  service counter, pickup/handoff point, or waiting area.
- For kitchens/safety-sensitive areas, safety_concern is true only for a concrete visible
  concern. Smoke/flame or a fall/accident must be visibly apparent; never diagnose a fire,
  injury, illness, food-safety breach, or equipment fault from ambiguous imagery.
- Keep summary, activity and business.operational_state concise, factual and useful to the owner.
- If image quality prevents a reliable conclusion, say so through quality and use "unclear"
  where appropriate.
{restaurant_contract}
""".strip()
    image_b64 = load_and_mirror_image(job)
    result = ollama_chat([
        {"role": "user", "content": prompt, "images": [image_b64]}
    ], SNAPSHOT_SCHEMA)

    try:
        result["people_count"] = max(0, min(50, int(result.get("people_count", 0))))
    except Exception:
        result["people_count"] = 0
    result["people"] = (result.get("people") or [])[:result["people_count"] or 0]
    result["summary"] = str(result.get("summary") or "Nothing notable was visible.")[:500]
    result["activity"] = str(result.get("activity") or "")[:300]
    result["unusual_reason"] = str(result.get("unusual_reason") or "")[:300]

    business = result.get("business") if isinstance(result.get("business"), dict) else {}
    queue = str(business.get("queue_pressure") or "not_applicable")
    if queue not in {"not_applicable", "none", "light", "moderate", "heavy", "unclear"}:
        queue = "unclear"
    result["business"] = {
        "role": str(business.get("role") or role)[:200],
        "operational_state": str(business.get("operational_state") or result["activity"])[:300],
        "queue_pressure": queue,
        "safety_concern": bool(business.get("safety_concern", False)),
        "safety_reason": str(business.get("safety_reason") or "")[:300],
        "visible_smoke_or_flame": bool(business.get("visible_smoke_or_flame", False)),
        "visible_fall_or_accident": bool(business.get("visible_fall_or_accident", False)),
    }
    _normalize_restaurant(result, ctx)
    return result


def summarize_day(day: dict) -> dict:
    tz = day.get("timezone") or "Asia/Karachi"
    frames = day.get("frames") or []
    ctx = day.get("business_context") or {}
    site_type = str(ctx.get("site_type") or "business")
    owner_priorities = ctx.get("owner_insight_priorities") or []
    ai_note = str(ctx.get("ai_context_note") or "")
    compact = []
    for f in frames:
        compact.append({
            "time": f.get("captured_at"),
            "camera": f.get("camera"),
            "purpose": f.get("purpose"),
            "people_count": f.get("people_count"),
            "summary": f.get("summary"),
            "activity": f.get("activity"),
            "business": f.get("business"),
            "restricted_area": f.get("restricted_area"),
            "unusual": f.get("unusual"),
            "unusual_reason": f.get("unusual_reason"),
            "quality": f.get("quality"),
        })

    prompt = f"""
Prepare a concise owner-facing operational summary for {day.get('date')} in timezone {tz}.
The site is a {site_type}. The input is a chronological list of visual observations from
periodic CCTV snapshots, not continuous video.

BUSINESS GUIDANCE
- Owner priorities: {json.dumps(owner_priorities, ensure_ascii=False)}
- Site guidance: {ai_note or "Focus on operationally useful, visibly supported observations."}

RULES
- Consolidate repeated adjacent observations into continuous-looking periods only when timing and
  evidence support it. Say plainly when WatchLog cannot confirm what happened between observations,
  without mentioning snapshots, frames, images or how WatchLog captures or reviews them.
- Never count frames as people, customers, visits, transactions or orders.
- Do not invent identities or roles. "Appears to be the same person" is allowed only when
  clothing/location/timing make that visually plausible.
- A serious incident requires a concrete visible security or safety problem.
- Restricted-area entries/exits must be based on visible evidence, not alert labels.
- Report first and last VISUALLY OBSERVED activity; never claim those are actual opening/closing.
- For restaurants/cafes, useful themes include relative floor activity, visible queue/service
  pressure, service-handoff activity, kitchen activity continuity, access activity, office presence,
  late-night/after-hours exceptions, and visible safety concerns when the camera supports them.
- Never infer sales, revenue, order accuracy, food quality, staff performance, unique customer
  counts, gender, age, ethnicity, relationship status, confirmed fire, confirmed injury, or medical
  conditions from CCTV alone.
- For restaurants, prefer defensible covers/session/party-size/service metrics produced from the
  structured restaurant observations. Do not manufacture a full-day total across unverified time.
- Use Pakistan-friendly 12-hour times such as 2:15 PM.

OWNER TEXT RULES (WatchLog AI harness; every sentence above is shown to the owner)
{HARNESS_OWNER_TEXT_RULES}
- No technical terms about models, detections, confidence, databases, queues, pipelines, or AI internals.

OBSERVATIONS:
{json.dumps(compact, separators=(',', ':'), ensure_ascii=False)}
""".strip()
    return ollama_chat([{"role": "user", "content": prompt}], DAY_SCHEMA, timeout=240)


def service_day(job: dict) -> str:
    """Return the configured business/service date for a snapshot.

    Overnight sites such as Chai Wala (16:00 -> 04:00) keep after-midnight
    evidence on the service date on which the shift opened.
    """
    tz = job.get("timezone") or "Asia/Karachi"
    local_dt = datetime.fromisoformat(str(job["captured_at"]).replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
    ctx = job.get("business_context") or {}
    open_raw = str(ctx.get("open_time") or "00:00:00")
    close_raw = str(ctx.get("close_time") or "23:59:59")
    overnight = bool(ctx.get("overnight"))

    def _seconds(value: str) -> int:
        parts = value.split(":")
        h = int(parts[0] or 0)
        m = int(parts[1] or 0) if len(parts) > 1 else 0
        s = int(float(parts[2])) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s

    open_s = _seconds(open_raw)
    close_s = _seconds(close_raw)
    now_s = local_dt.hour * 3600 + local_dt.minute * 60 + local_dt.second
    overnight = overnight or close_s <= open_s

    if overnight and now_s < close_s:
        return (local_dt.date() - timedelta(days=1)).isoformat()
    return local_dt.date().isoformat()


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
            touched.add((job["site_id"], service_day(job)))
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
    heartbeat("starting")
    while not ensure_media_store():
        heartbeat("media_unavailable")
        time.sleep(20)
    while not ensure_model():
        heartbeat("model_unavailable")
        time.sleep(20)
    state["ready"] = True
    heartbeat("ready")
    log(f"ready — model={VISION_MODEL}, batch={BATCH_SIZE}")
    last_heartbeat = 0.0
    while True:
        try:
            now = time.monotonic()
            if now - last_heartbeat >= 30:
                heartbeat("ready")
                last_heartbeat = now
            n = process_batch()
            if n == 0:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            heartbeat("stopping")
            return
        except Exception as exc:
            state["last_error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            heartbeat("error")
            log(f"loop error: {state['last_error']}")
            time.sleep(max(POLL_SECONDS, 10))


if __name__ == "__main__":
    main()
