// A colour picker with alpha (Settings → Appearance › Colors, the swatch
// editor): a saturation / value square under a hue bar and an alpha bar,
// each dragged with any pointer or stepped with the arrow keys, a preview
// over a checkerboard, a hex field that takes #rrggbb or #rrggbbaa, an
// opacity field in percent, and an eyedropper where the browser has one
// (window.EyeDropper, Chromium). Controlled: `value` is {hex, alpha},
// onChange gets the next one on every move. The gradients are inline so
// the stylesheet keeps no raw colours (docs/dev/ui-design.md "Tokens").
import React, { useEffect, useRef, useState } from "react";
import { MIN_ALPHA, hexToHsv, hsvToHex, normalizeColor, swatchCss } from "../model/palette.js";
import { PipetteIcon } from "./Icons";
import { t } from "../i18n/i18n.js";

const CHECKER = "repeating-conic-gradient(rgba(127, 127, 127, 0.35) 0 25%, transparent 0 50%) 0 0 / 10px 10px";
const HUES = "linear-gradient(to right, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00)";
const clamp01 = (v) => Math.min(1, Math.max(0, v));

// A draggable surface: onPoint(x, y) with both in 0..1 from the pointer's
// place, on press and while held; the arrow keys step `onStep(dx, dy)`.
function Surface({ className, style, label, valueText, onPoint, onStep, children }) {
  const ref = useRef(null);
  const point = (e) => {
    const r = ref.current.getBoundingClientRect();
    onPoint(clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height));
  };
  return (
    <div ref={ref} className={className} style={style} role="slider" tabIndex={0} aria-label={label} aria-valuetext={valueText}
      onPointerDown={(e) => { e.preventDefault(); ref.current.setPointerCapture(e.pointerId); ref.current.focus(); point(e); }}
      onPointerMove={(e) => { if (ref.current.hasPointerCapture?.(e.pointerId)) point(e); }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 0.1 : 0.01;
        const dx = e.key === "ArrowRight" ? step : e.key === "ArrowLeft" ? -step : 0;
        const dy = e.key === "ArrowDown" ? step : e.key === "ArrowUp" ? -step : 0;
        if (dx || dy) { e.preventDefault(); onStep(dx, dy); }
      }}>
      {children}
    </div>
  );
}

export function ColorPicker({ value, onChange }) {
  const hex = value?.hex || "#000000";
  const alpha = value?.alpha ?? 1;
  // Hue and saturation are kept here: a gray or black has none, and the
  // hex alone would lose the user's place on the square and the bar.
  const [hsv, setHsv] = useState(() => hexToHsv(hex) || [0, 0, 0]);
  useEffect(() => {
    if (hsvToHex(...hsv) === hex) return; // our own change
    const next = hexToHsv(hex);
    if (next) setHsv((cur) => [next[1] ? next[0] : cur[0], next[1], next[2]]); // a gray keeps the hue
  }, [hex]);
  const [h, s, v] = hsv;
  const pick = (next) => { setHsv(next); onChange({ hex: hsvToHex(...next), alpha }); };
  const setAlpha = (a) => onChange({ hex, alpha: Math.round(Math.min(1, Math.max(MIN_ALPHA, a)) * 100) / 100 });

  // The hex field keeps a draft while it is typed; a valid colour applies
  // at once (#rrggbbaa sets the alpha too), the rest is dropped on blur.
  const [draft, setDraft] = useState(null);
  const applyHex = (text) => {
    const c = normalizeColor(text.trim().startsWith("#") ? text.trim() : `#${text.trim()}`);
    if (!c || !/^#?[0-9a-f]{6}([0-9a-f]{2})?$/i.test(text.trim())) return false;
    const withAlpha = text.trim().replace("#", "").length === 8;
    onChange({ hex: c.slice(0, 7), alpha: withAlpha ? Math.max(MIN_ALPHA, Math.round(parseInt(c.slice(7), 16) / 255 * 100) / 100) : alpha });
    return true;
  };
  const eyedropper = typeof window !== "undefined" && "EyeDropper" in window;
  const pct = Math.round(alpha * 100);
  const pure = hsvToHex(h, 1, 1);
  return (
    <div className="colorPicker">
      <Surface className="cpArea" label={t("Saturation and brightness")} valueText={`${Math.round(s * 100)}%, ${Math.round(v * 100)}%`}
        style={{ background: `linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, ${pure})` }}
        onPoint={(x, y) => pick([h, x, 1 - y])} onStep={(dx, dy) => pick([h, clamp01(s + dx), clamp01(v - dy)])}>
        <span className="cpHandle" style={{ left: `${s * 100}%`, top: `${(1 - v) * 100}%`, background: hex }} />
      </Surface>
      <Surface className="cpBar" label={t("Hue")} valueText={`${Math.round(h)}°`} style={{ background: HUES }}
        onPoint={(x) => pick([x * 360, s, v])} onStep={(dx) => pick([(h + dx * 360 + 360) % 360, s, v])}>
        <span className="cpHandle" style={{ left: `${(h / 360) * 100}%`, background: pure }} />
      </Surface>
      <Surface className="cpBar cpAlphaBar" label={t("Opacity")} valueText={`${pct}%`}
        style={{ background: `linear-gradient(to right, transparent, ${hex}), ${CHECKER}` }}
        onPoint={(x) => setAlpha(x)} onStep={(dx) => setAlpha(alpha + dx)}>
        <span className="cpHandle" style={{ left: `${alpha * 100}%`, background: swatchCss({ hex, alpha }) }} />
      </Surface>
      <div className="cpFields">
        <span className="cpPreview" style={{ background: CHECKER }}><span style={{ background: swatchCss({ hex, alpha }) }} /></span>
        <input className="cpHex" value={draft ?? hex} aria-label={t("Hex color")} spellCheck={false}
          onChange={(e) => { setDraft(e.target.value); if (applyHex(e.target.value)) setDraft(null); }}
          onBlur={() => setDraft(null)} onKeyDown={(e) => { if (e.key === "Enter") { applyHex(e.currentTarget.value); setDraft(null); } }} />
        <label className="cpAlphaField">
          <input type="number" min={Math.round(MIN_ALPHA * 100)} max={100} step={1} value={pct} aria-label={t("Opacity")}
            onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n)) setAlpha(n / 100); }} />
          <span>%</span>
        </label>
        {eyedropper ? (
          <button type="button" className="uiBtn sm iconSq" title={t("Pick a color from the screen")} aria-label={t("Pick a color from the screen")}
            onClick={() => new window.EyeDropper().open().then((r) => onChange({ hex: r.sRGBHex.toLowerCase(), alpha })).catch(() => {})}>
            <PipetteIcon size={14} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
