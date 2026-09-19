from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def main():
    problems = []
    migration = read("prototype/supabase/migrations/0111_visual_snapshot_pipeline.sql")
    worker = read("prototype/vision_worker/worker.py")
    compose = read("prototype/vision_worker/docker-compose.coolify.yml")
    gateway = read("prototype/supabase/functions/watchlog-ai/index.ts")

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
        "gemma3:4b",
        "images",
        "SNAPSHOT_SCHEMA",
        "DAY_SCHEMA",
        "Do NOT identify any person",
        "wl_vision_claim_snapshots",
        "wl_vision_complete_snapshot",
        "wl_vision_save_day_summary",
    ]:
        if token not in worker:
            problems.append(f"vision worker missing: {token}")

    for token in ["ollama/ollama", "watchlog_ollama", "SUPABASE_SERVICE_ROLE_KEY", "VISION_MODEL"]:
        if token not in compose:
            problems.append(f"Coolify vision compose missing: {token}")

    for token in ["wl_my_visual_day", "visualDayFallback", "visual_day"]:
        if token not in gateway:
            problems.append(f"Watch AI is not wired to visual findings: {token}")

    if "SUPABASE_SERVICE_ROLE_KEY" in gateway:
        problems.append("service role key must never enter the customer Watch AI browser/runtime payload")

    if problems:
        raise SystemExit("visual snapshot pipeline contract failed:\n- " + "\n- ".join(problems))
    print("visual snapshot pipeline contract: PASS")


if __name__ == "__main__":
    main()
