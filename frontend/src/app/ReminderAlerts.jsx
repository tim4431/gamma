// The reminders due (app/useReminders.js): a stack of cards in the bottom
// right corner, each saying when, what its note says and on which page,
// with Open (the note, in its workspace) and a × that dismisses it for
// this account everywhere. Past three, one line offers to dismiss them all.
import React from "react";
import { AlarmClockIcon, XIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { REMIND_TIME, dateLabel } from "../editor/mentions.js";
import { reminderText } from "./reminders";

const SHOWN = 3;

export function ReminderAlerts({ due, now, labelOf, onOpen, onDismiss, phone = false }) {
  if (!due.length) return null;
  return (
    <div className={`reminderAlerts${phone ? " phone" : ""}`} role="region" aria-label={t("Reminders")}>
      {due.slice(0, SHOWN).map((r) => (
        <div key={r.key} className="reminderCard" role="alert">
          <span className="reminderIcon"><AlarmClockIcon size={16} /></span>
          <div className="reminderBody">
            <div className="reminderWhen">{dateLabel({ date: r.date, time: r.time || REMIND_TIME }, now)}</div>
            <div className="reminderText">{reminderText(r, labelOf, now) || t("(empty)")}</div>
            <div className="reminderPage">{r.page_title || t("Untitled")}</div>
            <button type="button" className="uiBtn sm" onClick={() => onOpen(r)}>{t("Open")}</button>
          </div>
          <button type="button" className="uiClose uiCloseSm" title={t("Dismiss")} aria-label={t("Dismiss")}
            onClick={() => onDismiss([r.key])}><XIcon size={14} /></button>
        </div>
      ))}
      {due.length > SHOWN ? (
        <button type="button" className="uiBtn sm ghost reminderAll" onClick={() => onDismiss(due.map((r) => r.key))}>
          {t("Dismiss all {n} reminders", { n: due.length })}
        </button>
      ) : null}
    </div>
  );
}
