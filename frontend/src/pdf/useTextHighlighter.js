// The text highlighters (docs/dev/settings.md "Text highlighters"): the
// account's list (`highlightPresets`, shared/model/markStyle.js; the seed
// while none is stored), which one is armed this session and the strip
// button whose menu is open (highlighterState.js), the edits with what the
// pill says of them, and the keys while one is armed. App calls it once and
// hands its commands to the strip (HighlighterStrip.jsx), the selection
// popup and the menus (MarkToolsContext, `markTools`) and Settings.
//
// Inputs: `stored` / `setStored` the preference; `seedColor` the released
// `highlighterColor` (only the seed reads it); `palette` / `setPalette`;
// `canHighlight` (a PDF on screen the reader may highlight: off, nothing is
// armed); `inkOpen` (the pen's strip, whose 1–9 are its presets');
// `onArm()` (arming puts the pen's strip away); `openSettings(pane)`;
// `setStatus(msg, action)`.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  MAX_SHOWN_PRESETS, addPreset, duplicatePreset, effectivePresets, hiddenPresets, movePreset, normalizePresets, presetKey, presetKeys,
  presetName, removePreset, restorePreset, selectionTint, setPresetHidden, shownPresets, swapIntoStrip,
} from "../shared/model/markStyle.js";
import { pickerColors } from "../shared/model/palette.js";
import { isTextField } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { HL_IDLE, armedTo, disarmed, highlighterKey, menuClosed, menuOpened, nextArmed, toggled } from "./highlighterState.js";

export function useTextHighlighter({ stored, setStored, seedColor, palette, setPalette, canHighlight, inkOpen, onArm, openSettings, setStatus }) {
  const [state, setState] = useState(HL_IDLE);
  const firstSwatch = pickerColors(palette, "highlight")[0];
  const list = useMemo(() => effectivePresets(stored, seedColor, firstSwatch), [stored, seedColor, firstSwatch]);
  // The commands read the latest of these, so they keep one identity.
  const live = useRef(null);
  live.current = { list, state, onArm, openSettings, setStatus, setStored, palette, setPalette };

  // The only writer: an updater over the list in effect (the seed until the
  // first edit stores it), through the normalizer.
  const write = useCallback((next) => {
    const out = normalizePresets(next);
    if (out) live.current.setStored(out);
  }, []);
  const cmds = useMemo(() => {
    const L = () => live.current;
    const name = (id, list = L().list) => {
      const p = list.find((x) => x.id === id);
      return p ? presetName(p) : "";
    };
    const say = (msg, action) => L().setStatus(msg, action);
    const arm = (id) => {
      if (id) L().onArm?.();
      setState((s) => armedTo(s, id));
    };
    const show = (id) => {
      const list = L().list;
      if (!list.some((x) => x.id === id)) return;
      const next = setPresetHidden(list, id, false);
      if (next) write(next);
      else say(t("Up to {n} on the strip", { n: MAX_SHOWN_PRESETS }));
    };
    const c = {
      arm,
      off: () => setState(disarmed),
      // a press on a strip button: on, or off when it is the armed one
      toggle: (id) => {
        const s = L().state;
        const next = toggled(s, id, L().list);
        if (next.armed && next.armed !== s.armed) L().onArm?.();
        setState(next);
        return next.armed;
      },
      next: () => {
        const next = nextArmed(L().state, L().list);
        if (next.armed) L().onArm?.();
        setState(next);
        return next.armed;
      },
      openMenu: (id, at) => setState((s) => menuOpened(s, id, at)),
      closeMenu: () => setState(menuClosed),
      setPresets: (fn) => write(typeof fn === "function" ? fn(L().list) : fn),
      edit: (id, patch) => write(L().list.map((p) => (p.id === id ? { ...p, ...patch } : p))),
      duplicate: (id) => {
        const r = duplicatePreset(L().list, id);
        if (!r) { say(t("Up to {n} highlighters", { n: 12 })); return null; }
        write(r.list);
        arm(r.id);
        // the menu follows the copy, open for its edits
        setState((s) => (s.menu ? { ...s, menu: { ...s.menu, id: r.id } } : s));
        if (r.hidden) say(t("{name} added under Hidden highlighters.", { name: name(r.id, r.list) }), { label: t("Put it on the strip"), run: () => c.swapIn(r.id) });
        return r.id;
      },
      hide: (id) => {
        const next = setPresetHidden(L().list, id, true);
        if (!next) return;
        const n = name(id);
        write(next);
        say(t("{name} hidden.", { name: n }), { label: t("Show"), run: () => show(id) });
      },
      show,
      swapIn: (id) => {
        const r = swapIntoStrip(L().list, id);
        write(r.list);
        if (r.out) say(t("{name} moved under Hidden highlighters.", { name: presetName(r.out) }));
      },
      remove: (id) => {
        const r = removePreset(L().list, id);
        if (!r) return;
        write(r.list);
        setState((s) => (s.armed === id ? disarmed(menuClosed(s)) : menuClosed(s)));
        say(t("{name} removed.", { name: presetName(r.item) }), { label: t("Undo"), run: () => write(restorePreset(L().list, r.item, r.index)) });
      },
      move: (id, by) => {
        const next = movePreset(L().list, id, by);
        if (next === L().list) return;
        write(next);
        const k = presetKey(next, id);
        if (k) say(t("{name} is now key {n}.", { name: name(id, next), n: k }));
      },
      // a highlight's look kept as a highlighter (a highlight's menu, the made bar)
      saveLook: (look) => {
        const r = addPreset(L().list, look);
        if (!r) { say(t("Up to {n} highlighters", { n: 12 })); return; }
        if (r.existed) { say(t("{name} is already one of your highlighters.", { name: name(r.id) })); return; }
        write(r.list);
        const n = name(r.id, r.list);
        if (r.hidden) say(t("{name} added under Hidden highlighters.", { name: n }), { label: t("Put it on the strip"), run: () => c.swapIn(r.id) });
        else say(t("{name} added to the strip.", { name: n }));
      },
      setGroup: (id) => {
        const p = L().palette;
        L().setPalette({ ...p, pickers: { ...p.pickers, highlight: id } });
      },
      openHighlighters: () => L().openSettings?.("highlighters"),
    };
    return c;
  }, [write]);

  // Armed only where a highlight can be made; one removed elsewhere is off.
  const armedPreset = canHighlight && state.armed ? list.find((p) => p.id === state.armed) || null : null;
  const armedId = armedPreset?.id || null;

  // The keys while one is armed (highlighterState.js highlighterKey): 1–9 arm
  // the highlighter at that key, its own key or Escape turns it off. After
  // an open menu's and the popup's own (they handle theirs first, in the
  // capture phase), and never in a field, a dialog or the pen's strip.
  useEffect(() => {
    if (!armedId) return undefined;
    const onKey = (e) => {
      if (e.defaultPrevented) return;
      const L = live.current;
      const act = highlighterKey(e, {
        list: L.list, armed: armedId, inField: isTextField(document.activeElement),
        // any card or menu over the page takes its own keys (Escape closes it)
        dialog: !!document.querySelector('.reportOverlay, .ctxMenu, .popover, .hlReviewCard, [role="dialog"], [role="menu"]'), inkOpen,
      });
      if (!act) return;
      e.preventDefault();
      if (act.kind === "off") cmds.off();
      else cmds.arm(act.id);
      const p = L.list.find((x) => x.id === (act.kind === "off" ? null : act.id));
      L.setStatus(p ? t("{name} on.", { name: presetName(p) }) : t("Text highlighter off."));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [armedId, inkOpen, cmds]);

  const shown = useMemo(() => shownPresets(list), [list]);
  const hidden = useMemo(() => hiddenPresets(list), [list]);
  const keyIds = useMemo(() => presetKeys(list), [list]);
  const markTools = useMemo(() => ({
    presets: list, shown, hidden, keyIds, armedId,
    saveLook: cmds.saveLook, setGroup: cmds.setGroup, openHighlighters: cmds.openHighlighters,
    keys: canHighlight && !inkOpen,
  }), [list, shown, hidden, keyIds, armedId, cmds, canHighlight, inkOpen]);

  return {
    ...cmds, list, shown, hidden, keyIds, state, menu: state.menu, armedId, armedPreset,
    on: !!armedPreset, tint: selectionTint(armedPreset), markTools, firstSwatch,
  };
}
