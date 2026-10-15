// A recording's player (docs/dev/recording.md "Playback"), docked at the
// bottom of the viewer (or of the notes, when no viewer holds it) while a
// recording plays through replay/playback.js: play and pause, ten seconds
// either way, the time, a scrubber with marks where something was written,
// skips between those marks, then one button each for how the unreached
// strokes are drawn and for the speed (a menu apiece), Follow, and Done.
// Every control is one icon button of the same height; the scrubber takes
// the width that is left. Keys while the bar is open and no editor has the
// focus: Space, ← →, Escape.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InkTooltips, useInkVersion } from "../ink/InkLayer";
import * as inkStore from "../ink/inkStore";
import { fmtClock, nextTick, writingTicks } from "../replay/clock.js";
import * as playback from "../replay/playback.js";
import { MODES } from "../replay/playback.js";
import { useReplay } from "../replay/useFrame.js";
import { REPLAY_RATES } from "../app/prefDefs.js";
import { ContextMenu, MenuItem } from "../shared/ui/Menus";
import {
  Back10Icon, CheckIcon, ContrastIcon, EyeIcon, Forward10Icon, LocateIcon, PauseIcon, PenIcon, PlayIcon, SkipBackIcon,
  SkipForwardIcon, XIcon,
} from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";
import "./recording.css";

// The three ways to draw handwriting the playhead has not reached, each
// with its glyph: faded, written as it was, or left as it is.
const MODE_ICONS = { spotlight: ContrastIcon, reveal: PenIcon, static: EyeIcon };
const MODE_LABELS = { spotlight: T("Spotlight"), reveal: T("Reveal"), static: T("Static") };
const MODE_TITLES = {
  spotlight: T("Spotlight: handwriting not yet reached is faded"),
  reveal: T("Reveal: handwriting appears as it was written"),
  static: T("Static: the page stays as it is; tapping still plays from that moment"),
};
const SKIP_MS = 10_000;
// A press on a control never takes the focus from an open editor (a note's
// or a text box's), as the ink strip's buttons never do: the editor would
// close and the row redraw under the reader's eyes. The scrubber keeps its
// own focus for the keyboard.
const keepFocus = (e) => e.preventDefault();

// Where the keyboard's keys should be left alone: someone is typing.
const typing = (el) => !!el?.closest?.("input, textarea, select, [contenteditable=''], [contenteditable='true'], .cm-editor");

// `block`: the recording block playing; `inkBlocks` and `blocks` the
// page's handwriting groups and its blocks ([{id, t0}]) for the marks;
// `prefs` the display choices; `onShow(tick)` scrolls to what a mark is.
export function PlayerBar({ block, inkBlocks, blocks, prefs, onShow }) {
  const state = useReplay();
  useInkVersion();
  const [menu, setMenu] = useState(null); // {kind: "mode" | "rate", x, y}
  const triggerRef = useRef(null);         // the open menu's button: a press on it closes, never reopens
  const closeMenu = useCallback(() => setMenu(null), []);
  const clock = state?.scope.recording ? state.clock : null;
  const groups = useMemo(() => (inkBlocks || []).map((b) => ({ id: b.id, ink: inkStore.inkFor(b) })).filter((g) => g.ink),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [inkBlocks, inkStore.currentVersion()]);
  const ticks = useMemo(() => (clock ? writingTicks(clock, groups, blocks || []) : []), [clock, groups, blocks]);

  // Follow the writing: the latest mark reached names what to show; one
  // scroll per handwriting group or note, never per stroke.
  const shownRef = useRef(null);
  const t0 = state?.t;
  useEffect(() => {
    if (!clock || !prefs.follow || !onShow) return;
    let latest = null;
    for (const k of ticks) { if (k.t <= t0) latest = k; else break; }
    const key = latest ? `${latest.kind}:${latest.group || latest.id}` : null;
    if (key && key !== shownRef.current) { shownRef.current = key; onShow(latest); }
  }, [clock, ticks, t0, prefs.follow, onShow]);
  useEffect(() => { shownRef.current = null; }, [block?.id]);

  // Keys, while the bar is open.
  useEffect(() => {
    if (!clock) return undefined;
    const onKey = (e) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      if (e.key === " ") { e.preventDefault(); playback.toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); playback.seek(playback.now().t - SKIP_MS); }
      else if (e.key === "ArrowRight") { e.preventDefault(); playback.seek(playback.now().t + SKIP_MS); }
      else if (e.key === "Escape" && !menu) { e.preventDefault(); playback.stop(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clock, menu]);

  if (!clock) return null;
  const { t: at, playing, rate, mode } = state;
  const duration = clock.duration;
  const skip = (dir) => {
    const tick = nextTick(ticks, at, dir);
    if (tick) playback.seek(Math.max(0, tick.t - state.leadIn));
    else playback.seek(dir > 0 ? duration : 0);
  };
  const openMenu = (kind) => (e) => {
    if (menu?.kind === kind) { setMenu(null); return; }
    const r = e.currentTarget.getBoundingClientRect();
    triggerRef.current = e.currentTarget;
    setMenu({ kind, x: r.left, y: r.top - 4 });
  };
  const ModeIcon = MODE_ICONS[mode] || ContrastIcon;
  const joins = clock.segments.slice(1).map((seg, i) => {
    const before = clock.segments[i];
    return { key: seg.url + i, left: (seg.start / duration) * 100, pause: seg.started > before.started + before.dur + 1000 };
  });
  return (
    <InkTooltips className="replayBar" role="toolbar" aria-label={t("Recording player")} data-guide="recording.player">
      <div className="replayRow">
        <button type="button" onMouseDown={keepFocus} className="ctlBtn replayPlay" onClick={() => playback.toggle()} aria-pressed={playing}
          aria-label={playing ? t("Pause (Space)") : t("Play (Space)")}>
          {playing ? <PauseIcon size={16} /> : <PlayIcon size={16} />}
        </button>
        <button type="button" onMouseDown={keepFocus} className="ctlBtn" onClick={() => playback.seek(at - SKIP_MS)} aria-label={t("Back 10 seconds (←)")}>
          <Back10Icon size={16} />
        </button>
        <button type="button" onMouseDown={keepFocus} className="ctlBtn" onClick={() => playback.seek(at + SKIP_MS)} aria-label={t("Forward 10 seconds (→)")}>
          <Forward10Icon size={16} />
        </button>
        <span className="replayTime">{fmtClock(at)}</span>
        <div className="replayScrub">
          <div className="replayTrack" aria-hidden="true">
            <div className="replayFill" style={{ width: `${duration ? (at / duration) * 100 : 0}%` }} />
            {joins.map((j) => <span key={j.key} className={"replayJoin" + (j.pause ? " pause" : "")} style={{ left: `${j.left}%` }} />)}
            {ticks.map((k) => (
              <span key={`${k.kind}-${k.id}`} className={`replayTick ${k.kind}`} style={{ left: `${(k.t / duration) * 100}%` }} />
            ))}
          </div>
          <input type="range" min={0} max={duration} step={100} value={Math.round(at)} aria-label={t("Position in the recording")}
            aria-valuetext={`${fmtClock(at)} / ${fmtClock(duration)}`} onChange={(e) => playback.seek(Number(e.target.value))} />
        </div>
        <span className="replayTime">{fmtClock(duration)}</span>
        <button type="button" onMouseDown={keepFocus} className="ctlBtn" onClick={() => skip(-1)} disabled={!ticks.length}
          aria-label={t("Previous writing: skip back to the moment before something was written")}>
          <SkipBackIcon size={15} />
        </button>
        <button type="button" onMouseDown={keepFocus} className="ctlBtn" onClick={() => skip(1)} disabled={!ticks.length}
          aria-label={t("Next writing: skip the silence to the next thing written")}>
          <SkipForwardIcon size={15} />
        </button>
        <span className="replaySep" aria-hidden="true" />
        <button type="button" onMouseDown={keepFocus} className={"ctlBtn" + (menu?.kind === "mode" ? " modeActive" : "")} onClick={openMenu("mode")}
          aria-haspopup="menu" aria-expanded={menu?.kind === "mode"} aria-label={t(MODE_TITLES[mode])} data-role="mode">
          <ModeIcon size={16} />
        </button>
        <button type="button" onMouseDown={keepFocus} className={"ctlBtn replayRate" + (menu?.kind === "rate" ? " modeActive" : "")} onClick={openMenu("rate")}
          aria-haspopup="menu" aria-expanded={menu?.kind === "rate"} aria-label={t("Speed {rate}×", { rate })}>
          {rate}×
        </button>
        <button type="button" onMouseDown={keepFocus} className={"ctlBtn" + (prefs.follow ? " modeActive" : "")} aria-pressed={prefs.follow}
          onClick={() => prefs.setFollow(!prefs.follow)}
          aria-label={prefs.follow ? t("Following the writing: the page and the notes scroll to what is being written") : t("Follow the writing")}>
          <LocateIcon size={16} />
        </button>
        <button type="button" onMouseDown={keepFocus} className="ctlBtn replayClose" onClick={() => playback.stop()} aria-label={t("Done (Esc)")}>
          <XIcon size={16} />
        </button>
      </div>
      {menu ? (
        <PlayerMenu menu={menu} mode={mode} rate={rate} triggerRef={triggerRef} onClose={closeMenu}
          onMode={(m) => { playback.setMode(m); prefs.setMode(m); setMenu(null); }}
          onRate={(r) => { playback.setRate(r); prefs.setRate(r); setMenu(null); }} />
      ) : null}
    </InkTooltips>
  );
}

// The mode or speed menu, apart from the bar so the bar's re-render on
// every frame of the replay leaves the open menu as it is.
const PlayerMenu = React.memo(function PlayerMenu({ menu, mode, rate, triggerRef, onClose, onMode, onRate }) {
  return (
    <ContextMenu x={menu.x} y={menu.y} anchorBottom ignoreRef={triggerRef} onClose={onClose}>
      {menu.kind === "mode" ? MODES.map((m) => {
        const Icon = MODE_ICONS[m];
        return (
          <MenuItem key={m} icon={Icon} title={t(MODE_TITLES[m])} trailing={mode === m ? <CheckIcon size={14} /> : null}
            aria-checked={mode === m} onMouseDown={keepFocus} onClick={() => onMode(m)}>
            {t(MODE_LABELS[m])}
          </MenuItem>
        );
      }) : REPLAY_RATES.map((r) => (
        <MenuItem key={r} trailing={rate === r ? <CheckIcon size={14} /> : null} aria-checked={rate === r} onMouseDown={keepFocus}
          onClick={() => onRate(r)}>
          {r}×
        </MenuItem>
      ))}
    </ContextMenu>
  );
}, (a, b) => a.menu === b.menu && a.mode === b.mode && a.rate === b.rate);
