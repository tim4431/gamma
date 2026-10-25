// The selection toolbar (docs/dev/ui-design.md "Block editor interactions"):
// Notion's floating bar over a note's selected text — bold, italic,
// underline, strikethrough, inline code, inline equation, link, highlight,
// and Color, which opens a row of text colors and background tints under
// the tools. Each tool but Color is a catalog command
// (editor/blockCommands.js) run with the row's command context, so a
// button, its key and the palette never disagree; the command's `state`
// presses its button where the selection carries the mark and greys it out
// where it would do nothing, and the bar stays away where no tool applies
// (math, a code fence). Color writes the colored runs of mdMarks.recolor.
//
// The bar sits above the selection's first line, under its last when there
// is no room, inside the box the note scrolls in, and follows it as that
// scrolls (shared/ui/placement.js: placed before it is painted). A press
// never takes the focus or the selection from the editor. When it shows is
// useSelectionToolbar.js; on a touch screen the editing bar (EditBar.jsx)
// has these tools instead.
import React, { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { commandById, commandChord } from "../app/commands.js";
import { commandIcon } from "../app/commandIcons.jsx";
import { t } from "../shared/i18n/i18n.js";
import { chordLabel } from "../shared/lib/hotkeys.js";
import { placeFloating, scrollBox, useFloatingPosition } from "../shared/ui/placement.js";
import { ChevronDownIcon } from "../shared/ui/Icons";
import { colorStateAt, runRecolor } from "./markCommands.js";
import { TEXT_COLORS, tint } from "./mdMarks.js";

const COLOR = "color";
// The tools, in groups between separators: catalog command ids, and Color.
const GROUPS = [
  ["block.bold", "block.italic", "block.underline", "block.strike"],
  ["block.code", "block.math", "block.link"],
  ["block.highlight", COLOR],
];
const COMMAND_IDS = GROUPS.flat().filter((id) => id !== COLOR);

// The gap between the bar and the selected line.
const GAP = 6;

// Where the bar goes for the selection [from, to] of `view`: above its first
// line, or under its last, inside the box the editor scrolls in (the notes
// pane); out of sight (data-away) while the selection is scrolled out of it.
function placeBar(el, view, from, to) {
  const start = view.coordsAtPos(from, 1), end = view.coordsAtPos(to, -1);
  const box = scrollBox(view.dom)?.getBoundingClientRect() || { top: 0, bottom: window.innerHeight };
  const away = !start || !end || end.bottom < box.top || start.top > box.bottom;
  el.toggleAttribute("data-away", away);
  if (away) return { left: 0, top: 0 };
  return placeFloating(el.getBoundingClientRect(), {
    x: start.left, y: start.top - GAP, anchorBottom: true, flipY: end.bottom + GAP,
    bounds: { top: Math.max(0, box.top), bottom: Math.min(window.innerHeight, box.bottom) },
  });
}

// A press keeps the editor's focus and selection, and stays out of the row
// (the bar is portalled, but React hands its events to the row's handlers).
const hold = (e) => { e.preventDefault(); e.stopPropagation(); };
const sameColor = (a, b) => (a || "").toLowerCase() === (b || "").toLowerCase();

// `context()`: the row's command context, built fresh for each press;
// `selection`: {from, to, text} (useSelectionToolbar); `keybindings`: the
// account's, for the chords in the titles.
export default function SelectionToolbar({ context, selection, keybindings }) {
  const ref = useRef(null);
  const [colorsOpen, setColorsOpen] = useState(false);
  const { from, to, text } = selection;
  const ctx = context();
  const view = ctx.editor?.view;
  const pos = useFloatingPosition(ref, (el) => placeBar(el, view, from, to), [view, from, to, text, colorsOpen], { follow: true });
  if (!view) return null;

  const states = new Map(COMMAND_IDS.map((id) => [id, commandById(id).state(ctx)]));
  const color = colorStateAt(text, from, to);
  if (color.blocked && [...states.values()].every((s) => s === "blocked")) return null;

  const title = (label, chord) => (chord ? t("{label} ({hint})", { label, hint: chordLabel(chord) }) : label);
  const recolor = (patch) => {
    runRecolor(context().editor.view, patch);
    setColorsOpen(false);
  };
  // One choice of the color row: a palette color (null: none) as the text's
  // color or as the tint behind it.
  const swatch = (c, background) => {
    const key = background ? "background" : "color";
    const value = c ? (background ? tint(c.color) : c.color) : null;
    const on = !!color.style && sameColor(color.style[key], value);
    const name = c && t(c.label);
    const label = background
      ? (c ? t("{color} background", { color: name }) : t("Default background"))
      : (c ? t("{color} text", { color: name }) : t("Default text color"));
    return (
      <button key={c?.name || "default"} type="button" tabIndex={-1} className={"ctlBtn" + (on ? " modeActive" : "")}
        title={label} aria-label={label} aria-pressed={on} onClick={() => recolor({ [key]: value })}>
        <span className="selToolsGlyph" style={value ? { [key]: value } : undefined} aria-hidden="true">A</span>
      </button>
    );
  };

  const tool = (id) => {
    if (id === COLOR) {
      return (
        <button key={id} type="button" tabIndex={-1} className={"ctlBtn selToolsColorBtn" + (colorsOpen ? " modeActive" : "")}
          title={t("Color")} aria-label={t("Color")} aria-expanded={colorsOpen} disabled={color.blocked}
          onClick={() => setColorsOpen(!colorsOpen)}>
          <span className="selToolsGlyph" style={{ color: color.style?.color, background: color.style?.background }} aria-hidden="true">A</span>
          <ChevronDownIcon size={12} aria-hidden="true" />
        </button>
      );
    }
    const cmd = commandById(id);
    const state = states.get(id);
    const Icon = commandIcon(cmd);
    return (
      <button key={id} type="button" tabIndex={-1} className={"ctlBtn" + (state === "on" ? " modeActive" : "")}
        data-command={id} title={title(cmd.label, commandChord(id, keybindings))} aria-label={cmd.label}
        aria-pressed={state === "on"} disabled={state === "blocked"} onClick={() => cmd.run(context())}>
        <Icon size={16} aria-hidden="true" />
      </button>
    );
  };

  return createPortal(
    <div ref={ref} className="selTools" role="toolbar" aria-label={t("Formatting")}
      style={pos ? { left: pos.left, top: pos.top } : { visibility: "hidden" }}
      onPointerDown={hold} onMouseDown={hold} onClick={(e) => e.stopPropagation()}>
      <div className="selToolsRow">
        {GROUPS.map((group, i) => (
          <React.Fragment key={i}>
            {i ? <span className="pdfInkSep" /> : null}
            {group.map(tool)}
          </React.Fragment>
        ))}
      </div>
      {colorsOpen ? (
        <div className="selToolsColors">
          <div className="selToolsColorRow" role="group" aria-label={t("Text color")}>
            {swatch(null, false)}
            {TEXT_COLORS.map((c) => swatch(c, false))}
          </div>
          <div className="selToolsColorRow" role="group" aria-label={t("Background color")}>
            {swatch(null, true)}
            {TEXT_COLORS.map((c) => swatch(c, true))}
          </div>
        </div>
      ) : null}
    </div>,
    document.body,
  );
}
