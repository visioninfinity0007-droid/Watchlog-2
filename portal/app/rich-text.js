// Rendering the parsed blocks. The parsing rules live in rich-text-parse.js.
//
// NOTHING here produces HTML: every fragment leaves as a React text node, because
// the answer is untrusted model output. No dangerouslySetInnerHTML, ever.
import { parseRichText } from "./rich-text-parse";

function Spans({ spans }) {
  return spans.map((s, i) => {
    if (s.bold) return <strong key={i}>{s.text}</strong>;
    if (s.code) return <code key={i} className="wlAiCode">{s.text}</code>;
    if (s.italic) return <em key={i}>{s.text}</em>;
    return <span key={i}>{s.text}</span>;
  });
}

/**
 * Render a model answer as readable prose.
 *
 * `className` is applied to the wrapper so each surface keeps its own type styling.
 */
export default function RichText({ text, className }) {
  const blocks = parseRichText(text);
  if (!blocks.length) return null;
  // whiteSpace is set inline, not in CSS: the surfaces that pass a className (the chat
  // bubble's .messageText) set white-space:pre-wrap for the old single-text-node rendering,
  // and which of two single-class rules wins depends on stylesheet injection order. Blocks
  // carry their own spacing, so pre-wrap here would double it.
  return <div className={`wlAiProse${className ? ` ${className}` : ""}`} style={{ whiteSpace: "normal" }}>
    {blocks.map((b, i) => {
      if (b.type === "h") return <p className="wlAiHeading" key={i}><Spans spans={b.spans}/></p>;
      if (b.type === "ul") return <ul className="wlAiList" key={i}>
        {b.items.map((it, j) => <li key={j}><Spans spans={it}/></li>)}
      </ul>;
      if (b.type === "ol") return <ol className="wlAiList" key={i}>
        {b.items.map((it, j) => <li key={j}><Spans spans={it}/></li>)}
      </ol>;
      return <p key={i}><Spans spans={b.spans}/></p>;
    })}
  </div>;
}
