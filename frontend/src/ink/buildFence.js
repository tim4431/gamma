// The build fence (docs/dev/handwriting.md "Two writers, one group" and
// "Client"): a page whose bundle is older than the server it talks to
// stops starting strokes and cuts, behind a pill that asks for a reload.
// App composes it: the session's build comes in as `sessionBuild`, and
// every socket hello's reaches `noteServerBuild` through usePageCollab's
// onBuild.
import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";

// sessionBuild: the server's build as /api/session reported it (App's
// authUser.build). setStatus: App's status pill.
export function useBuildFence({ sessionBuild, setStatus }) {
  // The build fence ("Two writers, one group", "Fences"): the server's build
  // as this page loaded (the session's) against every socket hello; once
  // they differ the server was updated under this bundle, and the page
  // stops starting strokes and cuts until it is reloaded. Reads keep working.
  const inkBuildRef = useRef(null);     // the names of the build seen first, or null
  const inkStaleRef = useRef(false);
  const [inkStale, setInkStale] = useState(false);
  const noteServerBuild = useCallback((build) => {
    // A build is named by its commit, its label or its version (the
    // session's `build` carries all three, a socket's hello any of them):
    // the same build shares at least one name with the one seen first.
    const names = typeof build === "string" ? [build] : [build?.commit, build?.label, build?.version].filter((v) => typeof v === "string" && v);
    if (!names.length) return;
    if (!inkBuildRef.current) { inkBuildRef.current = new Set(names); return; }
    if (inkStaleRef.current || names.some((n) => inkBuildRef.current.has(n))) return;
    inkStaleRef.current = true;
    setInkStale(true);
    setStatus(t("Gamma was updated, reload to keep drawing"), { label: t("Reload"), run: () => window.location.reload() }, { sticky: true });
  }, [setStatus]);
  useEffect(() => { noteServerBuild(sessionBuild); }, [sessionBuild, noteServerBuild]);
  return { inkStale, noteServerBuild };
}
