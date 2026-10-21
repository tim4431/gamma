// The Inbox's bell in the topbar (docs/dev/comments.md "In the app" › "The
// Inbox"), with the unread count on it ("9+" past nine), and the same
// count as a menu row's trailing badge (the phone's More sheet). In the
// startup bundle; the panel it opens is InboxPanel.jsx, fetched when the
// pointer or the focus reaches the bell.
import React from "react";
import { BellIcon } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";
import { shortCount } from "../comments/comments.js";
import "./inbox.css";

export function InboxButton({ unread, open, onToggle, onPreload }) {
  // the name carries the count, which the badge shows only to the eye
  const name = unread ? tn("Inbox — {n} unread", "Inbox — {n} unread", unread) : t("Inbox");
  return (
    <button
      type="button"
      className={`iconBtn inboxBtn ${open ? "activeIcon" : ""}`}
      onClick={onToggle}
      onPointerEnter={onPreload}
      onFocus={onPreload}
      data-guide="header.inbox"
      aria-expanded={open}
      title={name}
      aria-label={name}
    >
      <BellIcon size={16} />
      <InboxBadge unread={unread} />
    </button>
  );
}

// The count: on the button's corner, or `inline` after a menu row's label.
export function InboxBadge({ unread, inline = false }) {
  if (!unread) return null;
  return <span className={`inboxBadge${inline ? " inline" : ""}`} aria-hidden="true">{shortCount(unread)}</span>;
}
