// The account's reminders (docs/dev/mentions.md): GET /api/reminders on
// load, every few minutes and when the window comes back, and a timer for
// the next one due (app/reminders.js reminderState). A reminder due and not
// dismissed is a card (app/ReminderAlerts.jsx) and, once per browser and
// while it is fresh, a system notification when the browser allows them —
// asked the first time "Remind me" is picked, the gesture a permission
// prompt needs. `now` is also the chips' clock (editor/MentionMenu.jsx
// MentionClock): it moves when a reminder falls due and at midnight.
// One instance, in App.jsx; off in share views (`enabled`).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { reminderState, reminderText, ringsNow } from "./reminders";

const POLL_MS = 5 * 60 * 1000;
const RUNG_KEY = "gamma-reminders-rung"; // the keys this browser has notified, the latest kept
const RUNG_CAP = 200;

function rungKeys() {
  try { return JSON.parse(localStorage.getItem(RUNG_KEY) || "[]"); } catch { return []; }
}

export function useReminders(enabled, { labelOf, onOpen } = {}) {
  const [data, setData] = useState({ reminders: [], done: [] });
  const [now, setNow] = useState(() => new Date());
  const cb = useRef({});
  cb.current = { labelOf, onOpen };
  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const got = await apiJson(`${API}/reminders`);
      setData({ reminders: got.reminders || [], done: got.done || [] });
      setNow(new Date());
    } catch {
      // stays as it was: the next poll tries again
    }
  }, [enabled]);
  useEffect(() => {
    if (!enabled) { setData({ reminders: [], done: [] }); return undefined; }
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    const onFocus = () => { if (document.visibilityState !== "hidden") refresh(); };
    window.addEventListener("focus", onFocus);
    return () => { clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [enabled, refresh]);

  const { due, wake } = useMemo(() => reminderState(data.reminders, data.done, now), [data, now]);
  useEffect(() => {
    const timer = setTimeout(() => setNow(new Date()), Math.max(1000, wake - Date.now() + 250));
    return () => clearTimeout(timer);
  }, [wake]);

  // A fresh reminder rings once per browser (the tag also keeps two tabs
  // to one notification).
  useEffect(() => {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const rung = rungKeys();
    const fresh = due.filter((r) => ringsNow(r, now) && !rung.includes(r.key));
    if (!fresh.length) return;
    for (const r of fresh) {
      const n = new Notification(t("Reminder: {page}", { page: r.page_title || t("Untitled") }), {
        body: reminderText(r, cb.current.labelOf) || undefined, tag: `gamma-reminder:${r.key}`,
      });
      n.onclick = () => { window.focus(); cb.current.onOpen?.(r); n.close(); };
    }
    try { localStorage.setItem(RUNG_KEY, JSON.stringify([...rung, ...fresh.map((r) => r.key)].slice(-RUNG_CAP))); } catch {}
  }, [due, now]);

  const dismiss = useCallback((keys) => {
    setData((d) => ({ ...d, done: [...new Set([...d.done, ...keys])] }));
    apiJson(`${API}/reminders/done`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keys }),
    }).catch(() => refresh());
  }, [refresh]);

  // "Remind me" was just picked: ask once whether this browser may notify
  // (now, inside the key press or click), and look again once the note is saved.
  const noteSet = useCallback(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
    setTimeout(refresh, 2500);
  }, [refresh]);

  return useMemo(() => ({ due, now, dismiss, noteSet, refresh }), [due, now, dismiss, noteSet, refresh]);
}
