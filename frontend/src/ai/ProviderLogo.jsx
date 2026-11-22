// A connection's logo by its service (docs/dev/ai.md "Other services"): the
// connect dialog's service tiles, a connection's card, the model menu's
// connection headings and the chat's model chip. The marks are
// @lobehub/icons-static-svg 1.105.0's (MIT licence, Copyright (c) LobeHub),
// kept as data in providerMarks.js with which service wears which.
//
// A brand's colours stay its own; a mono mark, and the mono part of a
// colour one, takes the colour of the text around it; a colour fill is
// lifted toward the ink on a dark scheme, so a deep blue stays legible
// there (.providerLogoTone in modelMenu.css). A service with no mark (a
// custom endpoint) draws the globe, or nothing with `fallback={false}`.
// Decorative: what it stands for is always named beside it.
import { useId } from "react";
import { GlobeIcon } from "../shared/ui/Icons";
import { MARKS, serviceMark } from "./providerMarks.js";
import "./modelMenu.css";

export function ProviderLogo({ service, size = 16, fallback = true }) {
  const id = useId();
  const mark = MARKS[serviceMark(service)];
  if (!mark) return fallback ? <GlobeIcon size={size} className="providerLogo" aria-hidden="true" /> : null;
  return (
    <svg className="providerLogo" width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
      fillRule={mark.evenodd ? "evenodd" : undefined} aria-hidden="true" focusable="false">
      {mark.gradients ? (
        <defs>
          {mark.gradients.map(([x1, y1, x2, y2, color, fade], i) => (
            <linearGradient key={String(i)} id={`${id}g${i}`} gradientUnits="userSpaceOnUse" x1={x1} y1={y1} x2={x2} y2={y2}>
              <stop stopColor={color} />
              <stop offset={fade} stopColor={color} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
      ) : null}
      {mark.paths.map(([d, fill], i) => (
        typeof fill === "number" ? <path key={String(i)} d={d} fill={`url(#${id}g${fill})`} />
          : fill ? <path key={String(i)} d={d} className="providerLogoTone" fill={fill} style={{ "--logo-tone": fill }} />
            : <path key={String(i)} d={d} />
      ))}
    </svg>
  );
}
