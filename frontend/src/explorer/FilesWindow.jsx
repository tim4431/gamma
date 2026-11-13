// The Files window (docs/dev/work_folders.md "The Files window"): the
// library's folders and the work folders' files as a tree in a dock window
// beside the open page, on a phone an overlay. Loaded on first use
// (shared/ui/lazy.jsx), with the tree's rows and keys (FileTree.jsx,
// fileTree.js) and its stylesheet; its state is App's owner hooks'
// (useFilesWindow.js, useFilesTree.js), handed in as `files` and `tree`.
import { t } from "../shared/i18n/i18n.js";
import { ChevronUpIcon } from "../shared/ui/Icons";
import { DockWindow } from "../shared/ui/Widgets";
import FileTree from "./FileTree.jsx";
import "./filesWindow.css";

// common: the dock window's grip, fold and collapse (App's renderWindow);
// onClose closes the window. The rest is FileTree's.
export default function FilesWindow({ onClose, files, tree, libTree, workTree, folderMeta, libraryPages, lib, onOpen, pageMenu,
  folderMenu, menuPress, onDragStart, onDrop, dragOver, setDragOver, ...common }) {
  return (
    <DockWindow title={t("Files")} guide="files.grip" {...common} onClose={onClose}
      headerContent={(
        <button type="button" className="ctlBtn" onClick={files.collapseAll} aria-label={t("Collapse all")} title={t("Collapse all")}>
          <ChevronUpIcon size={16} />
        </button>
      )}>
      <div className="filesPanel">
        <FileTree files={files} tree={tree} libTree={libTree} workTree={workTree} folderMeta={folderMeta} libraryPages={libraryPages}
          lib={lib} onOpen={onOpen} pageMenu={pageMenu} folderMenu={folderMenu} menuPress={menuPress} onDragStart={onDragStart}
          onDrop={onDrop} dragOver={dragOver} setDragOver={setDragOver} />
      </div>
    </DockWindow>
  );
}
