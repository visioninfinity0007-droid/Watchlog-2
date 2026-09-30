// Parsing a model's prose into display blocks. PURE: no React, no DOM, no HTML.
// Split out from rich-text.js so the parsing rules can be tested directly
// (prototype/tests/rich_text_test.ts).
//
// Every place that showed an AI answer did `<div>{text}</div>`, i.e. one text node. The
// model writes the way chat models write -- "**Overnight**", "- Camera 3 offline",
// "### Summary" -- so the customer read the asterisks and hashes as literal characters,
// in one unbroken wall. That is the "hard to read" half of the complaint.
//
// This turns that into paragraphs, lists and emphasis. Two rules it must never break:
//
//   1. NOTHING here produces HTML. The answer is model output, so it is untrusted: every
//      fragment below leaves as a React text node. No dangerouslySetInnerHTML, ever.
//   2. A model-authored link is never rendered as a link. An LLM-supplied href is a
//      phishing vector; WatchLog navigation comes from proposed_actions, which are
//      whitelisted server-side against SAFE_HREFS. Link syntax here degrades to its label.

const BULLET = /^\s{0,3}(?:[-*•]|–)\s+(.*)$/;
const NUMBERED = /^\s{0,3}(\d{1,2})[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*)$/;
const TABLE_DIVIDER = /^\s*\|?[\s:-]*\|[\s:|-]*$/;

/** `[label](https://...)` -> `label`. Deliberately lossy: see rule 2 above. */
function dropLinks(text) {
  return text.replace(/\[([^\]]*)\]\((?:[^)]*)\)/g, "$1");
}

/** A markdown table row becomes a readable line rather than a row of pipes. */
function flattenTableRow(line) {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "")
    .split("|").map((c) => c.trim()).filter(Boolean).join(" · ");
}

/**
 * An emphasis marker may only OPEN at the start of a word, and only CLOSE at the end of
 * one. Without this, `wl_sync_cameras` renders as "wl" + italic "sync" + "cameras" and a
 * Windows path like C:\_data_ loses its underscores -- both actively misleading in an
 * answer a customer is meant to act on.
 */
const CAN_OPEN = /[\s([{"'‘“]/;
const CAN_CLOSE = /[\s.,;:!?)\]}"'’”]/;
const opensHere = (src, i) => i === 0 || CAN_OPEN.test(src[i - 1]);
const closesHere = (src, end) => end >= src.length || CAN_CLOSE.test(src[end]);

/**
 * Split one line into emphasis spans. Returns [{text, bold, italic, code}].
 * Unmatched markers are left as ordinary characters -- a lone asterisk is not emphasis.
 */
export function parseInline(line) {
  const src = dropLinks(String(line ?? ""));
  const spans = [];
  let buf = "";
  const flush = (mark) => {
    if (buf) spans.push({ text: buf, ...(mark || {}) });
    buf = "";
  };
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);
    const take = (re, mark) => {
      const m = re.exec(rest);
      if (!m || !opensHere(src, i) || !closesHere(src, i + m[0].length)) return false;
      flush();
      spans.push({ text: m[1], ...mark });
      i += m[0].length;
      return true;
    };
    if (take(/^\*\*([^*]+)\*\*/, { bold: true })) continue;
    if (take(/^__([^_]+)__/, { bold: true })) continue;
    if (take(/^`([^`]+)`/, { code: true })) continue;
    if (take(/^\*([^*\s][^*]*)\*/, { italic: true })) continue;
    if (take(/^_([^_\s][^_]*)_/, { italic: true })) continue;
    buf += src[i];
    i += 1;
  }
  flush();
  return spans.length ? spans : [{ text: "" }];
}

/**
 * Turn a model answer into display blocks.
 *
 * Blocks: {type:"p"|"h", spans} and {type:"ul"|"ol", items:[spans]}. Pure and
 * framework-free so it can be tested without a browser.
 */
export function parseRichText(text) {
  const raw = String(text ?? "").replace(/\r\n/g, "\n").trim();
  if (!raw) return [];
  const lines = raw.split("\n");
  const blocks = [];
  let para = [];
  let list = null;

  const endPara = () => {
    if (para.length) blocks.push({ type: "p", spans: parseInline(para.join(" ")) });
    para = [];
  };
  const endList = () => {
    if (list && list.items.length) blocks.push(list);
    list = null;
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { endPara(); endList(); continue; }

    const heading = HEADING.exec(line);
    if (heading) {
      endPara(); endList();
      blocks.push({ type: "h", spans: parseInline(heading[2]) });
      continue;
    }

    // A divider row carries no information once the pipes are gone.
    if (TABLE_DIVIDER.test(trimmed) && trimmed.includes("|")) continue;
    const isTableRow = trimmed.includes("|") && trimmed.split("|").length > 2;

    const bullet = BULLET.exec(line);
    if (bullet) {
      endPara();
      if (!list || list.type !== "ul") { endList(); list = { type: "ul", items: [] }; }
      list.items.push(parseInline(bullet[1]));
      continue;
    }

    const numbered = NUMBERED.exec(line);
    if (numbered) {
      endPara();
      if (!list || list.type !== "ol") { endList(); list = { type: "ol", items: [] }; }
      list.items.push(parseInline(numbered[2]));
      continue;
    }

    endList();
    para.push(isTableRow ? flattenTableRow(trimmed) : trimmed);
  }
  endPara();
  endList();
  return blocks;
}

