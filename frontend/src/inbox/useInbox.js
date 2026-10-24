// The account's Inbox as the tab holds it (docs/dev/comments.md "In the
// app" › "The Inbox"): one instance in App, on for a signed-in account that
// is not a guest, outside share views. The bell's count comes from
// GET /api/inbox/unread every 30 s while the tab shows, and on window focus
// or when a hidden tab shows again; every listing and every mark answer
// carries the count too, and the bell takes it from them (a listing
// archives the items that lead nowhere any more, which the bare count still
// includes). While the panel is open (`open`) the listing itself is asked
// every 5 s. A page's socket says `{t: "inbox"}` when something arrives;
// usePageCollab turns it into the window event gamma:inbox. A nudge, a
// focus and a tab showing again are taken 300 ms after the last of them, as
// one look (a return to the tab fires both focus and visibilitychange, and
// a comment on the open page also sends `comments`).
//
// Items load when the panel opens, 50 at a time ("Show older"). Each tab
// keeps the listing it last showed: the panel opens, and a tab comes back,
// on the first page of it while a fresh look is on its way, and the
// server's first page that look brings takes its place, changing it in
// place (`loaded` turns true once it is in). Show older waits for that
// look, and the looks after it keep the older rows it loads. Marks show
// at once; an answer to a request sent before the latest mark, or while a
// mark was on its way (the server may have answered it before the mark was
// stored), is dropped, so a poll never undoes a mark, and a count or
// listing dropped that way is asked again once the marks have settled. A refused mark puts back the
// rows and the count it changed (unless a later mark has changed them
// since), then looks again. With `notify` (the browser pref
// inboxSystemNotifications) and the browser's permission, a fresh unread
// item rings once per browser as a system notification, as reminders do
// (app/useReminders.js); its click is `onOpen(item)`.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { nowStamp } from "../library/libraryUtils";
import { t } from "../shared/i18n/i18n.js";
import { applyMark, inboxSentence, inboxSnippet, itemsToRing, lookedListing, reopenedListing, sameListing } from "./inboxText.js";

const IDLE_MS = 30 * 1000;
const OPEN_MS = 5 * 1000;
const NUDGE_MS = 300;
const PAGE = 50;
const MARK_BATCH = 100; // the ids one POST /api/inbox/mark takes
const RUNG_KEY = "gamma-inbox-rung"; // the item ids this browser has rung, the latest kept
const RUNG_CAP = 200;
const EMPTY = { filter: "unread", items: [], more: false, loaded: false, failed: false };
const post = (body) => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
// A mark goes through the page's keepalive ledger, small and recoverable
// (shared/lib/keepalive.js 'normal'): a navigation does not cut it off.
const MARK = { keepalive: "normal" };

function rungIds() {
  try { return JSON.parse(localStorage.getItem(RUNG_KEY) || "[]"); } catch { return []; }
}
const canRing = () => typeof Notification !== "undefined" && Notification.permission === "granted";

// opts: enabled, open (the panel is showing), notify (system notifications
// on), onOpen(item) (a notification's click), onStatus(text).
export function useInbox({ enabled, open, notify = false, onOpen, onStatus }) {
  const [unread, setUnread] = useState(0);
  const [list, setList] = useState(EMPTY);
  const listRef = useRef(list);
  listRef.current = list;
  // The listing each tab showed last, by filter: what it opens on again.
  const kept = useRef(new Map());
  useEffect(() => { kept.current.set(list.filter, list); }, [list]);
  const cb = useRef({});
  cb.current = { onOpen, onStatus, notify, open, enabled };
  // Bumped by every mark sent: an answer to an earlier request is stale.
  const markGen = useRef(0);
  const marking = useRef(0);
  const known = useRef(0); // the count last shown, to tell when it rose
  // A listing was dropped or a mark refused while marks were on their way:
  // look again once the last of them settles.
  const recheck = useRef(false);

  // What a request knew when it was sent: the marks sent so far, and whether
  // one was still on its way. An answer is stale when a mark was sent after
  // it, when one is on its way now, or when one was on its way as it went
  // out: the server may have answered it before that mark was stored.
  const stamp = () => ({ gen: markGen.current, busy: marking.current > 0 });
  const stale = (s) => s.gen !== markGen.current || s.busy || marking.current > 0;
  const takeCount = useCallback((n, s) => {
    if (stale(s) || typeof n !== "number") return false;
    known.current = n;
    setUnread(n);
    return true;
  }, []);

  // A fresh unread item rings once per browser (the tag keeps two tabs to one).
  const ring = useCallback(async () => {
    if (!cb.current.notify || !canRing()) return;
    const sent = stamp();
    let got;
    try { got = await apiJson(`${API}/inbox?filter=unread&limit=10`); } catch { return; }
    takeCount(got.unread, sent); // a listing's count, as everywhere
    const rung = rungIds();
    const fresh = itemsToRing(got.items, rung);
    if (!fresh.length) return;
    try { localStorage.setItem(RUNG_KEY, JSON.stringify([...rung, ...fresh.map((i) => i.id)].slice(-RUNG_CAP))); } catch {}
    for (const item of fresh) {
      const n = new Notification(inboxSentence(item), { body: inboxSnippet(item) || undefined, tag: `gamma-inbox:${item.id}` });
      n.onclick = () => { window.focus(); cb.current.onOpen?.(item); n.close(); };
    }
  }, [takeCount]);

  // The listing of the shown tab, its first page over what is held.
  const fetchList = useCallback(async function load() {
    const filter = listRef.current.filter;
    const sent = stamp();
    try {
      const got = await apiJson(`${API}/inbox?filter=${filter}&limit=${PAGE}`);
      if (listRef.current.filter !== filter) return; // the new tab's listing is on its way
      if (stale(sent)) {
        // older than a mark: asked again once the marks have settled
        if (marking.current) recheck.current = true;
        else load();
        return;
      }
      takeCount(got.unread, sent);
      setList((cur) => {
        if (cur.filter !== filter) return cur;
        const next = lookedListing(cur, got);
        return sameListing(cur, next) ? cur : next; // an unchanged poll renders nothing
      });
    } catch {
      // what is held stays, still to be replaced by the next look; with
      // nothing held, the tab says it could not load
      setList((cur) => (cur.filter !== filter || cur.items.length ? cur : { ...cur, loaded: true, failed: true }));
    }
  }, [takeCount]);

  const fetchCount = useCallback(async () => {
    const sent = stamp();
    try {
      const got = await apiJson(`${API}/inbox/unread`);
      const before = known.current;
      if (takeCount(got.unread, sent)) {
        if (got.unread > before) ring();
      } else if (marking.current) {
        recheck.current = true; // asked again once the marks have settled
      } else if (sent.busy && sent.gen === markGen.current) {
        fetchCount(); // sent while a mark was on its way: ask again now it is stored
      }
    } catch {
      // stays as it was: the next poll asks again
    }
  }, [takeCount, ring]);

  const refresh = useCallback(() => {
    if (!cb.current.enabled) return;
    if (cb.current.open) fetchList();
    else fetchCount();
  }, [fetchList, fetchCount]);

  useEffect(() => {
    if (!enabled) { setUnread(0); known.current = 0; kept.current.clear(); setList(EMPTY); return undefined; }
    if (open) {
      // the panel opens on the tab it showed last, its first page as it
      // was, and looks again
      const filter = listRef.current.filter;
      const next = reopenedListing(kept.current.get(filter), filter, PAGE);
      listRef.current = next;
      setList(next);
      fetchList();
    } else fetchCount();
    const timer = setInterval(() => { if (document.visibilityState !== "hidden") refresh(); }, open ? OPEN_MS : IDLE_MS);
    // One look 300 ms after the last nudge, focus or show: the count or the
    // open listing, and after a socket's nudge with the panel open the rings
    // (with it closed, the count's rise rings).
    let nudge = null;
    let nudgeRings = false;
    const soon = (rings) => {
      nudgeRings = nudgeRings || rings;
      clearTimeout(nudge);
      nudge = setTimeout(() => {
        const withRings = nudgeRings;
        nudgeRings = false;
        refresh();
        if (withRings && cb.current.open) ring();
      }, NUDGE_MS);
    };
    const onFocus = () => { if (document.visibilityState !== "hidden") soon(false); };
    const onShow = () => { if (document.visibilityState === "visible") soon(false); };
    const onNudge = () => soon(true);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onShow);
    window.addEventListener("gamma:inbox", onNudge);
    return () => {
      clearInterval(timer);
      clearTimeout(nudge);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onShow);
      window.removeEventListener("gamma:inbox", onNudge);
    };
  }, [enabled, open, fetchList, fetchCount, refresh, ring]);

  const setFilter = useCallback((filter) => {
    if (filter === listRef.current.filter) return;
    const next = reopenedListing(kept.current.get(filter), filter, PAGE);
    listRef.current = next;
    setList(next);
    fetchList();
  }, [fetchList]);

  const loadMore = useCallback(async () => {
    const { filter, items } = listRef.current;
    if (!items.length) return;
    const before = items[items.length - 1].at;
    const sent = stamp();
    try {
      const got = await apiJson(`${API}/inbox?filter=${filter}&limit=${PAGE}&before=${encodeURIComponent(before)}`);
      if (listRef.current.filter !== filter) return;
      takeCount(got.unread, sent);
      setList((cur) => {
        const seen = new Set(cur.items.map((i) => i.id));
        return { ...cur, items: [...cur.items, ...(got.items || []).filter((i) => !seen.has(i.id))], more: !!got.more };
      });
    } catch (err) {
      cb.current.onStatus?.(t("Couldn't load the inbox: {message}", { message: err?.message || "" }));
    }
  }, [takeCount]);

  // The last mark on its way has settled: look again when one was refused
  // or a listing was dropped meanwhile.
  const settled = useCallback(() => {
    if (marking.current || !recheck.current) return;
    recheck.current = false;
    refresh();
  }, [refresh]);

  // A mark, shown at once; the server's count replaces the guess when this
  // is still the latest mark. A refused one puts back what it changed, when
  // no later mark has changed it since, and is put right by a fresh look.
  const mark = useCallback(async (target, change) => {
    const gen = ++markGen.current;
    marking.current += 1;
    const before = { filter: listRef.current.filter, items: listRef.current.items, known: known.current };
    const { items, unreadDelta } = applyMark(listRef.current.items, target, change, listRef.current.filter, nowStamp());
    const guess = target.all && change.read ? 0 : Math.max(0, known.current + unreadDelta);
    known.current = guess;
    setUnread(guess);
    setList((cur) => ({ ...cur, items }));
    try {
      const ids = target.ids || [];
      let got = null;
      if (target.all) got = await apiJson(`${API}/inbox/mark`, post({ all: true, ...change }), MARK);
      for (let i = 0; i < ids.length; i += MARK_BATCH) {
        got = await apiJson(`${API}/inbox/mark`, post({ ids: ids.slice(i, i + MARK_BATCH), ...change }), MARK);
      }
      marking.current -= 1;
      // the mark's own answer counts once it is the latest and the last on its way
      if (got) takeCount(got.unread, { gen, busy: false });
      settled();
      return true;
    } catch (err) {
      marking.current -= 1;
      cb.current.onStatus?.(t("Couldn't update the inbox: {message}", { message: err?.message || "" }));
      if (gen === markGen.current) {
        markGen.current += 1; // the answers sent before it are stale too
        known.current = before.known;
        setUnread(before.known);
        setList((cur) => (cur.filter === before.filter ? { ...cur, items: before.items } : cur));
      }
      recheck.current = true;
      settled();
      return false;
    }
  }, [takeCount, settled]);

  const markRead = useCallback((item, read = true) => mark({ ids: [item.id] }, { read }), [mark]);
  const archive = useCallback((item, archived = true) => mark({ ids: [item.id] }, { archived }), [mark]);
  const markAllRead = useCallback(() => mark({ all: true }, { read: true }), [mark]);

  return useMemo(() => ({
    enabled: !!enabled, unread, filter: list.filter, items: list.items, more: list.more, loaded: list.loaded,
    failed: list.failed, setFilter, loadMore, markRead, archive, markAllRead, refresh,
  }), [enabled, unread, list, setFilter, loadMore, markRead, archive, markAllRead, refresh]);
}
