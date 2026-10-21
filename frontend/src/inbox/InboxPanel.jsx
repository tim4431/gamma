// The Inbox panel under the bell (docs/dev/comments.md "In the app" › "The
// Inbox"): Unread, All or Archived, grouped by day, with Mark all read. A
// row says what happened (who, and on which page), the words it was about,
// the workspace when it is not this one, and when; its actions mark it read
// or unread and archive it or bring it back. A click on a row is App's
// `onOpen(item)`: marked read, the panel closed, and the jump. Loaded on
// first use (shared/ui/lazy.jsx), from the bell.
import React from "react";
import { Empty, Segmented } from "../settings/SettingsKit";
import { ArchiveIcon, ArchiveRestoreIcon, BellIcon, CheckIcon, MailIcon } from "../shared/ui/Icons";
import { PeerAvatar, nameColor } from "../collaboration/Presence";
import { t } from "../shared/i18n/i18n.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { inboxDays, inboxSentence, inboxSnippet, isUnread } from "./inboxText.js";

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
  const actor = item.actor || "?";
  const readLabel = unread ? t("Mark read") : t("Mark unread");
  const archiveLabel = item.archived_at ? t("Move back to the inbox") : t("Archive this item");
  return (
    <div className={`inboxRow${unread ? " unread" : ""}`} data-inbox-id={item.id} data-kind={item.kind}>
      <PeerAvatar peer={{ name: actor, color: nameColor(actor), anchor: 0 }} title={item.actor || undefined} />
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
            {unread ? <CheckIcon size={14} /> : <MailIcon size={14} />}
          </button>
        )}
        <button type="button" className="ctlBtn inboxArchive" title={archiveLabel} aria-label={archiveLabel}
          onClick={() => inbox.archive(item, !item.archived_at)}>
          {item.archived_at ? <ArchiveRestoreIcon size={14} /> : <ArchiveIcon size={14} />}
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
  return (
    <div className="popover inboxPopover" role="region" aria-label={t("Inbox")}>
      <div className="popoverTitle inboxHead">
        <span>{t("Inbox")}</span>
        <button type="button" className="uiBtn ghost sm inboxMarkAll" disabled={!inbox.unread}
          onClick={() => inbox.markAllRead()}>{t("Mark all read")}</button>
      </div>
      <div className="inboxTabs">
        <Segmented value={inbox.filter} onChange={inbox.setFilter} options={filters} />
      </div>
      <div className="inboxList">
        {days.map((day) => (
          <React.Fragment key={day.key}>
            {day.label ? <div className="popoverSection">{day.label}</div> : null}
            {day.items.map((item) => (
              <InboxRow key={item.id} item={item} inbox={inbox} workspaceId={workspaceId} now={now} onOpen={onOpen} />
            ))}
          </React.Fragment>
        ))}
        {!inbox.items.length && inbox.loaded ? (
          <Empty icon={inbox.filter === "archived" ? ArchiveIcon : BellIcon}>
            {inbox.failed ? t("The inbox could not be loaded.") : EMPTY_TEXT[inbox.filter]()}
          </Empty>
        ) : null}
        {!inbox.loaded ? <div className="popoverHint inboxLoading">{t("Loading…")}</div> : null}
        {inbox.more ? (
          <button type="button" className="uiBtn ghost sm inboxMore" onClick={() => inbox.loadMore()}>{t("Show older")}</button>
        ) : null}
      </div>
    </div>
  );
}
