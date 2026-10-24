// The reminders due (app/useReminders.js): a stack of cards in the bottom
// right corner, each saying when, what its note says and on which page,
// with Open (the note, in its workspace) and a × that dismisses it for
// this account everywhere. Past three, one line offers to dismiss them all.
// A card rises in when it comes due and sinks out when dismissed
// (motion.css, useLeave).
import React from "react";
import { AlarmClockIcon, XIcon } from "../shared/ui/Icons";
import { useLeave } from "../shared/ui/leave.js";
import { t } from "../shared/i18n/i18n.js";
import { REMIND_TIME, dateLabel } from "../editor/mentions.js";
import { reminderText } from "./reminders";

const SHOWN = 3;

export function ReminderAlerts({ due, now, labelOf, onOpen, onDismiss, phone = false }) {
  // The stack stays when its last card goes, so that card has somewhere to
  // leave from; empty, it is no region.
  return (
    <div className={`reminderAlerts${phone ? " phone" : ""}`} {...(due.length ? { role: "region", "aria-label": t("Reminders") } : {})}>
      {due.slice(0, SHOWN).map((r) => (
        <ReminderCard key={r.key} reminder={r} now={now} labelOf={labelOf} onOpen={onOpen} onDismiss={onDismiss} />
      ))}
      {due.length > SHOWN ? (
        <button type="button" className="uiBtn sm ghost reminderAll" onClick={() => onDismiss(due.map((r) => r.key))}>
          {t("Dismiss all {n} reminders", { n: due.length })}
        </button>
      ) : null}
    </div>
  );
}

function ReminderCard({ reminder: r, now, labelOf, onOpen, onDismiss }) {
  const leave = useLeave("toast");
  return (
    <div ref={leave} className="reminderCard" role="alert">
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
  );
}
