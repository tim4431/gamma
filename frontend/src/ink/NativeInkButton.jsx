import React, { useEffect, useRef, useState } from "react";
import { API, apiJson, makeId } from "../shared/lib/utils";
import { PenIcon } from "../shared/ui/Icons";
import { newInk } from "./ink";
import * as inkStore from "./inkStore";
import { assertSameContext, hasNativeInk, presentNativeInk } from "./nativeInk";

export function NativeInkButton({ context, snapshotRef, selection, blocks, hasPending, onSaved, onStatus }) {
  const current = useRef(context);
  current.current = context;
  const [busy, setBusy] = useState(false);
  useEffect(() => { current.current = context; return () => { current.current = null; }; }, []);
  if (!hasNativeInk() || context.readOnly || !context.user || !context.workspace) return null;
  const open = async () => {
    if (busy) return;
    setBusy(true);
    const scope = { ...current.current };
    try {
      if (inkStore.dirtyDrafts().length || hasPending()) throw new Error("Wait for your current edits to save, then try again.");
      if (selection?.items?.length > 1) throw new Error("Select one ink group to edit, or clear the selection to start a new group.");
      const selected = selection?.items?.length === 1 ? blocks.find((b) => b.id === selection.items[0].id) : null;
      const shot = await snapshotRef.current?.(selected?.properties.pdf_page);
      if (!shot) throw new Error("The PDF page is still loading.");
      assertSameContext(scope, current.current);
      const target = selected ? await apiJson(`${API}/blocks/${selected.id}`) : null;
      const ink = target ? await inkStore.loadInk(target.properties.ink_url) : newInk(shot.page, shot.width, shot.height);
      if (!ink) throw new Error("Could not load this handwriting. Try again when connected.");
      if (["page", "width", "height"].some((k) => ink.space[k] !== shot[k])) {
        throw new Error("The PDF page dimensions changed. Reopen the document before editing this group.");
      }
      const background = [];
      for (const block of blocks.filter((b) => b.id !== target?.id && b.properties.pdf_page === shot.page)) {
        const other = inkStore.inkFor(block) || await inkStore.loadInk(block.properties.ink_url);
        if (!other) throw new Error("Could not load the other handwriting on this page.");
        background.push(other);
      }
      assertSameContext(scope, current.current);
      const request = { requestId: makeId(), user: scope.user, workspace: scope.workspace, pageId: scope.pageId,
        document: scope.pdfUrl, blockId: target?.id || makeId(), parentId: target?.parent_id || scope.pageId,
        expectedURL: target?.properties.ink_url ?? null, existing: !!target, ink, background, image: shot.image };
      await presentNativeInk(request, async (result, changed) => {
        assertSameContext(scope, current.current);
        const abort = new AbortController();
        const timeout = setTimeout(() => abort.abort(), 30000);
        try { await apiJson(`${API}/blocks/${result.blockId}/ink`, { method: "PUT", signal: abort.signal,
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({
            parent_id: request.parentId, expected_url: result.expectedURL, ink: changed,
          }) }); } finally { clearTimeout(timeout); }
        // The socket carries the committed op. Reload only when the same
        // document is still active; do not make an acknowledged save retry
        // merely because refreshing the tree fails.
        if (current.current?.pageId === scope.pageId) Promise.resolve(onSaved()).catch(() => {});
        onStatus("Handwriting saved.");
      });
    } catch (error) { onStatus(error.message || String(error)); }
    finally { setBusy(false); }
  };
  return <button data-native-ink onClick={open} disabled={busy} title="Write with Apple Pencil; select one ink group to edit it" aria-label="Write with PencilKit">
    <PenIcon size={15} /><span style={{ fontSize: 10 }}>iPad</span>
  </button>;
}
