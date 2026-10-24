// The Comments window: the open page's threads, Open or Resolved, the
// latest activity first, each with what it is on (the note's words, the
// highlight's quote, or the page). A click scrolls to the anchor, flashes
// it and opens its thread popover (App's onOpen). A dock window like Notes
// and Chat, loaded on first use (shared/ui/lazy.jsx;
// docs/dev/comments.md "In the app"). The list moves as it changes
// (ListMotion): a thread that comes rises into place, and one with a new
// reply glides to the top as the others make room.
import React, { useLayoutEffect, useRef, useState } from "react";
import ListMotion from "../shared/ui/ListMotion";
import { DockWindow } from "../shared/ui/Widgets";
import { Empty, Segmented } from "../settings/SettingsKit";
import { CheckIcon, MessageSquareIcon } from "../shared/ui/Icons";
import { PersonAvatar } from "../collaboration/Presence";
import { t, tn } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { anchorSnippet, threadsIn } from "./comments.js";

// common: the dock window's grip, fold and collapse (App's renderWindow).
// `threads`: the page's, open and resolved. anchorText(blockId): the words
// of the block a thread is on ("" when the page does not show it);
// `pageId`: the page's own thread reads "This page". labelOf(id): a
// [[link]]'s label, so a line names the page it links to.
export default function CommentsPanel({ threads, pageId, anchorText, labelOf, onOpen, onClose, ...common }) {
  const [filter, setFilter] = useState("open");
  const list = threadsIn(threads || [], filter);
  // The page's threads coming in fill the list from none: they come in still.
  const had = useRef(0);
  useLayoutEffect(() => { had.current = list.length; });
  // The tabs carry their counts ("Open" alone is the verb's key in the catalog).
  const filters = [
    ["open", t("Open ({n})", { n: threadsIn(threads || [], "open").length })],
    ["resolved", t("Resolved ({n})", { n: threadsIn(threads || [], "resolved").length })],
  ];
  return (
    <DockWindow title={t("Comments")} guide="comments.grip" {...common} onClose={onClose}
      headerContent={<Segmented value={filter} onChange={setFilter} options={filters} />}>
      <div className="commentsPanel">
        <ListMotion keys={list.map((th) => th.id)} scope={`${pageId}:${filter}`} enter="rise" still={!had.current}>
          {list.length ? list.map((th) => {
            const first = th.comments[0];
            const replies = th.comments.length - 1;
            const anchor = th.block_id === pageId ? t("This page") : anchorSnippet(anchorText(th.block_id), 80, labelOf) || t("A note");
            return (
              <button key={th.id} type="button" className={`commentsPanelRow${filter === "resolved" ? " resolved" : ""}`}
                data-motion-key={th.id} data-thread-id={th.id} onClick={() => onOpen(th)}>
                <span className="commentsPanelAnchor">{anchor}</span>
                <span className="commentsPanelFirst">
                  <PersonAvatar name={first?.author || "?"} title={first?.author} />
                  <span className="commentsPanelText">
                    <span className="commentAuthor">{first?.author}</span>{" "}
                    {first?.deleted ? <em>{t("Comment deleted")}</em> : anchorSnippet(first?.body, 140, labelOf)}
                  </span>
                </span>
                <span className="commentsPanelMeta">
                  {replies ? <span>{tn("{n} reply", "{n} replies", replies)}</span> : null}
                  <span>{formatRelativeTime(th.last_at)}</span>
                </span>
              </button>
            );
          }) : (
            <Empty icon={filter === "open" ? MessageSquareIcon : CheckIcon}>
              {filter === "open" ? t("No open comments on this page.") : t("No resolved comments on this page.")}
            </Empty>
          )}
        </ListMotion>
      </div>
    </DockWindow>
  );
}
