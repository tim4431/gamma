// The one report dialog (reportContent.js): App mounts this host once, and
// it opens the dialog, loaded on first use, while a surface's Report has a
// target in the store.
import React, { useSyncExternalStore } from "react";
import { LazyBoundary, lazySurface } from "../shared/ui/lazy";
import { closeReport, reportTarget, subscribeReport } from "./reportContent.js";

const ReportContentDialog = lazySurface(() => import("./ReportContentDialog"));

export default function ReportContentHost({ setStatus }) {
  const target = useSyncExternalStore(subscribeReport, reportTarget);
  if (!target) return null;
  return (
    <LazyBoundary>
      <ReportContentDialog target={target} onClose={closeReport} setStatus={setStatus} />
    </LazyBoundary>
  );
}
