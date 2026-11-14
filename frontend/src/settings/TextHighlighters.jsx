// Settings → Appearance › Text highlighters (docs/dev/settings.md "Text
// highlighters"; shared/model/markStyle.js): the account's highlighters in
// their order, the ones on the strip first and the hidden ones under a
// line, each a row — its grip (drag it, or its arrow keys, within its
// group), its look, its name (none: it is called by its look), its key, On
// the strip, an editor (its style, its colour from the Text highlights
// group or any colour), Duplicate and Remove — then Add highlighter, and
// Reset, which puts the one highlighter an account starts with back. Every
// write is a command of useTextHighlighter's (`hl`, App's textHighlighter),
// the strip's own, so each says in the pill what the strip's menu says
// (Remove and Reset with an Undo).
import React from "react";
import { createPortal } from "react-dom";
import { NameInput, Section } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import {
  MARK_STYLES, MAX_PRESETS, MAX_PRESET_NAME, MAX_SHOWN_PRESETS, hiddenPresets, presetFor, presetKey, presetLook, presetName, shownPresets,
} from "../shared/model/markStyle.js";
import { colorName, highlightColor, pickerColors } from "../shared/model/palette.js";
import { MarkGlyph } from "../shared/ui/MarkGlyph.jsx";
import { ColorPicker } from "../shared/ui/ColorPicker";
import { useDragOrder } from "../shared/ui/useDragOrder.js";
import { StyleRow, SwatchRow } from "../pdf/MarkTools.jsx";
import {
  ChevronDownIcon, DuplicateIcon, EyeIcon, EyeOffIcon, GripIcon, PipetteIcon, PlusIcon, TrashIcon,
} from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

function HighlighterRow({ p, hl, colors, open, onOpen, drag, sourceProps, keyProps }) {
  const [custom, setCustom] = React.useState(false); // the colour picker open in the editor
  const list = hl.list;
  const key = presetKey(list, p.id);
  const look = presetLook(p);
  const shown = shownPresets(list).length;
  const lastShown = !p.hidden && shown <= 1;
  const edit = (patch) => hl.edit(p.id, patch);
  const name = presetName(p);
  return (
    <div data-order-id={p.id} className={`hlRow${p.hidden ? " off" : ""}${drag?.id === p.id ? " lifted" : ""}`}>
      <div className="hlRowHead">
        <button type="button" className="barLayoutGrip" aria-label={t("Move {label}", { label: name })}
          title={t("Drag to reorder, or use the arrow keys")} {...sourceProps(p.id)} {...keyProps(p.id)}><GripIcon size={16} /></button>
        <MarkGlyph style={p.style} color={highlightColor(p.color)} size={18} />
        <NameInput className="paletteGroupName hlName" value={p.name} allowEmpty maxLength={MAX_PRESET_NAME} placeholder={look}
          aria-label={t("Name of {look}", { look })} onCommit={(next) => edit({ name: next })} />
        {key ? <span className="hlKey" title={t("Key {n}", { n: key })}>{key}</span> : <span className="hlKey none" aria-hidden="true" />}
        <button type="button" className="uiClose uiCloseSm" aria-pressed={!p.hidden}
          disabled={p.hidden ? shown >= MAX_SHOWN_PRESETS : lastShown}
          title={p.hidden ? (shown >= MAX_SHOWN_PRESETS ? t("Up to {n} on the strip", { n: MAX_SHOWN_PRESETS }) : t("Show on the strip"))
            : lastShown ? t("One stays on the strip") : t("Hide from the strip")}
          aria-label={t("{name} on the strip", { name })}
          onClick={() => (p.hidden ? hl.show(p.id) : hl.hide(p.id))}>
          {p.hidden ? <EyeOffIcon size={14} /> : <EyeIcon size={14} />}
        </button>
        <button type="button" className="uiClose uiCloseSm" aria-expanded={open} title={t("Edit")} aria-label={t("Edit {name}", { name })}
          onClick={onOpen}><ChevronDownIcon size={14} className={open ? "hlOpen" : undefined} /></button>
        <button type="button" className="uiClose uiCloseSm" disabled={list.length >= MAX_PRESETS}
          title={list.length >= MAX_PRESETS ? t("Up to {n} highlighters", { n: MAX_PRESETS }) : t("Duplicate")}
          aria-label={t("Duplicate {name}", { name })} onClick={() => hl.duplicate(p.id, { arm: false })}>
          <DuplicateIcon size={14} />
        </button>
        <button type="button" className="uiClose uiCloseSm" disabled={lastShown} title={lastShown ? t("One stays on the strip") : t("Remove")}
          aria-label={t("Remove {name}", { name })} onClick={() => hl.remove(p.id)}>
          <TrashIcon size={14} />
        </button>
      </div>
      {open ? (
        <div className="hlRowEditor">
          <StyleRow value={p.style} color={highlightColor(p.color)} onPick={(style) => edit({ style })} />
          <div className="paletteSwatches" role="group" aria-label={t("Colors of {name}", { name })}>
            <SwatchRow colors={colors} style={p.style}
              selected={(c) => c.hex === p.color.hex && Math.abs(c.alpha - p.color.alpha) < 0.005}
              label={(c) => t("{color} for this highlighter", { color: t(colorName(c)) })}
              onPick={(c) => edit({ color: { hex: c.hex, alpha: c.alpha } })} />
            <button type="button" className={"colorBtn inkSwatch paletteSwatch paletteAdd" + (custom ? " selected" : "")} aria-pressed={custom}
              title={t("Another colour")} aria-label={t("Another colour for {name}", { name })}
              onClick={() => setCustom(!custom)}><PipetteIcon size={14} /></button>
          </div>
          {custom ? (
            <div className="paletteEditor" role="group" aria-label={t("Edit color")}>
              <ColorPicker value={p.color} onChange={(c) => edit({ color: c })} />
              <div className="paletteEditorSide">
                <span className="paletteEditorName">{t(colorName(p.color))}</span>
                <span className="settingDesc">{t("A wash uses the opacity; a line is drawn in a deeper tone of the colour, opaque.")}</span>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// One group (on the strip, or hidden): its rows, dragged within it.
function HighlighterGroup({ items, hl, colors, openId, setOpenId, setSaid }) {
  const ref = React.useRef(null);
  const list = hl.list;
  const move = (id, to) => {
    const next = hl.place(id, to);
    if (!next) return;
    const group = next.filter((p) => !!p.hidden === !!items[0]?.hidden);
    const p = next.find((x) => x.id === id);
    setSaid(t("{label}: {n} of {count}", { label: presetName(p), n: group.indexOf(p) + 1, count: group.length }));
  };
  const { sourceProps, keyProps, drag } = useDragOrder({ containerRef: ref, axis: "y", start: "handle", onMove: move, onStatus: setSaid });
  const dragged = drag ? list.find((p) => p.id === drag.id) : null;
  return (
    <div className="hlList barLayoutList" ref={ref}>
      {items.map((p) => (
        <HighlighterRow key={p.id} p={p} hl={hl} colors={colors} open={openId === p.id} onOpen={() => setOpenId(openId === p.id ? null : p.id)}
          drag={drag} sourceProps={sourceProps} keyProps={keyProps} />
      ))}
      {drag?.bar ? <span className="dropBar" style={drag.bar} aria-hidden="true" /> : null}
      {dragged ? createPortal(
        <div className="dragGhost barLayoutGhost" style={{ left: drag.x, top: drag.y }} aria-hidden="true">
          <MarkGlyph style={dragged.style} color={highlightColor(dragged.color)} size={14} />{presetName(dragged)}
        </div>,
        document.body,
      ) : null}
    </div>
  );
}

// The next look no highlighter has: a wash in the first Text highlights
// swatch no wash highlighter has, then an underline, a wavy underline and a
// strikethrough the same way; the first swatch's wash when every one is taken.
function nextLook(list, colors) {
  for (const style of MARK_STYLES) {
    const c = colors.find((s) => !presetFor(list, style, s));
    if (c) return { style, color: { hex: c.hex, alpha: c.alpha } };
  }
  return { style: "highlight", color: colors[0] };
}

// `hl` useTextHighlighter's commands and the list in effect (`hl.list`),
// `presets` the stored list ([] while none is: nothing to reset), `palette`.
export function TextHighlighters({ hl, presets, palette }) {
  const list = hl.list;
  const colors = pickerColors(palette, "highlight");
  const [openId, setOpenId] = React.useState(null);
  const [said, setSaid] = React.useState("");
  const shown = shownPresets(list), hidden = hiddenPresets(list);
  return (
    <Section title={t("Text highlighters")} scope="account" prefs={SECTION_PREFS.appearance["Text highlighters"]}
      action={(
        <button type="button" className="uiBtn sm" disabled={!presets?.length}
          title={t("Back to the one highlighter an account starts with")} onClick={hl.reset}>
          {t("Reset")}
        </button>
      )}>
      <p className="settingDesc hlHint">
        {t("Each is a style and a colour, named if you like. The shown ones, up to four, are buttons on the viewer's strip and in the selection bar; hidden ones are a menu away. Keys 1–9 press them in this order.")}
      </p>
      <HighlighterGroup items={shown} hl={hl} colors={colors} openId={openId} setOpenId={setOpenId} setSaid={setSaid} />
      {hidden.length ? (
        <>
          <div className="hlSep" role="separator" aria-label={t("Hidden — not on the strip")}>{t("Hidden — not on the strip")}</div>
          <HighlighterGroup items={hidden} hl={hl} colors={colors} openId={openId} setOpenId={setOpenId} setSaid={setSaid} />
        </>
      ) : null}
      <div className="hlActions">
        <button type="button" className="uiBtn sm" disabled={list.length >= MAX_PRESETS}
          title={list.length >= MAX_PRESETS ? t("Up to {n} highlighters", { n: MAX_PRESETS }) : undefined}
          onClick={() => {
            const id = hl.saveLook(nextLook(list, colors));
            if (id) setOpenId(id);
          }}>
          <PlusIcon size={14} />{t("Add highlighter")}
        </button>
      </div>
      <span className="srOnly" aria-live="polite">{said}</span>
    </Section>
  );
}
