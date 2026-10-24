// The recording in progress (recording/recorderStore.js), floating over
// the page: a red dot, the time recorded, the input level, Pause, Bookmark
// and Stop. It re-renders for every level reading on its own; App does not.
import React, { useSyncExternalStore } from "react";
import { BookmarkIcon, PauseIcon, PlayIcon, StopIcon } from "../shared/ui/Icons";
import { IconSwap } from "../shared/ui/MotionWidgets";
import { t } from "../shared/i18n/i18n.js";
import { fmtClock } from "../replay/clock.js";
import * as recorderStore from "./recorderStore.js";
import "./recording.css";

export function RecordingPill() {
  const state = useSyncExternalStore(recorderStore.subscribe, recorderStore.now);
  if (!state) return null;
  const { status, elapsed, level } = state;
  const paused = status === "paused";
  return (
    <div className={"recordingPill" + (status === "recording" ? " live" : "")} role="status" aria-live="off"
      aria-label={t("Recording")} data-guide="recording.pill">
      <span className="recDot" aria-hidden="true" />
      {status === "asking" ? (
        <span className="recText">{t("Waiting for the microphone…")}</span>
      ) : status === "finishing" ? (
        <><span className="pillSpin" aria-hidden="true" /><span className="recText">{t("Saving the recording…")}</span></>
      ) : (
        <>
          <span className="recTime">{fmtClock(elapsed)}</span>
          <span className="recLevel" aria-hidden="true"><span style={{ transform: `scaleX(${paused ? 0 : level})` }} /></span>
          {paused ? <span className="recText">{t("Paused")}</span> : null}
          <button type="button" onMouseDown={(e) => e.preventDefault()} className="ctlBtn" onClick={() => recorderStore.toggle()}
            title={paused ? t("Resume recording") : t("Pause recording")} aria-label={paused ? t("Resume recording") : t("Pause recording")}>
            <IconSwap swap={paused} from={<PauseIcon size={14} />} to={<PlayIcon size={14} />} />
          </button>
          <button type="button" onMouseDown={(e) => e.preventDefault()} className="ctlBtn" onClick={() => recorderStore.bookmark()}
            title={t("Bookmark this moment: a note stamped a few seconds back")} aria-label={t("Bookmark this moment")}>
            <BookmarkIcon size={16} />
          </button>
          <button type="button" onMouseDown={(e) => e.preventDefault()} className="ctlBtn recStop" onClick={() => recorderStore.stop()}
            title={t("Stop recording")} aria-label={t("Stop recording")}>
            <StopIcon size={14} />
          </button>
        </>
      )}
    </div>
  );
}
