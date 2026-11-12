// The text highlighters on the viewer's strip (docs/dev/settings.md "Text
// highlighters"): a button per highlighter on the strip, in the list's
// order, each its look (HighlighterGlyph); the armed one pressed, and one
// armed from the hidden ones pinned after them so it can be turned off. A
// tap arms it (with a mouse or a pen, a selection is then marked at once in
// its look; with a finger the popup opens on it, ringed), a tap on the
// armed one turns it off, a right-click or a held finger opens its menu
// (HighlighterMenu), where it is renamed, restyled, recoloured, moved,
// duplicated, hidden or removed. HighlighterRows are the strip's ⋯ rows
// while the whole item is hidden in Viewer tools. `hl` is
// useTextHighlighter's.
import React from "react";
import { ContextMenu, MenuDivider, MenuItem, SubMenuItem } from "../shared/ui/Menus";
import { menuPress } from "../shared/ui/press.js";
import { MarkGlyph } from "../shared/ui/MarkGlyph.jsx";
import {
  ChevronDownIcon, ChevronUpIcon, DuplicateIcon, EyeIcon, EyeOffIcon, HighlightIcon, PaletteIcon, SettingsIcon, SparklesIcon, Trash2Icon,
} from "../shared/ui/Icons";
import { MAX_PRESETS, MAX_PRESET_NAME, MAX_SHOWN_PRESETS, presetKey, presetLook, presetName, startersMissing } from "../shared/model/markStyle.js";
import { colorName, highlightColor, pickerColors } from "../shared/model/palette.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import { composing } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { GroupRows, HighlighterGlyph, StyleRow, SwatchRow } from "./MarkTools.jsx";
import { stripPresets } from "./highlighterState.js";

// "Definition (Underline in Blue)" for a named one, else its look.
const labelOf = (p) => (p.name ? `${p.name} (${presetLook(p)})` : presetLook(p));
const glyphIcon = (p) => (props) => <MarkGlyph style={p.style} color={highlightColor(p.color)} size={props.size} />;

// `only`: the item is hidden in Viewer tools and pinned because one is
// armed, so the armed one alone. `menuAt(el)` places a button's menu.
export function HighlighterStrip({ hl, only = false, menuAt }) {
  const buttons = stripPresets(hl.list, hl.armedId, only);
  const single = hl.list.length === 1;
  return (
    <div className="pdfCtlGroup stripMarks" role="group" aria-label={t("Text highlighters")} data-guide="viewer.highlighter">
      {buttons.map((p) => {
        const on = hl.armedId === p.id;
        const key = presetKey(hl.list, p.id);
        const label = labelOf(p);
        const keyText = key ? ` ${t("Key {n}.", { n: key })}` : "";
        return (
          <button key={p.id} type="button" data-hl-preset={p.id}
            className={"stripInkBtn stripMarkBtn" + (on ? " modeActive" : "") + (p.hidden ? " pinned" : "")}
            aria-pressed={on} aria-keyshortcuts={key ? String(key) : undefined}
            aria-label={t("Text highlighter: {name}", { name: presetName(p) })}
            title={on
              ? t("{label} is on. Click to turn it off (Esc); right-click or hold for its options.", { label }) + keyText
              : t("{label}. Click to highlight what you select; right-click or hold for its options.", { label }) + keyText
                + (single ? ` ${t("Duplicate it from its menu to have more.")}` : "")}
            onClick={() => hl.toggle(p.id)}
            {...menuPress((e) => { e.preventDefault(); hl.openMenu(p.id, menuAt(e.currentTarget)); })}>
            <HighlighterGlyph preset={p} />
          </button>
        );
      })}
    </div>
  );
}

// A strip highlighter's menu: its name, style and colour (each edit keeps
// the menu open, as an editor's), the colour group, then turning it on or
// off, moving it, Duplicate (the menu goes on to the copy), Hide or Show,
// the hidden ones, the three starters while none is there, Remove, and
// Edit highlighters….
export function HighlighterMenu({ hl }) {
  const palette = usePalette();
  const p = hl.menu ? hl.list.find((x) => x.id === hl.menu.id) : null;
  if (!hl.menu || !p) return null;
  const colors = pickerColors(palette, "highlight");
  const close = hl.closeMenu;
  const then = (run) => () => { close(); run(); };
  const on = hl.armedId === p.id;
  const key = presetKey(hl.list, p.id);
  const group = hl.list.filter((x) => !!x.hidden === !!p.hidden);
  const at = group.indexOf(p);
  const lastShown = !p.hidden && hl.shown.length <= 1;
  const groups = palette.groups.filter((g) => g.colors.length);
  return (
    <ContextMenu x={hl.menu.x} y={hl.menu.y} onClose={close} className="hlMenu" aria-label={t("{name} highlighter", { name: presetName(p) })}>
      <div className="markMenu">
        <div className="hlMenuHead" role="group" aria-label={t("Name")}>
          <MarkGlyph style={p.style} color={highlightColor(p.color)} size={18} />
          {/* typed straight into the list (its saves wait a moment, prefs.js): no draft to lose as the menu closes */}
          <input className="hlMenuName" type="text" value={p.name} maxLength={MAX_PRESET_NAME} spellCheck={false}
            placeholder={presetLook(p)} aria-label={t("Name of this highlighter")}
            onChange={(e) => hl.edit(p.id, { name: e.target.value })}
            onKeyDown={(e) => { if (composing(e)) return; if (e.key === "Enter") e.currentTarget.blur(); }} />
        </div>
        <StyleRow value={p.style} color={highlightColor(p.color)} menu onPick={(style) => hl.edit(p.id, { style })} />
        <div className="colorRow">
          <SwatchRow colors={colors} style={p.style} menu label={(c) => t("{color} for this highlighter", { color: t(colorName(c)) })}
            selected={(c) => c.hex === p.color.hex && Math.abs(c.alpha - p.color.alpha) < 0.005}
            onPick={(c) => hl.edit(p.id, { color: { hex: c.hex, alpha: c.alpha } })} />
        </div>
      </div>
      {groups.length > 1 ? (
        <SubMenuItem id="hlGroup" icon={PaletteIcon} label={t("Color group")}>
          <GroupRows palette={palette} onPick={hl.setGroup} menu />
        </SubMenuItem>
      ) : null}
      <MenuDivider />
      {on ? (
        <MenuItem icon={HighlightIcon} keys="Esc" onClick={then(hl.off)}>{t("Turn off the highlighter")}</MenuItem>
      ) : (
        <MenuItem icon={glyphIcon(p)} keys={key ? String(key) : undefined} onClick={then(() => hl.arm(p.id))}>
          {t("Highlight with {name}", { name: presetName(p) })}
        </MenuItem>
      )}
      <MenuItem icon={ChevronUpIcon} disabled={at <= 0} onClick={() => hl.move(p.id, -1)}>{t("Move up")}</MenuItem>
      <MenuItem icon={ChevronDownIcon} disabled={at >= group.length - 1} onClick={() => hl.move(p.id, 1)}>{t("Move down")}</MenuItem>
      <MenuItem icon={DuplicateIcon} disabled={hl.list.length >= MAX_PRESETS}
        title={hl.list.length >= MAX_PRESETS ? t("Up to {n} highlighters", { n: MAX_PRESETS }) : undefined}
        onClick={() => hl.duplicate(p.id)}>{t("Duplicate")}</MenuItem>
      {p.hidden ? (
        <MenuItem icon={EyeIcon} disabled={hl.shown.length >= MAX_SHOWN_PRESETS}
          title={hl.shown.length >= MAX_SHOWN_PRESETS ? t("Up to {n} on the strip", { n: MAX_SHOWN_PRESETS }) : undefined}
          onClick={then(() => hl.show(p.id))}>{t("Show on the strip")}</MenuItem>
      ) : (
        <MenuItem icon={EyeOffIcon} disabled={lastShown} title={lastShown ? t("One stays on the strip") : undefined}
          onClick={then(() => hl.hide(p.id))}>{t("Hide from the strip")}</MenuItem>
      )}
      {hl.hidden.length ? (
        <SubMenuItem id="hlHidden" icon={EyeOffIcon} label={t("Hidden highlighters")}>
          {hl.hidden.map((h) => {
            const k = presetKey(hl.list, h.id);
            return (
              <MenuItem key={h.id} role="menuitemradio" aria-checked={hl.armedId === h.id} icon={glyphIcon(h)} keys={k ? String(k) : undefined}
                onClick={then(() => hl.arm(h.id))}>{presetName(h)}</MenuItem>
            );
          })}
        </SubMenuItem>
      ) : null}
      {startersMissing(hl.list) ? (
        <MenuItem icon={SparklesIcon} onClick={then(hl.addStarters)}>{t("Add Definition, Question and Disputed")}</MenuItem>
      ) : null}
      <MenuItem icon={Trash2Icon} danger disabled={lastShown} title={lastShown ? t("One stays on the strip") : undefined}
        onClick={then(() => hl.remove(p.id))}>{t("Remove")}</MenuItem>
      <MenuDivider />
      <MenuItem icon={SettingsIcon} onClick={then(hl.openHighlighters)}>{t("Edit highlighters…")}</MenuItem>
    </ContextMenu>
  );
}

// The strip's ⋯ rows while the whole item is hidden in Viewer tools: the
// highlighters as one flyout, a row each (its key beside it), a row arming
// it; then turning the armed one off. `close` wraps a row (App's stripRow).
export function HighlighterRows({ hl, close }) {
  return (
    <SubMenuItem key="highlighter" id="highlighters" icon={HighlightIcon} label={t("Text highlighters")}>
      {hl.list.map((p) => {
        const k = presetKey(hl.list, p.id);
        return (
          <MenuItem key={p.id} role="menuitemradio" aria-checked={hl.armedId === p.id} icon={glyphIcon(p)} keys={k ? String(k) : undefined}
            onClick={close(() => hl.arm(p.id))}>{presetName(p)}</MenuItem>
        );
      })}
      {hl.on ? <MenuItem icon={HighlightIcon} keys="Esc" onClick={close(hl.off)}>{t("Turn off the highlighter")}</MenuItem> : null}
    </SubMenuItem>
  );
}
