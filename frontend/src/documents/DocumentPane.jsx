// The document pane (docs/dev/documents.md "Which viewer a page shows"):
// draws the viewer the view names (documentView.js). The PDF and the
// notebook are the host's render functions, which need its state; every
// other pane is a viewer of this module's own, drawn from one `surface`
// object the host builds (App's, memoised; PagePane's read-only), in a
// chunk fetched on first use: the text viewer with the CodeMirror packages
// (TextViewer.jsx), and the picture, the player and the file pane together
// (FileViews.jsx). A viewer a host neither renders nor gives a surface is
// the fallback.
import React from "react";
import { LazyBoundary, lazySurface } from "../shared/ui/lazy";
import { viewerOf } from "./viewers.js";

const TextViewer = lazySurface(() => import("./viewers/TextViewer.jsx"));
const ImageViewer = lazySurface(() => import("./viewers/FileViews.jsx"), (m) => m.ImageViewer);
const MediaViewer = lazySurface(() => import("./viewers/FileViews.jsx"), (m) => m.MediaViewer);
const FilePane = lazySurface(() => import("./viewers/FileViews.jsx"), (m) => m.FilePane);
const DEFAULT = { text: TextViewer, image: ImageViewer, media: MediaViewer, file: FilePane };

// Fetches the chunk of the pane a document of `format` gets, as a page of
// it starts to open (docs/dev/frontend-refactor.md: no click waits on the
// network for a chunk).
export const preloadViewer = (format, doc = null) => DEFAULT[viewerOf(format, doc)]?.preload();

// The surface: { doc, fileUrl, pageId, readOnly, preview, hidden, phone,
// scale: {value, onZoomTo, onEffectiveScale}, place, onPlace, reportRef,
// mark, onMarkDone, regions, anchors, flashId, regionMode, onRegion, onRegionJump,
// onRegionContext, onRegionThread, commentMargin, marginRef, onMarginLost,
// scrollRef, captureRef, onAskRegion, onReveal, onStatus } — each viewer
// takes what it uses (docs/dev/documents.md "Viewers").
export default function DocumentPane({ view, render, fallback = null, surface = null }) {
  const host = view.viewer ? render?.[view.viewer] : null;
  if (host) return host();
  const Viewer = view.viewer ? DEFAULT[view.viewer] : null;
  if (!Viewer || !surface) return fallback;
  return (
    <LazyBoundary fallback={<div className="docViewSkeleton" aria-busy="true" />}>
      <Viewer {...surface} />
    </LazyBoundary>
  );
}
