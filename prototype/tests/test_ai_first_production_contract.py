from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def main():
    problems = []
    gateway = read("prototype/supabase/functions/watchlog-ai/index.ts")
    guardrails = read("prototype/supabase/migrations/0104_ai_runtime_guardrails.sql")
    hardening = read("prototype/supabase/migrations/0103_production_security_hardening.sql")
    portal = read("portal/app/ai/page.js")

    required_gateway = [
        'wl_ai_record_usage',
        'wl_ai_conversation_context',
        'conversation_site_mismatch',
        'AbortSignal.timeout(35000)',
        'ACTION_KINDS',
        'SAFE_HREFS',
        'delete data.command',
        'delete data.script',
        'WATCHLOG_PORTAL_ORIGINS',
        'verify',
    ]
    for token in required_gateway:
        if token not in gateway:
            problems.append(f"AI gateway missing production guard: {token}")

    for token in [
        'create table if not exists public.ai_usage_events',
        'wl_ai_conversation_context',
        'wl_ai_record_usage',
        "auth.role() <> 'service_role'",
        'p_minute_limit int default 20',
        'p_daily_limit int default 500',
    ]:
        if token not in guardrails:
            problems.append(f"AI runtime guardrail missing: {token}")

    for token in [
        'set search_path = public, pg_temp',
        'wl_current_site_agent(uuid) from public, anon, authenticated',
        'wl_site_coverage_report(uuid,timestamptz,timestamptz) from public, anon, authenticated',
        'user_id = (select auth.uid())',
        "'watchlog-ai-context-v2'",
    ]:
        if token not in hardening:
            problems.append(f"production hardening missing: {token}")

    if 'JSON.stringify(data,null,2)' in portal or 'JSON.stringify(data, null, 2)' in portal:
        problems.append("AI customer cards must not fall back to raw JSON dumps")
    for token in ['ActionButtons', 'IncidentCard', 'ReportCard', 'Verified-data mode', 'cov?.classes']:
        if token not in portal:
            problems.append(f"AI portal missing customer-ready behavior: {token}")

    # Provider credentials must remain server-only.
    for rel in ["portal/app/ai/page.js", "portal/app/setup/page.js", "portal/app/shell.js"]:
        text = read(rel)
        if 'WATCHLOG_AI_API_KEY' in text or 'NEXT_PUBLIC_WATCHLOG_AI' in text:
            problems.append(f"{rel}: AI provider secret/config must not enter the browser bundle")

    if problems:
        raise SystemExit("AI-first production contract failed:\n- " + "\n- ".join(problems))
    print("AI-first production contract: PASS")


if __name__ == "__main__":
    main()
