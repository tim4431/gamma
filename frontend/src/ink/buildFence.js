// The build fence (docs/dev/handwriting.md "Two writers, one group" and
// "Client"): a page whose bundle is older than the server it talks to
// stops starting strokes and cuts, behind a pill that asks for a reload.
// App composes it: the session's build comes in as `sessionBuild`, and
// every socket hello's reaches `noteServerBuild` through usePageCollab's
// onBuild. The comparison is pure (buildTrips, below;
// frontend/tests/buildFence.test.mjs).
import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";

// A build's names. A build is named by its commit, its label or its
// version (the session's `build` carries all three, a socket's hello any
// of them): those that are non-empty strings; a bare string is one name;
// anything else has none.
export function buildNames(build) {
  return typeof build === "string" ? [build] : [build?.commit, build?.label, build?.version].filter((v) => typeof v === "string" && v);
}
// Whether `names` name the build seen first (`seen`, a Set of its names):
// the same build shares at least one name with it.
export const sameBuild = (seen, names) => names.some((n) => seen.has(n));
// A build's news to the fence, {seen: the names of the build seen first,
// or null; stale: tripped}, which it updates: the first build with names
// is kept, and the first later one sharing none of them trips the fence.
// → whether this one tripped it; a build without names, the same build or
// a second trip does nothing.
export function buildTrips(fence, build) {
  const names = buildNames(build);
  if (!names.length) return false;
  if (!fence.seen) { fence.seen = new Set(names); return false; }
  if (fence.stale || sameBuild(fence.seen, names)) return false;
  fence.stale = true;
  return true;
}

// sessionBuild: the server's build as /api/session reported it (App's
// authUser.build). setStatus: App's status pill.
export function useBuildFence({ sessionBuild, setStatus }) {
  // The build fence ("Two writers, one group", "Fences"): the server's build
  // as this page loaded (the session's) against every socket hello; once
  // they differ the server was updated under this bundle, and the page
  // stops starting strokes and cuts until it is reloaded. Reads keep working.
  const fenceRef = useRef({ seen: null, stale: false });   // the fence's state (buildTrips)
  const [inkStale, setInkStale] = useState(false);
  const noteServerBuild = useCallback((build) => {
    if (!buildTrips(fenceRef.current, build)) return;
    setInkStale(true);
    setStatus(t("Gamma was updated, reload to keep drawing"), { label: t("Reload"), run: () => window.location.reload() }, { sticky: true });
  }, [setStatus]);
  useEffect(() => { noteServerBuild(sessionBuild); }, [sessionBuild, noteServerBuild]);
  return { inkStale, noteServerBuild };
}
