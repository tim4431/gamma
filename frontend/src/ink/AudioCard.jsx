import React, { useEffect, useRef, useState } from "react";
import { assetUrl } from "../shared/lib/utils.js";
import { t } from "../shared/i18n/i18n.js";
import { replayStore } from "./inkReplay.js";

export default function AudioCard({ block }) {
  const [enabled, setEnabled] = useState(true);
  const frame = useRef(0);
  const settings = useRef(null);
  const segments = block.properties.audio_segments || [];
  const events = block.properties.audio_events || [];
  settings.current = { enabled, events, segments };
  useEffect(() => () => { cancelAnimationFrame(frame.current); replayStore.clear(block.id); }, [block.id]);
  const update = (segment, audio) => {
    const current = settings.current;
    if (!current.events.length) { replayStore.clear(block.id); return; }
    if (current.enabled) replayStore.set({ audioId: block.id, segmentId: segment.id, segmentIds: current.segments.map((s) => s.id),
      ms: audio.currentTime * 1000, events: current.events });
  };
  const play = (segment, audio) => {
    if (!settings.current.events.length) replayStore.set(null);
    for (const other of document.querySelectorAll(".audioCard audio")) if (other !== audio) other.pause();
    cancelAnimationFrame(frame.current);
    const tick = () => {
      update(segment, audio);
      if (!audio.paused && !audio.ended) frame.current = requestAnimationFrame(tick);
    };
    tick();
  };
  return <div className="audioCard" onPointerDown={(e) => e.stopPropagation()}>
    {events.length > 0 && <label><input type="checkbox" checked={enabled} onChange={(e) => {
      setEnabled(e.target.checked); if (!e.target.checked) replayStore.clear(block.id);
    }} /> {t("Replay handwriting")}</label>}
    {segments.map((segment, i) => <audio key={segment.id} controls preload="metadata" src={assetUrl(segment.url)}
      aria-label={t("Recording {n}", { n: i + 1 })}
      onPlay={(e) => play(segment, e.currentTarget)} onPause={() => cancelAnimationFrame(frame.current)}
      onTimeUpdate={(e) => update(segment, e.currentTarget)} onSeeked={(e) => update(segment, e.currentTarget)}
      onEnded={() => replayStore.clear(block.id)} />)}
  </div>;
}
