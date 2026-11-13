// The Files window's reads and the effects that need App's later values
// (docs/dev/work_folders.md "The Files window"): the open directories'
// rows (useDirectoryRows.js), the window's first slot, and the reveal of
// the open page. The window's own state is useFilesWindow.js's (`files`).
import { useEffect, useRef, useState } from "react";
import { folderChain } from "../library/libraryUtils.js";
import { useDirectoryRows } from "./useDirectoryRows.js";
import { workOf } from "./workFolders.js";
import { revealPath } from "./workPaths.js";

const SIDES = ["left", "right", "bottom"];

// {dirs, reveal}: `dirs` the open directories' rows (read only while
// `shown`, and only those the tree draws), `reveal` {pageId, dir, nonce},
// bumped each time the tree shows the open page (`dir` the folder it is
// shown under), which the tree scrolls to once it draws it (FileTree.jsx).
// While `shown` on a desktop, a layout with no slot holding the window
// puts it first on the left. The open page (`focusedBlockId`, its row
// `focusedRow` once App knows it) is revealed once, when its row is first
// known and the window is shown: its folders opened (none closed), its
// directory paged until it holds the row, and the row selected. A page
// opened while the window is hidden is revealed when it is shown.
export function useFilesTree({ files, shown, libTree, workTree, focusedBlockId, focusedRow, layout, moveWindow, isPhone }) {
  // the open directories the tree draws: every folder above them open too
  const open = new Set(files.expanded);
  const wanted = files.expanded.filter((id) => workOf(workTree, id) && folderChain(libTree, id).every((f) => open.has(f)));
  const dirs = useDirectoryRows({ wanted, counts: libTree.workCounts, changes: libTree.workChanges, enabled: shown });
  useEffect(() => {
    if (!shown || isPhone) return;
    if (!SIDES.some((side) => layout[side]?.includes("files"))) moveWindow("files", "left", 0);
  }, [shown, isPhone, layout]); // eslint-disable-line react-hooks/exhaustive-deps
  const revealed = useRef("");
  const [reveal, setReveal] = useState({ pageId: "", dir: "", nonce: 0 });
  useEffect(() => {
    if (!shown || !focusedBlockId || !focusedRow || revealed.current === focusedBlockId) return;
    const chain = revealPath(libTree, focusedRow);
    if (!chain.length) return;
    revealed.current = focusedBlockId;
    files.expand(chain);
    const dir = chain.at(-1);
    if (workOf(workTree, dir)) dirs.find(dir, focusedBlockId);
    files.select(focusedRow);
    setReveal((r) => ({ pageId: focusedBlockId, dir, nonce: r.nonce + 1 }));
  }, [focusedBlockId, !!focusedRow, shown]); // eslint-disable-line react-hooks/exhaustive-deps
  return { dirs, reveal };
}
