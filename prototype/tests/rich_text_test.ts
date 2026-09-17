// The OTHER half of "not easy to read for humans".
//
// Even when the envelope parsed correctly, every AI surface rendered the answer as ONE
// text node: `<div>{message.content}</div>`. Chat models write chat markdown, so the
// customer read this, literally, in one unbroken block:
//
//   **Overnight summary** - Camera 3 offline from 01:10 - No verified events ### Next
//
// These tests drive the real parser the portal now uses.
//
//   deno test prototype/tests/rich_text_test.ts
import { assert, assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
// @ts-ignore: plain ESM shipped to the browser, no types
import { parseInline, parseRichText } from "../../portal/app/rich-text-parse.js";

// The parser is plain JS shipped to the browser; treat its output as data here.
const blocksOf = (text: unknown): any[] => parseRichText(text as string) as any[];
const plain = (spans: any[]) => spans.map((s) => s.text).join("");
const flat = (blocks: any[]) =>
  blocks.map((b: any) =>
    b.items ? b.items.map(plain).join(" | ") : plain(b.spans)).join("\n");

// --------------------------------------------------------------------- the complaint
Deno.test("markdown markers never reach the reader as characters", () => {
  const answer = [
    "### Overnight summary",
    "",
    "**Camera 3** was offline from 01:10. No *verified* events were recorded.",
    "",
    "- Camera 3 offline",
    "- Camera 7 healthy",
  ].join("\n");
  const text = flat(blocksOf(answer));
  for (const marker of ["**", "###", "- ", "*verified*"]) {
    assert(!text.includes(marker), `"${marker}" survived into the visible text: ${text}`);
  }
  assert(text.includes("Camera 3"), text);
});

Deno.test("one wall of text becomes separate paragraphs", () => {
  const blocks = blocksOf("First point about the site.\n\nSecond, unrelated point.");
  assertEquals(blocks.length, 2);
  assertEquals(blocks.every((b: any) => b.type === "p"), true);
});

Deno.test("a bulleted list is a list, not a run-on sentence", () => {
  const blocks = blocksOf("Issues:\n- Camera 3 offline\n- Recorder clock drifted");
  assertEquals(blocks[0].type, "p");
  assertEquals(blocks[1].type, "ul");
  assertEquals(blocks[1].items.length, 2);
  assertEquals(plain(blocks[1].items[1]), "Recorder clock drifted");
});

Deno.test("asterisk, bullet and dash lists are all recognised", () => {
  for (const marker of ["-", "*", "\u2022"]) {
    const blocks = blocksOf(`${marker} one\n${marker} two`);
    assertEquals(blocks[0].type, "ul", `marker ${marker} was not treated as a bullet`);
    assertEquals(blocks[0].items.length, 2);
  }
});

Deno.test("a numbered list keeps its order and drops the literal numbers", () => {
  const blocks = blocksOf("1. Check the recorder\n2. Re-seat camera 3");
  assertEquals(blocks[0].type, "ol");
  assertEquals(plain(blocks[0].items[0]), "Check the recorder");
  assertEquals(plain(blocks[0].items[1]), "Re-seat camera 3");
});

Deno.test("a bulleted list directly after a numbered one is not merged into it", () => {
  const blocks = blocksOf("1. First\n- Second");
  assertEquals(blocks.map((b: any) => b.type), ["ol", "ul"]);
});

Deno.test("headings become their own emphasised line", () => {
  const blocks = blocksOf("## Summary\nNothing happened overnight.");
  assertEquals(blocks[0].type, "h");
  assertEquals(plain(blocks[0].spans), "Summary");
  assertEquals(blocks[1].type, "p");
});

Deno.test("wrapped lines of one paragraph rejoin with a space, not jammed together", () => {
  const blocks = blocksOf("The recorder reported\na clock drift of two minutes.");
  assertEquals(blocks.length, 1);
  assertEquals(plain(blocks[0].spans), "The recorder reported a clock drift of two minutes.");
});

// ------------------------------------------------------------------------- emphasis
Deno.test("bold, italic and code are marked up rather than shown raw", () => {
  const spans = parseInline("**Camera 3** is *offline*; run `wl status`.");
  assertEquals(spans.find((s: any) => s.bold)?.text, "Camera 3");
  assertEquals(spans.find((s: any) => s.italic)?.text, "offline");
  assertEquals(spans.find((s: any) => s.code)?.text, "wl status");
  assert(!plain(spans).includes("*"), plain(spans));
});

Deno.test("a lone asterisk is ordinary punctuation, not broken emphasis", () => {
  assertEquals(plain(parseInline("Camera 3 * see note")), "Camera 3 * see note");
  assertEquals(plain(parseInline("2 * 3 cameras")), "2 * 3 cameras");
});

Deno.test("an unclosed bold marker does not eat the rest of the answer", () => {
  assertEquals(plain(parseInline("**Camera 3 was offline")), "**Camera 3 was offline");
});

Deno.test("identifiers and paths with underscores are not turned into italics", () => {
  // `wl_sync_cameras` rendering as wl + italic "sync" + cameras is worse than useless in
  // an answer someone is meant to act on.
  assertEquals(plain(parseInline("call wl_sync_cameras now")), "call wl_sync_cameras now");
  assertEquals(plain(parseInline("stored in C:\_data_ archive")), "stored in C:\_data_ archive");
  assertEquals(plain(parseInline("see _this_ camera")).includes("_"), false,
               "genuine emphasis at a word boundary must still work");
});

// -------------------------------------------------------------------------- security
Deno.test("a model-authored link is stripped to its label and never becomes a link", () => {
  // An LLM-supplied href is a phishing vector. Real navigation comes from
  // proposed_actions, which the edge function whitelists against SAFE_HREFS.
  const spans = parseInline("See [the report](https://evil.example.com/steal) for detail.");
  assertEquals(plain(spans), "See the report for detail.");
  assert(!JSON.stringify(spans).includes("evil.example.com"));
});

Deno.test("the parser emits data only, never HTML", () => {
  const blocks = blocksOf('<img src=x onerror="alert(1)"> and <b>bold</b>');
  // The angle brackets stay TEXT. RichText renders text nodes, so they are escaped by
  // React; nothing here is ever handed to dangerouslySetInnerHTML.
  assertEquals(plain(blocks[0].spans), '<img src=x onerror="alert(1)"> and <b>bold</b>');
  for (const b of blocks) {
    for (const s of (b.spans || b.items.flat())) {
      assertEquals(typeof s.text, "string");
      assertEquals(Object.keys(s).every((k) => ["text", "bold", "italic", "code"].includes(k)), true);
    }
  }
});

// ---------------------------------------------------------------------- odds and ends
Deno.test("a markdown table is flattened into a readable line", () => {
  const blocks = blocksOf("| Camera | State |\n| --- | --- |\n| Cam 3 | Offline |");
  const text = flat(blocks);
  assert(!text.includes("|"), text);
  assert(!text.includes("---"), text);
  assert(text.includes("Cam 3"), text);
});

Deno.test("empty and whitespace answers produce nothing to render", () => {
  assertEquals(blocksOf(""), []);
  assertEquals(blocksOf("   \n\n  "), []);
  assertEquals(blocksOf(null as any), []);
});

Deno.test("plain prose with no markdown is left exactly as written", () => {
  const prose = "No verified events overnight. Camera 3 stayed offline from 01:10.";
  assertEquals(flat(blocksOf(prose)), prose);
});
