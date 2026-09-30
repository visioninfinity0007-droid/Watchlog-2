// The portal showed a customer this, verbatim, in the chat bubble:
//
//   {"answer":"Overnight there were no verified events at Al-Khalid Office...","cards":
//   [{"type":"coverage","title":"Monitoring coverage","data":{...
//
// The model's content was correct every time. What failed was the WRAPPER: index.ts did
// `try { JSON.parse(text) } catch { { answer: text } }`, so any reply the parser could not
// take whole was handed to the reader as raw JSON.
//
// Each test below is one real way a provider returns an envelope that JSON.parse rejects.
//
//   deno test model_result_test.ts
import { assert, assertEquals, assertStringIncludes } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  coerceModelResult, firstJsonObject, looksLikeJson, salvageAnswer, stripFence,
  UNREADABLE_ANSWER,
} from "./model_result.ts";

const ENVELOPE = {
  answer: "Overnight there were no verified events at Al-Khalid Office.",
  cards: [{ type: "coverage", title: "Monitoring coverage", data: { live_minutes: 480 } }],
  suggestions: ["Show me last night"],
  proposed_actions: [],
};

// ----------------------------------------------------------------- the whole point
Deno.test("a customer is NEVER shown a JSON envelope as the answer", () => {
  const shapes = [
    JSON.stringify(ENVELOPE),
    "```json\n" + JSON.stringify(ENVELOPE) + "\n```",
    "Here is the summary you asked for:\n" + JSON.stringify(ENVELOPE),
    JSON.stringify(ENVELOPE).slice(0, 120),          // truncated by the output cap
    '{"answer": "unterminated',                       // truncated mid-string
    "{}",
    "",
  ];
  for (const raw of shapes) {
    const answer = String(coerceModelResult(raw).answer ?? "");
    assert(!looksLikeJson(answer), `JSON reached the customer for: ${raw.slice(0, 40)}`);
    assert(!answer.includes('"cards"'), `card JSON leaked into prose for: ${raw.slice(0, 40)}`);
  }
});

// -------------------------------------------------------------- the recoverable shapes
Deno.test("clean JSON is passed through with its cards intact", () => {
  const out = coerceModelResult(JSON.stringify(ENVELOPE));
  assertEquals(out.answer, ENVELOPE.answer);
  assertEquals((out.cards as unknown[]).length, 1);
});

Deno.test("a fenced envelope keeps its cards instead of being dumped as text", () => {
  const out = coerceModelResult("```json\n" + JSON.stringify(ENVELOPE) + "\n```");
  assertEquals(out.answer, ENVELOPE.answer);
  assertEquals((out.cards as unknown[]).length, 1, "the fence must not cost the cards");
});

Deno.test("a bare fence with no language tag is handled too", () => {
  const out = coerceModelResult("```\n" + JSON.stringify(ENVELOPE) + "\n```");
  assertEquals(out.answer, ENVELOPE.answer);
});

Deno.test("prose before and after the object is discarded, not rendered", () => {
  const out = coerceModelResult(
    "Sure! Here is the result:\n" + JSON.stringify(ENVELOPE) + "\nLet me know if you need more.",
  );
  assertEquals(out.answer, ENVELOPE.answer);
  assertEquals((out.cards as unknown[]).length, 1);
});

Deno.test("a truncated envelope still yields the sentence the model wrote", () => {
  // The output cap cuts the object after the answer field. The answer is complete.
  const cut = JSON.stringify(ENVELOPE).slice(0, 110);
  assert(!cut.endsWith("}"), "fixture must actually be truncated");
  const out = coerceModelResult(cut);
  assertStringIncludes(String(out.answer), "Overnight there were no verified events");
  assert(!String(out.answer).includes("{"), out.answer);
});

Deno.test("escapes inside the salvaged answer are decoded, not shown raw", () => {
  const raw = '{"answer":"Line one.\nLine two with a \\"quote\\".","cards":[{"type":';
  assertEquals(coerceModelResult(raw).answer, 'Line one.\nLine two with a "quote".');
});

Deno.test("a brace inside the answer text does not truncate the object", () => {
  const withBrace = { ...ENVELOPE, answer: "Rule {motion} fired at 02:14." };
  const out = coerceModelResult("Note:\n" + JSON.stringify(withBrace));
  assertEquals(out.answer, withBrace.answer);
});

Deno.test("plain prose is a legitimate answer and survives untouched", () => {
  const prose = "No verified events overnight. Camera 3 stayed offline from 01:10.";
  assertEquals(coerceModelResult(prose).answer, prose);
});

Deno.test("unrecoverable input gets a readable sentence, never the raw blob", () => {
  for (const raw of ['{"cards":[{"type":"coverage"', "", "   ", "{"]) {
    const answer = String(coerceModelResult(raw).answer);
    assertStringIncludes(answer, "could not read the model's reply");
  }
});

Deno.test("an envelope wrapped in an array is unwrapped, not spread", () => {
  // Spreading an array into sanitizeResult produces numeric keys and an empty answer, so
  // the top-level parse rejects arrays; the object inside is still recovered.
  assertEquals(coerceModelResult('[{"answer":"nope"}]').answer, "nope");
});

// ------------------------------------------------------------------------- the helpers
Deno.test("stripFence leaves unfenced text alone", () => {
  assertEquals(stripFence("hello"), "hello");
  assertEquals(stripFence("```json\n{}\n```"), "{}");
});

Deno.test("firstJsonObject ignores braces that live inside strings", () => {
  assertEquals(firstJsonObject('x {"a":"}{"} y'), '{"a":"}{"}');
  assertEquals(firstJsonObject("no object here"), null);
  assertEquals(firstJsonObject('{"a":1'), null, "an unbalanced object is not an object");
});

Deno.test("salvageAnswer finds the answer field and nothing else", () => {
  assertEquals(salvageAnswer('{"answer":"hi","cards":[]}'), "hi");
  assertEquals(salvageAnswer('{"cards":[]}'), null);
});

Deno.test("looksLikeJson tells prose from machine output", () => {
  assert(looksLikeJson('{"answer":"x"}'));
  assert(looksLikeJson('{"cards":[]}'));
  assert(looksLikeJson("{"), "even a lone brace is not an answer");
  assert(looksLikeJson('{"unrelated":1}'));
  assert(!looksLikeJson("The answer: nothing happened overnight."));
  assert(!looksLikeJson("Camera 3 {motion} stayed offline."));
});

Deno.test("the unreadable answer is a recognisable constant, not a bare string", () => {
  // index.ts compares against this to fall through to the deterministic guided fallback.
  // If the sentence and the constant ever drift apart, that fall-through goes silent.
  assertEquals(coerceModelResult('{"cards":[').answer, UNREADABLE_ANSWER);
  assertEquals(coerceModelResult("").answer, UNREADABLE_ANSWER);
  assert(!looksLikeJson(UNREADABLE_ANSWER));
});
