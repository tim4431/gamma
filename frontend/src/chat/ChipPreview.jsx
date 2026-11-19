// A passage's preview, as the composer's chips (ChatDock.jsx SelChip) and
// a sent message's quotes (ChatTranscript.jsx) show it: the Markdown
// dropped, the math typeset (chat/chipText.js chipSegments, KaTeX). `max`
// characters of the source are read; `className` the element's look.
import React, { useMemo } from "react";
import { renderKatex } from "../editor/LatexEditor";
import { chipSegments } from "./chipText";

export default function ChipPreview({ text, max = 600, className = "chatSelChipText" }) {
  const segments = useMemo(() => chipSegments((text || "").slice(0, max)), [text, max]);
  return (
    <span className={className}>
      {segments.map((seg, i) => {
        if (seg.text) return <React.Fragment key={i}>{seg.text}</React.Fragment>;
        const html = renderKatex(seg.math, false);
        return html ? <span key={i} className="chatSelChipMath" dangerouslySetInnerHTML={{ __html: html }} />
          : <code key={i}>{seg.math}</code>;
      })}
    </span>
  );
}
