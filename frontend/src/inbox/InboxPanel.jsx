// The Inbox panel under the bell (docs/dev/comments.md "In the app" › "The
// Inbox"): Unread, All or Archived, grouped by day, with Mark all read. A
// row says what happened (who, and on which page), the words it was about,
// the workspace when it is not this one, and when; its actions mark it read
// or unread and archive it or bring it back. A click on a row is App's
// `onOpen(item)`: marked read, the panel closed, and the jump. Loaded on
// first use (shared/ui/lazy.jsx), from the bell.
//
// The list moves as it changes (ListMotion): a row that comes fades in, one
// that goes (marked read in Unread, archived) leaves from where it stood
// while the rest close up, and a day's header goes with its last row. A tab
// opens on what it showed last (useInbox) and its first listing comes into
// focus where it will stand (Reveal).
import React, { useEffect, useRef } from "react";
import { Empty, Segmented } from "../settings/SettingsKit";
import { ArchiveIcon, ArchiveRestoreIcon, BellIcon, CheckIcon, MailIcon } from "../shared/ui/Icons";
import ListMotion from "../shared/ui/ListMotion";
import { IconSwap, Reveal } from "../shared/ui/MotionWidgets";
import { PersonAvatar } from "../collaboration/Presence";
import { t } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { inboxActor, inboxDays, inboxSentence, inboxSnippet, isUnread } from "./inboxText.js";
import { Popover } from "../shared/ui/Surfaces";

const EMPTY_TEXT = {
  unread: () => t("You're all caught up."),
  all: () => t("Nothing in your inbox yet."),
  archived: () => t("Nothing archived."),
};

function InboxRow({ item, inbox, workspaceId, now, onOpen }) {
  const unread = isUnread(item);
  const sentence = inboxSentence(item);
  const snippet = inboxSnippet(item);
  const elsewhere = item.workspace_id !== workspaceId && item.workspace_name ? item.workspace_name : "";
  const actor = inboxActor(item) || "?";
  const readLabel = unread ? t("Mark read") : t("Mark unread");
  const archiveLabel = item.archived_at ? t("Move back to the inbox") : t("Archive this item");
  return (
    <div className={`inboxRow${unread ? " unread" : ""}`} data-motion-key={item.id} data-inbox-id={item.id} data-kind={item.kind}>
      <PersonAvatar name={actor} title={inboxActor(item) || undefined} />
      <button type="button" className="inboxMain" onClick={() => onOpen(item)} title={sentence}>
        <span className="inboxSentence">{sentence}</span>
        {snippet ? <span className="inboxSnippet">{snippet}</span> : null}
        <span className="inboxMeta">
          {elsewhere ? <span className="inboxWs">{elsewhere}</span> : null}
          <span>{formatRelativeTime(item.at, now)}</span>
        </span>
      </button>
      <span className="ctlBtnRow inboxActions">
        {item.archived_at ? null : (
          <button type="button" className="ctlBtn inboxRead" title={readLabel} aria-label={readLabel}
            onClick={() => inbox.markRead(item, unread)}>
            <IconSwap swap={unread} from={<MailIcon size={14} />} to={<CheckIcon size={14} />} />
          </button>
        )}
        <button type="button" className="ctlBtn inboxArchive" title={archiveLabel} aria-label={archiveLabel}
          onClick={() => inbox.archive(item, !item.archived_at)}>
          <IconSwap swap={!!item.archived_at} from={<ArchiveIcon size={14} />} to={<ArchiveRestoreIcon size={14} />} />
        </button>
      </span>
      {unread ? <span className="inboxDot" aria-hidden="true" /> : null}
    </div>
  );
}

// inbox: useInbox's value; workspaceId: the workspace this tab works in.
export default function InboxPanel({ inbox, workspaceId, onOpen }) {
  const now = Date.now();
  const days = inboxDays(inbox.items, now);
  const filters = [["unread", t("Unread")], ["all", t("All")], ["archived", t("Archived")]];
  // Mark all read empties Unread at once: its rows leaving one by one, all
  // together, would say nothing.
  const markedAll = useRef(false);
  useEffect(() => { markedAll.current = false; });
  const empty = !inbox.items.length && inbox.loaded;
  // the list's items in order: each day's header, then its rows
  const keys = [];
  for (const day of days) {
    if (day.label) keys.push(`day:${day.key}`);
    for (const item of day.items) keys.push(item.id);
  }
  if (empty) keys.push("empty");
  if (inbox.more) keys.push("more");
  return (
    <Popover className="inboxPopover" role="region" aria-label={t("Inbox")}>
      <div className="popoverTitle inboxHead">
        <span>{t("Inbox")}</span>
        <button type="button" className="uiBtn ghost sm inboxMarkAll" disabled={!inbox.unread}
          onClick={() => { markedAll.current = true; inbox.markAllRead(); }}>{t("Mark all read")}</button>
      </div>
      <div className="inboxTabs">
        <Segmented value={inbox.filter} onChange={inbox.setFilter} options={filters} />
      </div>
      <div className="inboxList">
        <Reveal className="inboxRows" k="inbox" ready={inbox.loaded || inbox.items.length > 0}
          fallback={<div className="popoverHint inboxLoading">{t("Loading…")}</div>}>
          <ListMotion keys={keys} scope={inbox.filter} leave="row" strip={["data-inbox-id"]} still={markedAll.current}>
            {days.map((day) => (
              <React.Fragment key={day.key}>
                {day.label ? <div className="popoverSection" data-motion-key={`day:${day.key}`}>{day.label}</div> : null}
                {day.items.map((item) => (
                  <InboxRow key={item.id} item={item} inbox={inbox} workspaceId={workspaceId} now={now} onOpen={onOpen} />
                ))}
              </React.Fragment>
            ))}
            {empty ? (
              <div data-motion-key="empty">
                <Empty icon={inbox.filter === "archived" ? ArchiveIcon : BellIcon}>
                  {inbox.failed ? t("The inbox could not be loaded.") : EMPTY_TEXT[inbox.filter]()}
                </Empty>
              </div>
            ) : null}
            {inbox.more ? (
              <button type="button" className="uiBtn ghost sm inboxMore" data-motion-key="more"
                onClick={() => inbox.loadMore()}>{t("Show older")}</button>
            ) : null}
          </ListMotion>
        </Reveal>
      </div>
    </Popover>
  );
}
