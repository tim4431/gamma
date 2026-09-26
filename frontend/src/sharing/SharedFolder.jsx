// The share view of a folder share (/?share=<token> naming a folder): the
// pages the link reaches, as the home library's cards, newest edit first —
// what GET /api/share/{token} listed (`pages`). A card opens its page in
// the same share view (App puts `page=<id>` beside the token in the URL and
// loads the page through the token, like a page share); the topbar's home
// button comes back here. Presentational: App owns the data and navigation.
import React from "react";
import { PageCard } from "../library/FileBrowser";
import { formatRelativeTime, pageKindLabel } from "../library/libraryUtils";
import { FileGlyph, FolderOpenIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export function SharedFolder({ folder, pages, labelMode, onOpen }) {
  return (
    <div className="sharedFolder" data-shared-folder={folder}>
      <div className="folderBrowser">
        <div className="folderCurrent">
          <FolderOpenIcon size={15} />
          <span className="sharedFolderName">{folder}</span>
          <span className="folderHint">{t("{n} page{_s}", { n: pages.length, _s: pages.length === 1 ? "" : "s" })}</span>
        </div>
      </div>
      {pages.length === 0 ? (
        <div className="empty">{t("No pages are filed in this folder yet.")}</div>
      ) : (
        <div className="fileGrid">
          {pages.map((p) => {
            const attachment = p.doc_id ? { id: p.doc_id } : null;
            return (
              <PageCard
                key={p.id}
                glyph={<FileGlyph isPdf={!!attachment} />}
                title={p.title}
                tip={t("{content}\nClick to open", { content: p.title })}
                kind={pageKindLabel(attachment)}
                time={formatRelativeTime(p.updated_at)}
                folders={p.folders} labels={p.labels} labelMode={labelMode}
                role="link"
                tabIndex={0}
                onClick={() => onOpen(p.id)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(p.id); } }}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

