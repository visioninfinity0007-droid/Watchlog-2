// Canonical manifest bytes, identical to prototype/agent/updater.py canonical_manifest_bytes:
//   json.dumps({k: v for k, v in manifest.items() if k != "signature"},
//              sort_keys=True, separators=(",", ":"))      # ensure_ascii=True (Python default)
// Keys sorted at every level, no whitespace, non-ASCII written as \uXXXX (lowercase hex).

function sortDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortDeep);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      out[key] = sortDeep((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  if (typeof value === "number" && !Number.isInteger(value)) {
    // Python and JavaScript print some floats differently; the manifest carries none.
    throw new Error("canonical manifest refuses non-integer numbers");
  }
  return value;
}

export function canonicalManifest(manifest: Record<string, unknown>): string {
  const body: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(manifest)) {
    if (key !== "signature") body[key] = value;
  }
  return JSON.stringify(sortDeep(body)).replace(/[\u007f-￿]/g,
    (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"));
}
