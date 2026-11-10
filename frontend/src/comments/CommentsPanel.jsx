// The Comments window: the open page's threads, Open or Resolved, the
// latest activity first, each with what it is on (the note's words, the
// highlight's quote, or the page). A click scrolls to the anchor, flashes
// it and opens its thread popover (App's onOpen). A dock window like Notes
// and Chat, loaded on first use (shared/ui/lazy.jsx;
// docs/dev/comments.md "In the app"). The list moves as it changes
// (ListMotion): a thread that comes rises into place, and one with a new
// reply glides to the top as the others make room. The bell in its header
// is the page's Notify me.
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import ListMotion from "../shared/ui/ListMotion";
import { DockWindow } from "../shared/ui/Widgets";
import { Empty, Segmented } from "../settings/SettingsKit";
import { MenuSelect } from "../shared/ui/Menus";
import { BellIcon, CheckIcon, MessageSquareIcon } from "../shared/ui/Icons";
import { PersonAvatar } from "../collaboration/Presence";
import { t, tn } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { formatRelativeTime } from "../library/libraryUtils";
import { anchorSnippet, threadsIn } from "./comments.js";

const notifyUrl = (page) => `${API}/pages/${encodeURIComponent(page)}/notify`;

// The page's Notify me for the reader (docs/dev/comments.md "Following a
// page"): "all" (every comment on it reaches their Inbox) or "replies"
// (replies and @mentions only); "" until it is read, and with `enabled`
// false (no Inbox to reach). A choice shows at once, and goes back, said in
// the status line, when the server refuses it.
function useNotifyLevel(pageId, enabled, onStatus) {
  const [state, setState] = useState({ pageId: "", level: "" });
  useEffect(() => {
    if (!enabled || !pageId) return undefined;
    let live = true;
    apiJson(notifyUrl(pageId))
      .then((d) => { if (live) setState({ pageId, level: d.level || "" }); })
      .catch(() => { if (live) setState({ pageId, level: "" }); });
    return () => { live = false; };
  }, [pageId, enabled]);
  const level = enabled && state.pageId === pageId ? state.level : "";
  const choose = useCallback((next) => {
    const before = level;
    setState({ pageId, level: next });
    apiJson(notifyUrl(pageId), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ level: next }) })
      .catch((err) => {
        setState((s) => (s.pageId === pageId && s.level === next ? { pageId, level: before } : s));
        onStatus?.(t("Couldn't change this page's notifications: {message}", { message: err?.message || "" }));
      });
  }, [pageId, level, onStatus]);
  return [level, choose];
}

// common: the dock window's grip, fold and collapse (App's renderWindow).
// `threads`: the page's, open and resolved. anchorText(blockId): the words
// of the block a thread is on ("" when the page does not show it);
// `pageId`: the page's own thread reads "This page". labelOf(id): a
// [[link]]'s label, so a line names the page it links to. `notify`: the
// reader has an Inbox (a signed-in account, no guest), so the header has
// the page's Notify me; onStatus(text) says a choice the server refused.
export default function CommentsPanel({ threads, pageId, anchorText, labelOf, onOpen, onClose, notify = false, onStatus, ...common }) {
  const [filter, setFilter] = useState("open");
  const [level, setLevel] = useNotifyLevel(pageId, notify, onStatus);
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
      headerContent={<>
        <Segmented value={filter} onChange={setFilter} options={filters} />
        {notify ? (
          <MenuSelect icon={BellIcon} label={t("Notify me")} heading={t("Notify me")} value={level} onChange={setLevel}
            disabled={!level} options={[["all", t("All comments")], ["replies", t("Replies and @mentions")]]} />
        ) : null}
      </>}>
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
