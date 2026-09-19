// Turning a model's reply into a WatchLog result.
//
// The model is asked for a JSON envelope {answer, cards, suggestions, proposed_actions}.
// It does not always comply exactly: it may wrap the JSON in a ```json fence, prepend a
// sentence, or get truncated by the output cap mid-object.
//
// The old handling was `try { JSON.parse(text) } catch { { answer: text } }`, so ANY of
// those cases dumped the raw envelope straight into the answer and the customer read
// `{"answer":"Overnight ...","cards":[{"type":"coverage" ...` in the chat. The content was
// fine every time; only the wrapper defeated the parser.
//
// So: recover in stages, and never let a JSON blob reach the customer as prose.

/**
 * Returned as the answer when nothing could be recovered. Exported so the caller can tell
 * "unreadable" from a real answer and prefer WatchLog's deterministic guided fallback,
 * which is a genuine answer rather than an apology.
 */
export const UNREADABLE_ANSWER = "WatchLog could not read the model's reply. Please ask again.";

export type ModelResult = {
  answer?: string;
  cards?: unknown;
  suggestions?: unknown;
  proposed_actions?: unknown;
};

/** Strip a ```json … ``` (or bare ```) fence. Models add these even in JSON mode. */
export function stripFence(text: string): string {
  const t = text.trim();
  if (!t.startsWith("```")) return t;
  const withoutOpen = t.replace(/^```[a-zA-Z0-9_-]*\s*\r?\n?/, "");
  const close = withoutOpen.lastIndexOf("```");
  return (close === -1 ? withoutOpen : withoutOpen.slice(0, close)).trim();
}

/** First BALANCED {...}, ignoring braces inside strings. Handles leading/trailing prose. */
export function firstJsonObject(text: string): string | null {
  const start = text.indexOf("{");
  if (start === -1) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (esc) { esc = false; continue; }
    if (ch === "\\") { esc = true; continue; }
    if (ch === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) return text.slice(start, i + 1);
  }
  return null;
}

/**
 * Pull just the "answer" string out of a TRUNCATED envelope.
 *
 * When the output cap cuts the object mid-way the answer is usually complete already —
 * it is the first field. Recovering it turns an unreadable blob into the sentence the
 * model actually wrote.
 */
export function salvageAnswer(text: string): string | null {
  const m = /"answer"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(text);
  if (!m) return null;
  try {
    return JSON.parse(`"${m[1]}"`);
  } catch {
    return m[1].replace(/\\n/g, "\n").replace(/\\"/g, '"');
  }
}

/**
 * True when a string is machine output rather than something a person should read.
 *
 * Deliberately broad: ANY text that opens with a brace or bracket is JSON we failed to
 * parse, and a half-parsed blob -- or even a lone "{" -- is never an acceptable answer.
 * No real answer about a camera site begins with a brace.
 */
export function looksLikeJson(text: string): boolean {
  const t = text.trim();
  return t.startsWith("{") || t.startsWith("[");
}

/**
 * Best-effort ModelResult from whatever the provider returned.
 *
 * Order: parse as-is -> strip fence -> first balanced object -> salvage the answer field
 * -> give up. Giving up returns a plain sentence, NEVER the raw text, because the whole
 * point is that a customer must not be shown JSON.
 */
export function coerceModelResult(raw: string): ModelResult {
  const text = String(raw ?? "");
  for (const candidate of [text.trim(), stripFence(text)]) {
    if (!candidate) continue;
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    } catch { /* next strategy */ }
  }

  const embedded = firstJsonObject(stripFence(text));
  if (embedded) {
    try {
      const parsed = JSON.parse(embedded);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed;
    } catch { /* next strategy */ }
  }

  const salvaged = salvageAnswer(text);
  if (salvaged && salvaged.trim()) return { answer: salvaged };

  // Plain prose is a legitimate answer; a broken envelope is not.
  if (!looksLikeJson(text) && text.trim()) return { answer: text.trim() };

  return { answer: UNREADABLE_ANSWER };
}
