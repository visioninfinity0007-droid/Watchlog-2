from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def main():
    problems = []
    migration = read("prototype/supabase/migrations/0111_visual_snapshot_pipeline.sql")
    media_migration = read("prototype/supabase/migrations/0113_private_media_mirror.sql")
    context_migration = read("prototype/supabase/migrations/0115_context_aware_visual_review.sql")
    worker = read("prototype/vision_worker/worker.py")
    compose = read("prototype/vision_worker/docker-compose.coolify.yml")
    gateway = read("prototype/supabase/functions/watchlog-ai/index.ts")
    browser_supabase = read("portal/lib/supabase.js")

    for token in [
        "snapshot_visual_reviews",
        "trg_snapshot_visual_review_queue",
        "wl_vision_claim_snapshots",
        "wl_vision_complete_snapshot",
        "wl_vision_fail_snapshot",
        "wl_vision_day_for_worker",
        "wl_vision_save_day_summary",
        "wl_my_visual_day",
        "service role required",
        "operational_snapshot",
    ]:
        if token not in migration:
            problems.append(f"visual pipeline migration missing: {token}")

    for token in [
        "wl_vision_mark_media",
        "media_bucket",
        "media_key",
        "media_sha256",
        "grant execute on function public.wl_vision_mark_media",
    ]:
        if token not in media_migration:
            problems.append(f"private media migration missing: {token}")

    for token in [
        "business_context",
        "camera_context",
        "owner_insight_priorities",
        "camera_purpose",
        "r.analysis->'business'",
    ]:
        if token not in context_migration:
            problems.append(f"context-aware visual migration missing: {token}")

    for token in [
        "gemma3:4b",
        "boto3",
        "load_and_mirror_image",
        "wl_vision_mark_media",
        "images",
        "SNAPSHOT_SCHEMA",
        "DAY_SCHEMA",
        "Do NOT identify any person",
        "wl_vision_claim_snapshots",
        "wl_vision_complete_snapshot",
        "wl_vision_save_day_summary",
        'ANALYSIS_VERSION = "snapshot-vision-v',   # versioned analysis (v3-restaurant since 6291c247)
        "Camera role:",
        "queue_pressure",
        "periodic CCTV snapshots",
    ]:
        if token not in worker:
            problems.append(f"vision worker missing: {token}")

    if "professional office security report" in worker:
        problems.append("vision worker must not hard-code office context")

    for token in ["minio/minio", "watchlog_media", "ollama/ollama", "watchlog_ollama", "SUPABASE_SERVICE_ROLE_KEY", "WATCHLOG_MEDIA_ACCESS_KEY", "WATCHLOG_MEDIA_SECRET_KEY", "VISION_MODEL"]:
        if token not in compose:
            problems.append(f"Coolify vision compose missing: {token}")

    for token in ["wl_my_visual_day", "visualDayFallback", "visual_day"]:
        if token not in gateway:
            problems.append(f"Watch AI is not wired to visual findings: {token}")

    if "SUPABASE_SERVICE_ROLE_KEY" in browser_supabase:
        problems.append("service role key must never enter the customer portal browser payload")
    if "SUPABASE_SERVICE_ROLE_KEY" not in gateway:
        problems.append("Watch AI server runtime must keep service-role usage server-side")

    if problems:
        raise SystemExit("visual snapshot pipeline contract failed:\n- " + "\n- ".join(problems))
    print("visual snapshot pipeline contract: PASS")


if __name__ == "__main__":
    main()
