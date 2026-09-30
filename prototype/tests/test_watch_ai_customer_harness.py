from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def main():
    problems = []
    gateway = read("prototype/supabase/functions/watchlog-ai/index.ts")

    required = [
        "trusted, experienced security and office manager",
        "natural Pakistan English",
        '"17 September 2026"',
        '"4:05 PM"',
        "internalMechanicsIntent",
        "customerSafeInternalAnswer",
        "scrubInternalLanguage",
        "WatchLog’s internal software and security implementation private",
        "Raw camera detections are evidence, not automatically unique people",
        "Do not mention AI confidence scores to customers",
    ]
    for token in required:
        if token not in gateway:
            problems.append(f"Watch AI customer harness missing: {token}")

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
