// A highlight's style drawn small (shared/model/markStyle.js), in the
// colour it would take: a letter A over a wash of the colour, or the letter
// with its line under it (straight or wavy) or through it, in the colour's
// line tone (palette.js lineColor), as the PDF draws them. The style
// buttons of the selection popup and of a highlight's menu, the presets,
// and a line highlight's marker in the notes draw with it. `color`: a
// colour as a highlight stores it (#rrggbbaa), or none for the letter
// alone in the text colour.
import React from "react";
import { lineColor, parseColor } from "../model/palette.js";

// The letter, its crossbar left out where a strike runs through it.
const LETTER = "M4.6 11.2 8 3l3.4 8.2";
const CROSSBAR = "M5.9 8.4h4.2";
const LINES = {
  underline: <path d="M2.5 14h11" strokeWidth="1.8" />,
  squiggly: <path d="M2 13.7q1.5-1.9 3 0t3 0 3 0 3 0" strokeWidth="1.5" />,
  strikeout: <path d="M2 7.2h12" strokeWidth="1.8" />,
};

// The letter's ink over a wash: dark over a light colour, white over a dark
// one, the colour taken over white at its alpha.
function inkOver(color) {
  const c = parseColor(color);
  if (!c) return "currentColor";
  const [r, g, b] = c.slice(0, 3).map((v) => 255 - c[3] * (255 - v));
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#262626" : "#ffffff";
}

export function MarkGlyph({ style = "highlight", color = "", size = 16, className = "" }) {
  const line = LINES[style];
  const tone = color ? lineColor(color) : "currentColor";
  return (
    <svg className={`markGlyph ${className}`.trim()} width={size} height={size} viewBox="0 0 16 16" fill="none"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {line ? null : <rect x="1.5" y="1.2" width="13" height="12.6" rx="2.6" fill={color || "currentColor"}
        fillOpacity={color ? undefined : 0.25} />}
      <path d={style === "strikeout" ? LETTER : LETTER + CROSSBAR} stroke={line || !color ? "currentColor" : inkOver(color)}
        strokeWidth="1.5" />
      {line ? <g stroke={tone}>{line}</g> : null}
    </svg>
  );
}
