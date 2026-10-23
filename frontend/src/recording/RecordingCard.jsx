// A recording block's row in the notes (docs/dev/recording.md): under the
// title, its card — while it is being recorded, the state and the time so
// far; afterwards, the play button, its length and date, and a menu with
// Transcribe and Download. The title is the block's content, edited like
// any note's text.
import React, { useState, useSyncExternalStore } from "react";
import { ContextMenu, MenuButton, MenuItem } from "../shared/ui/Menus";
import { DownloadIcon, PauseIcon, PlayIcon, SparklesIcon } from "../shared/ui/Icons";
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { assetUrl } from "../shared/lib/utils";
import { CLIENT_ID } from "../collaboration/usePageCollab.js";
import { fmtClock } from "../replay/clock.js";
import * as playback from "../replay/playback.js";
import { useOwnReplay } from "../replay/useFrame.js";
import * as recorderStore from "./recorderStore.js";
import { useRecordings } from "./RecordingContext.js";
import { heldElsewhere, normalizeRecording } from "./recording.js";
import "./recording.css";

// This tab's recording of `blockId`: its status and elapsed seconds, else null.
function useLiveRecording(blockId) {
  return useSyncExternalStore(recorderStore.subscribe, () => {
    const s = recorderStore.now();
    if (!s || s.blockId !== blockId) return null;
    return `${s.status}:${Math.floor(s.elapsed / 1000)}`;
  });
}

export function RecordingCard({ block, editable }) {
  const ctx = useRecordings();
  const rec = normalizeRecording(block.properties.recording);
  const live = useLiveRecording(block.id);
  const replay = useOwnReplay(block.id);
  const [menu, setMenu] = useState(null);

  if (live) {
    const [status, secs] = live.split(":");
    return (
      <div className="recordingCard live" data-guide="recording.card">
        <span className={"recDot" + (status === "recording" ? " live" : "")} aria-hidden="true" />
        <span className="recMeta">
          {status === "paused" ? t("Paused") : status === "finishing" ? t("Saving…") : t("Recording…")} · {fmtClock(Number(secs) * 1000)}
        </span>
      </div>
    );
  }
  if (heldElsewhere(rec, CLIENT_ID)) {
    return (
      <div className="recordingCard live" data-guide="recording.card">
        <span className="recDot live" aria-hidden="true" />
        <span className="recMeta">{t("Recording on another device")}{rec.dur ? ` · ${fmtClock(rec.dur)}` : ""}</span>
      </div>
    );
  }
  const n = rec.segments.length;
  const playing = !!replay?.playing;
  const when = rec.started ? fmtDate(new Date(rec.started), { dateStyle: "medium", timeStyle: "short" }) : "";
  const download = () => {
    rec.segments.forEach((seg, i) => {
      const a = document.createElement("a");
      a.href = assetUrl(seg.url);
      a.download = `${(block.content || t("Recording")).replace(/[\\/:*?"<>|]+/g, "_")}${n > 1 ? `-${i + 1}` : ""}${seg.url.slice(seg.url.lastIndexOf("."))}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    });
  };
  return (
    <div className={"recordingCard" + (replay ? " playing" : "")} data-guide="recording.card">
      {n ? (
        <button type="button" onMouseDown={(e) => e.preventDefault()} className={"ctlBtn recPlay" + (replay ? " modeActive" : "")} aria-pressed={!!replay}
          onClick={(e) => { e.stopPropagation(); if (replay) playback.toggle(); else ctx?.play(block.id); }}
          title={playing ? t("Pause") : replay ? t("Play") : t("Play the recording: the notes and handwriting follow the audio")}
          aria-label={playing ? t("Pause") : t("Play recording")}>
          {playing ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
        </button>
      ) : null}
      <span className="recMeta">
        {n ? fmtClock(rec.dur) : t("No audio was saved")}
        {when ? ` · ${when}` : ""}
        {n > 1 ? ` · ${tn("{n} part", "{n} parts", n)}` : ""}
        {rec.state !== "done" && n ? ` · ${t("interrupted")}` : ""}
      </span>
      {n ? <MenuButton label={t("Recording actions")} className="recMenuBtn" open={(e) => setMenu({ x: e.clientX, y: e.clientY })} /> : null}
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} aria-label={t("Recording actions")}>
          {editable && ctx?.transcribe ? (
            <MenuItem icon={SparklesIcon} onClick={() => { setMenu(null); ctx.transcribe(block.id); }}
              title={t("Write the spoken words under the recording, each paragraph linked to its moment")}>
              {t("Transcribe")}
            </MenuItem>
          ) : null}
          <MenuItem icon={DownloadIcon} onClick={() => { setMenu(null); download(); }}>
            {n > 1 ? t("Download audio ({n} files)", { n }) : t("Download audio")}
          </MenuItem>
        </ContextMenu>
      ) : null}
    </div>
  );
}
