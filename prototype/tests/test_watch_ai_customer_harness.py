from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def main():
    problems = []
    gateway = read("prototype/supabase/functions/watchlog-ai/index.ts")
    owner_context = read("prototype/supabase/migrations/0152_multi_recorder_owner_read_model.sql")

    required = [
        "trusted, experienced security and operations manager",
        "natural Pakistan English",
        '"17 September 2026"',
        '"4:05 PM"',
        "internalMechanicsIntent",
        "customerSafeInternalAnswer",
        "scrubInternalLanguage",
        "WatchLog’s internal software and security implementation private",
        "Raw camera detections are evidence, not automatically unique people",
        "Do not mention AI confidence scores to customers",
        "site-config-advisor-v2-multi-recorder-safe",
        "Recorder capabilities are verified per recorder",
        "recorders: (Array.isArray(ctx?.recorders)",
        "const multiRecorder = recorders.length > 1;",
    ]
    for token in required:
        if token not in gateway:
            problems.append(f"Watch AI customer harness missing: {token}")


    # MNVR-051 / per-recorder Site Control: every answer's recorder cards are grounded in the site
    # context and every Site Control proposal carries only a validated recorder/camera target.
    for token in (
        'from "./recorder_card.ts"',
        'from "./site_control_target.ts"',
        "siteControlTarget(body?.site_control_target,",
        "groundRecorderCards(result.cards,",
        "targetSiteControlActions(result.proposed_actions,",
        "data: recorderCardData(ctx, tools?.setup_advisor)",
    ):
        if token not in gateway:
            problems.append(f"Watch AI recorder grounding missing: {token}")
    if "data: { recorders, recommendation: tools?.setup_advisor }" in gateway:
        problems.append("multi-recorder card must not omit capability_known (renders as Checked)")
    grounded = gateway.find("groundRecorderCards(result.cards,")
    saved = gateway.find('service.rpc("wl_ai_append_assistant_message"')
    if grounded < 0 or saved < 0 or grounded > saved:
        problems.append("recorder cards must be grounded before the answer is saved and returned")

    owner_required = [
        "'facts_version','watchlog-ai-context-v7'",
        "'recorders',coalesce(v_recorders->'recorders','[]'::jsonb)",
        "when v_recorder_count<=1 then v_diag->'capabilities'",
        "else '{}'::jsonb",
        "when v_recorder_count<=1 then coalesce((v_diag->>'capability_known')::boolean,false)",
        "else false",
    ]
    for token in owner_required:
        if token not in owner_context:
            problems.append(f"Multi-recorder AI owner context missing fail-closed truth guard: {token}")

    unsafe_customer_phrases = [
        "Full model reasoning is not configured",
        "deterministic guidance only",
        "canonical intelligence dataset shows",
        "A frozen WatchLog report exists",
        "evidence-graded device profile",
        "I checked the exact recorder capability profile",
    ]
    for phrase in unsafe_customer_phrases:
        if phrase in gateway:
            problems.append(f"Customer-visible internal language still present: {phrase}")

    # The gateway must intercept implementation-detail questions before normal model routing.
    boundary_pos = gateway.find("internalMechanicsIntent(prompt)")
    provider_pos = gateway.find("Resolve the mode -> providers")
    if boundary_pos < 0 or provider_pos < 0 or boundary_pos > provider_pos:
        problems.append("Internal-mechanics customer boundary must run before provider routing")

    # Guided fallback must not append AI/provider availability disclosures to the customer answer.
    if 'result.answer += " Full model reasoning' in gateway:
        problems.append("Guided fallback must not disclose model/provider availability")

    if problems:
        raise SystemExit("Watch AI customer harness contract failed:\n- " + "\n- ".join(problems))
    print("Watch AI customer harness contract: PASS")


if __name__ == "__main__":
    main()
