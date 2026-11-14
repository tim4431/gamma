// The Comments window: the open page's threads, Open or Resolved, the
// latest activity first, each with what it is on (the note's words, the
// highlight's quote, the handwriting's page, or the page) and the chip of a
// Send waiting on it. A click scrolls to the anchor, flashes it and opens
// its thread popover (App's onOpen). A dock window like Notes
// and Chat, loaded on first use (shared/ui/lazy.jsx;
// docs/dev/comments.md "In the app"). The list moves as it changes
// (ListMotion): a thread that comes rises into place, and one with a new
// reply glides to the top as the others make room. The bell in its header
// is the page's Notify me, and on a work folder's file Feedback opens the
// feedback view (docs/dev/feedback.md).
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import ListMotion from "../shared/ui/ListMotion";
import { StateTag } from "../shared/ui/StateTag";
import { DockWindow } from "../shared/ui/Widgets";
import { Empty, Segmented } from "../settings/SettingsKit";
import { BellIcon, BotIcon, CheckIcon, MessageSquareIcon } from "../shared/ui/Icons";
import { PersonAvatar } from "../collaboration/Presence";
import { t, tn } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { formatRelativeTime } from "../library/libraryUtils";
import { anchorSnippet, authorLabel, authorTitle, sentChip, threadsIn } from "./comments.js";

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
// of the block a thread is on (comments.js anchorWords; "" when the page
// does not show it); `pageId`: the page's own thread reads "This page".
// labelOf(id): a [[link]]'s label, so a line names the page it links to.
// `notify`: the reader has an Inbox (a signed-in account, no guest), so the
// header has the page's Notify me; onStatus(text) says a choice the server
// refused. onFeedback(), when given (a work folder's file, a reader who may
// edit), is the header's Feedback. `agent`: the agent that answers the
// reader's Sends (App's commentAgent), so a waiting Send's chip reads as its
// thread's card does ("Waiting for TIM-PC" while that computer is offline).
export default function CommentsPanel({ threads, pageId, anchorText, labelOf, onOpen, onClose, notify = false, onStatus, onFeedback, agent = null, ...common }) {
  const [filter, setFilter] = useState("open");
  const [level, setLevel] = useNotifyLevel(pageId, notify, onStatus);
  const list = threadsIn(threads || [], filter);
  // The page's threads coming in fill the list from none: they come in still.
  const had = useRef(0);
  useLayoutEffect(() => { had.current = list.length; });
  // The tabs carry their counts ("Open" alone is the verb's key in the
  // catalog); in a narrow window their icons alone, the counts in their names.
  const filters = [
    ["open", t("Open ({n})", { n: threadsIn(threads || [], "open").length }), MessageSquareIcon],
    ["resolved", t("Resolved ({n})", { n: threadsIn(threads || [], "resolved").length }), CheckIcon],
  ];
  // The header's actions (DockWindow), folded into its "⋯" when the window
  // is too narrow for them.
  const actions = [
    notify && { id: "notify", icon: BellIcon, label: t("Notify me"), disabled: !level,
      choice: { value: level, onChange: setLevel, options: [["all", t("All comments")], ["replies", t("Replies and @mentions")]] } },
    onFeedback && { id: "feedback", icon: BotIcon, label: t("Feedback"), onClick: onFeedback,
      title: t("Feedback: this file's open comments, to send to an agent") },
  ].filter(Boolean);
  return (
    <DockWindow title={t("Comments")} guide="comments.grip" {...common} onClose={onClose}
      headerContent={<Segmented value={filter} onChange={setFilter} options={filters} />} actions={actions}>
      <div className="commentsPanel">
        <ListMotion keys={list.map((th) => th.id)} scope={`${pageId}:${filter}`} enter="rise" still={!had.current}>
          {list.length ? list.map((th) => {
            const first = th.comments[0];
            const who = first ? authorLabel(first) : "";
            const replies = th.comments.length - 1;
            const anchor = th.block_id === pageId ? t("This page") : anchorSnippet(anchorText(th.block_id), 80, labelOf) || t("A note");
            const chip = sentChip(th, agent);
            return (
              <button key={th.id} type="button" className={`commentsPanelRow${filter === "resolved" ? " resolved" : ""}`}
                data-motion-key={th.id} data-thread-id={th.id} onClick={() => onOpen(th)}>
                <span className="commentsPanelAnchor">{anchor}</span>
                <span className="commentsPanelFirst">
                  <PersonAvatar name={who || "?"} title={first ? authorTitle(first) : undefined} />
                  <span className="commentsPanelText">
                    <span className="commentAuthor">{who}</span>{" "}
                    {first?.deleted ? <em>{t("Comment deleted")}</em> : anchorSnippet(first?.body, 140, labelOf)}
                  </span>
                </span>
                <span className="commentsPanelMeta">
                  {chip ? <StateTag state={chip} data-sent={chip.kind} /> : null}
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
