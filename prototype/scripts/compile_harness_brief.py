#!/usr/bin/env python3
"""Compile the WatchLog AI harness into the compact brief the chat AI receives with every question.

Source of truth: ai-harness/ (core rules, site-type policies, tenant contexts, tenant registry).
Output: prototype/supabase/functions/watchlog-ai/harness_brief.generated.ts (never hand-edit).

    python prototype/scripts/compile_harness_brief.py          # regenerate
    python prototype/scripts/compile_harness_brief.py --check  # fail if the generated file is stale

The brief is distilled, not pasted: free provider tiers are token-capped per minute, so each
question carries only the rules, metric meanings, the tenant's own camera roles and its dated
review items (~1.5k tokens). It is customer-safe: no internal table/row/function names. Live facts
never come from here; they stay in the database.
"""
import hashlib, json, re, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / "ai-harness"
OUT = ROOT / "prototype/supabase/functions/watchlog-ai/harness_brief.generated.ts"

# Only harness rules the edge function's SYSTEM_PROMPT does not already state (tone, dates, privacy,
# recommendations and restaurant metric wording live there). Keeps every question inside token caps.
CORE_SECTIONS = {
    "core/truth.md": ["The semantic hierarchy (do not collapse)", "Coverage truth (LIVE / RECOVERED / UNVERIFIED)",
                      "Capability truth (evidence classes)"],
    "core/confidence.md": ["Forbidden"],
}
SITE_TYPES = ("office", "restaurant")
MAX_WORDS = 34


def clean(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", str(text))
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\((?:[^()]*\b(?:wl_[a-z_]+|migration|device-knowledge|incidents/|\.md|\.yaml)[^()]*)\)", "", text)
    text = re.sub(r"\s*\b(?:from|via|in|by|use)?\s*\bwl_[a-z_]+(?:/[a-z_]+)?\b", "", text, flags=re.I)
    text = re.sub(r"\b[\w-]+/[\w./-]*\.(?:md|yaml)\b", "", text)
    text = re.sub(r"\((?:e\.g\.|for example)[^()]*\)", "", text)          # examples may name another tenant
    text = re.sub(r"\s*\b(?:is read )?from [\w-]+/(?=[\s:,.]|$)", "", text)  # "read from <folder>/"
    text = re.sub(r"\b[\w-]+/(?=[\s:,.]|$)", "", text)                      # bare folder paths
    text = re.sub(r"\b([A-Z]+(?:_[A-Z]+)+)\b", lambda m: m.group(1).lower().replace("_", " "), text)  # taxonomy codes
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([,.;:])", r"\1", text)


def trim(text: str, words: int = MAX_WORDS) -> str:
    w = clean(text).split()
    return " ".join(w) if len(w) <= words else " ".join(w[:words]).rstrip(",;") + "…"


def md_sections(path: Path) -> dict:
    out, cur = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = line[3:].strip(); out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return out


def section_items(lines: list) -> list:
    """Prose and bullets in document order (bullets keep indented continuation lines), as one-liners."""
    items, kind = [], None
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith(("|", "```")):
            kind = None; continue
        if s.startswith("- "):
            items.append(s[2:]); kind = "bullet"
        elif kind == "bullet" and raw.startswith((" ", "\t")):
            items[-1] += " " + s
        elif kind == "prose":
            items[-1] += " " + s
        else:
            items.append(s); kind = "prose"
    return [x for x in (trim(i, 40) for i in items) if x]


def core_brief() -> str:
    lines = ["CORE RULES (all tenants)"]
    for rel, wanted in CORE_SECTIONS.items():
        secs = md_sections(H / rel)
        for name in wanted:
            if name not in secs:
                raise SystemExit(f"harness section missing: {rel} :: {name}")
            items = section_items(secs[name])
            lines.append(f"{name.split(' (')[0]}: " + " | ".join(items))
    return "\n".join(lines)


def load(rel: str) -> dict:
    return yaml.safe_load((H / rel).read_text(encoding="utf-8"))


def site_type_brief(site_type: str) -> str:
    d = load(f"site-types/{site_type}.yaml")
    lines = [f"SITE TYPE POLICY: {site_type}"]
    day = d.get("working_day") or d.get("service_day") or {}
    for k, v in day.items():
        if isinstance(v, str):
            lines.append(f"- {k.replace('_', ' ')}: {trim(v, 40)}")
    metrics = d.get("metric_definitions") or d.get("metrics") or {}
    lines.append("Metric meanings:")
    for name, m in metrics.items():
        if not isinstance(m, dict):
            continue
        cls = m.get("class") or m.get("evidence_class") or ""
        bits = [trim(m.get("definition") or m.get("rule") or "", 22)]
        if m.get("never_call"):
            bits.append("never call it: " + ", ".join(str(x).replace("_", " ") for x in m["never_call"]))
        if m.get("caveat"):
            bits.append("caveat: " + trim(m["caveat"], 14))
        lines.append(f"- {name.replace('_', ' ')}{f' ({cls})' if cls else ''}: " + "; ".join(b for b in bits if b))
    for tb in d.get("truth_boundaries") or []:
        lines.append(f"- {trim(tb, 24)}")
    return "\n".join(lines)


def role_line(camera: str, spec, roles: dict) -> str:
    role = spec.get("role") if isinstance(spec, dict) else str(spec)
    rdef = roles.get(role, {})
    bits = [f"{camera} = {role.replace('_', ' ')}"]
    if isinstance(spec, dict) and spec.get("note"):
        bits.append(f"({clean(spec['note'])})")
    if rdef.get("purpose"):
        bits.append(f"- {trim(rdef['purpose'], 12)}")
    if rdef.get("forbidden_outputs"):
        bits.append("; never: " + ", ".join(str(x).replace("_", " ") for x in rdef["forbidden_outputs"]))
    return " ".join(bits)


def tenant_brief(folder: Path) -> dict:
    d = yaml.safe_load((folder / "context.yaml").read_text(encoding="utf-8"))
    site_type = d.get("site_type", "")
    roles = load(f"site-types/{site_type}.yaml").get("camera_roles", {}) if site_type in SITE_TYPES else {}
    ident = d.get("identity") or {}
    lines = [f"TENANT CONTEXT: {ident.get('display_name', folder.name)} ({site_type})"]
    who = [ident.get(k) for k in ("tenant", "branch", "business_type", "service_style") if ident.get(k)]
    if who:
        lines.append("About: " + "; ".join(clean(x) for x in who))
    day = d.get("service_day") or d.get("working_day") or {}
    o, c = day.get("canonical_open") or day.get("open"), day.get("canonical_close") or day.get("close")
    days = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
    lines.append(f"Configured hours: {o}-{c}{' (overnight service day)' if day.get('overnight') else ''}, days: "
                 + ", ".join(days.get(x, str(x)) for x in day.get("days_iso", [])))
    if d.get("camera_role_map"):
        lines.append("Camera roles:")
        lines += [f"- {role_line(cam, spec, roles)}" for cam, spec in d["camera_role_map"].items()]
    ci = d.get("camera_identity") or {}
    if ci.get("mapping_status"):
        lines.append(f"Camera mapping: {ci['mapping_status'].replace('_', ' ')}. {trim(ci.get('rule', ''), 40)}")
    if d.get("known_operational_areas"):
        lines.append("Known areas (roles provisional until mapping is confirmed): " + ", ".join(d["known_operational_areas"]))
    ae = d.get("camera_attribution_evidence") or {}
    if ae.get("customer_safe_rule"):
        lines.append(f"Camera evidence caveat: {trim(ae['customer_safe_rule'], 60)}")
    cc = d.get("customer_counting") or {}
    if cc:
        lines.append(f"Customer counting: primary metric {cc.get('primary_metric', '').replace('_', ' ')}; unique footfall available: "
                     f"{'yes' if cc.get('unique_footfall_available') else 'no'}; accuracy: {str(cc.get('accuracy_status', '')).replace('_', ' ')}.")
    tm = d.get("table_model") or {}
    if tm:
        lines.append(f"Tables: {tm.get('total_calibrated_anchors')} calibrated anchors (Floor 1: {tm.get('floor_1_calibrated_anchors')}, "
                     f"Floor 2: {tm.get('floor_2_calibrated_anchors')}), movable, can be combined.")
    for tb in d.get("truth_boundaries") or []:
        lines.append(f"- Boundary: {trim(tb, 24)}")
    if d.get("owner_priorities"):
        lines.append("Owner priorities: " + "; ".join(clean(p) for p in d["owner_priorities"][:7]))
    items = d.get("known_review_focus") or []
    if items:
        lines.append("Known review items (dated evidence, NOT live facts; use them to explain gaps and recommend, re-check live context):")
        for it in items:
            if it.get("status") == "config_alignment_required" or it.get("chat") is False:
                continue  # internal configuration work, not customer-facing
            when = f", {it['evidence_window']}" if it.get("evidence_window") else ""
            finding = it.get("finding") or it.get("concern") or ""
            action = it.get("action") or it.get("action_if_confirmed") or ""
            lines.append(f"- {it.get('id', '').replace('_', ' ')} [{it.get('status', '').replace('_', ' ')}{when}]: "
                         f"{trim(finding, 28)} Action: {trim(action, 18)}")
    return {"key": folder.name, "siteType": site_type, "siteName": ident.get("display_name", ""),
            "tenantName": ident.get("tenant", ""), "brief": "\n".join(lines)}


def active_tenants() -> list:
    registry = (H / "tenants/README.md").read_text(encoding="utf-8").split("## Other production sites")[0]
    return re.findall(r"\| `([a-z0-9-]+)/` \|", registry)


def source_files() -> list:
    files = [H / rel for rel in CORE_SECTIONS] + [H / "core/customer-vocabulary.yaml"] + [H / m for m in METHOD_FILES] + [H / f"site-types/{t}.yaml" for t in SITE_TYPES]
    return files + [H / "tenants" / t / "context.yaml" for t in active_tenants()] + [H / "tenants/README.md"]


VOCAB_FILE = "core/customer-vocabulary.yaml"
METHOD_FILES = ["methods/people-counting.md", "methods/person-recognition.md", "methods/incident-video-analysis.md"]


def method_claims() -> str:
    """Each governed method's status line + its customer-facing claims, for the customer-facing model."""
    lines = ["COUNTING, RECOGNITION AND INCIDENT CLAIMS (governed methods):"]
    for rel in METHOD_FILES:
        text = (H / rel).read_text(encoding="utf-8")
        title = text.splitlines()[0].replace("# Method:", "").strip()
        status = next((l.replace("**Customer status:**", "").strip() for l in text.splitlines() if l.startswith("**Customer status:**")), "")
        claims = md_sections(H / rel).get("Customer-facing claims", [])
        bullets = [trim(c.strip()[2:], 30) for c in claims if c.strip().startswith("- ")]
        lines.append(f"- {title}: {status} Allowed wording: " + " | ".join(bullets))
    return chr(10).join(lines)
OUT_VISION = ROOT / "prototype/supabase/functions/watchlog-vision-worker/harness_rules.generated.ts"
OUT_WORKER = ROOT / "prototype/vision_worker/harness_rules.generated.json"
OUT_SQL = ROOT / "prototype/supabase/sql/customer_vocabulary_seed.generated.sql"


def vocabulary() -> dict:
    return load(VOCAB_FILE)


def vocabulary_brief() -> str:
    v = vocabulary()
    lines = ["CUSTOMER VOCABULARY (every customer-visible word; enforced):"]
    lines += [f"- {clean(p)}" for p in v["principles"]]
    lines.append("Never write these words or phrases (say instead):")
    lines += [f"- {pattern_words(f['pattern'])} -> {f['say_instead']}" for f in v["forbidden"]]
    return "\n".join(lines)


def pattern_words(pattern: str) -> str:
    r"""Readable words from a vocabulary regex: '\b(snapshots?|frames?)\b' -> 'snapshots, frames'."""
    s = pattern.replace("\\b", "").replace("\\w*", "")
    words = []
    for alt in s.split("|"):
        w = alt.replace("(", "").replace(")", "")
        w = re.sub(r"(\w)\?", r"\1", w)              # snapshots? -> snapshots
        w = re.sub(r"\[(.)[^\]]*\]", r"\1", w)       # analy[sz]ed -> analysed ; [- ] -> -
        w = w.replace("?", "")                       # optional groups: 'legacy camera ?rows' -> 'legacy camera rows'
        w = re.sub(r"\s+", " ", w).strip()
        if w and w not in words:
            words.append(w)
    return ", ".join(words)


def vocabulary_rules() -> list:
    return [{"id": f["id"], "pattern": f["pattern"], "replaceWith": f["replace_with"]} for f in vocabulary()["forbidden"]]


def seed_sql() -> str:
    rows = []
    for i, f in enumerate(vocabulary()["forbidden"], start=1):
        pg = f["pattern"].replace("\\b", "\\y")       # Postgres word boundary
        q = lambda s: "'" + str(s).replace("'", "''") + "'"
        rows.append(f"  ({q(f['id'])}, {i}, {q(pg)}, {q(f['replace_with'])})")
    exempt = ", ".join("'" + k + "'" for k in vocabulary()["internal_keys_exempt"])
    return "\n".join([
        "-- GENERATED from ai-harness/core/customer-vocabulary.yaml - do not edit by hand.",
        "-- Regenerate: python prototype/scripts/compile_harness_brief.py ; apply this file after any vocabulary change.",
        "insert into public.customer_vocabulary_rules(id, ord, pattern_pg, replace_with) values",
        ",\n".join(rows),
        "on conflict (id) do update set ord = excluded.ord, pattern_pg = excluded.pattern_pg, replace_with = excluded.replace_with;",
        f"delete from public.customer_vocabulary_rules where id not in ({', '.join(chr(39) + f['id'] + chr(39) for f in vocabulary()['forbidden'])});",
        f"-- internal keys exempt from rewriting (mirrored in wl_customer_json_clean): {exempt}", ""])


def build() -> str:
    digest = hashlib.sha256()
    for f in source_files():
        digest.update(f.relative_to(ROOT).as_posix().encode()); digest.update(f.read_bytes().replace(b"\r\n", b"\n"))
    tenants = [tenant_brief(H / "tenants" / t) for t in active_tenants()]
    site_types = {t: site_type_brief(t) for t in SITE_TYPES}
    return "\n".join([
        "// GENERATED FILE - do not edit by hand.",
        "// Source: ai-harness/ (core rules, customer vocabulary, site types, active tenants in ai-harness/tenants/README.md).",
        "// Regenerate: python prototype/scripts/compile_harness_brief.py",
        f"export const HARNESS_SOURCE_SHA256 = {json.dumps(digest.hexdigest())};",
        f"export const CORE_BRIEF = {json.dumps(core_brief() + chr(10) + vocabulary_brief() + chr(10) + method_claims(), ensure_ascii=False)};",
        "export const SITE_TYPE_BRIEFS: Record<string, string> = " + json.dumps(site_types, ensure_ascii=False, indent=1) + ";",
        "export interface TenantBrief { key: string; siteType: string; siteName: string; tenantName: string; brief: string }",
        "export const TENANT_BRIEFS: TenantBrief[] = " + json.dumps(tenants, ensure_ascii=False, indent=1) + ";",
        "export const CUSTOMER_VOCABULARY: { id: string; pattern: string; replaceWith: string }[] = "
        + json.dumps(vocabulary_rules(), ensure_ascii=False, indent=1) + ";", ""])


def build_vision() -> str:
    return "\n".join([
        "// GENERATED FILE - do not edit by hand. Source: ai-harness/core/customer-vocabulary.yaml",
        "// Regenerate: python prototype/scripts/compile_harness_brief.py",
        f"export const OWNER_TEXT_RULES = {json.dumps(vocabulary_brief(), ensure_ascii=False)};",
        "export const CUSTOMER_VOCABULARY: { id: string; pattern: string; replaceWith: string }[] = "
        + json.dumps(vocabulary_rules(), ensure_ascii=False, indent=1) + ";", ""])


def build_worker() -> str:
    return json.dumps({"_generated": "from ai-harness/core/customer-vocabulary.yaml by prototype/scripts/compile_harness_brief.py",
                       "owner_text_rules": vocabulary_brief(), "customer_vocabulary": vocabulary_rules()},
                      ensure_ascii=False, indent=1) + "\n"


OUTPUTS = [(OUT, build), (OUT_VISION, build_vision), (OUT_WORKER, build_worker), (OUT_SQL, seed_sql)]

if __name__ == "__main__":
    if "--check" in sys.argv:
        stale = [p.relative_to(ROOT).as_posix() for p, fn in OUTPUTS
                 if (p.read_text(encoding="utf-8").replace("\r\n", "\n") if p.exists() else "") != fn()]
        if stale:
            raise SystemExit("generated harness files are stale: " + ", ".join(stale) +
                             " - run python prototype/scripts/compile_harness_brief.py")
        print("harness brief is up to date"); sys.exit(0)
    for p, fn in OUTPUTS:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(fn(), encoding="utf-8", newline="\n")
        print(f"wrote {p.relative_to(ROOT)}")
