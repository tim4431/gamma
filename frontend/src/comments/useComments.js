// The open page's comment threads (docs/dev/comments.md "In the app"): one
// instance in App. It fetches them when the page opens (asked for alongside
// the page's tree: `prefetch`), again when the page's room says someone
// wrote one (`refresh`, from the socket's
// `comments` message and its `hello` after a reconnect), and makes the
// reader's own writes show at once, under ids minted here, before the
// server answers (a refused write is put back as it was and said in the
// status line). The folded maps the rows read keep their identity while
// they read the same (keepIfSame), so a refetch that changed nothing
// re-renders no row. `readable` turns false when the page's comments
// answer 404 or 403 (App then offers no comment controls).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { guideEvents } from "../guide/events.js";
import { API, apiJson, makeId } from "../shared/lib/utils";
import { nowStamp } from "../library/libraryUtils";
import { keepIfSame, sameMap } from "../shared/lib/keepIfSame.js";
import { t } from "../shared/i18n/i18n.js";
import { CLIENT_ID } from "../collaboration/usePageCollab";
import {
  addComment, deleteComment, editComment, findComment, foldThreads, keepThreads, resolveThread, upsertThread,
} from "./comments.js";
// The row control's, the PDF badge's and the lazy surfaces' rules: in the
// startup bundle with the rows that need them.
import "./comments.css";

const NO_THREADS = [];
const sameList = (a, b) => !!a && a.length === b.length && a.every((x, i) => x === b[i]);
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
// Every thread of a page, open and resolved.
const threadsOf = (page) => apiJson(`${API}/pages/${encodeURIComponent(page)}/comments?state=all`);

// opts: pageId (the open page, "" for none), enabled (false on the home
// library), me (the reader's username), onStatus(text), flush() (the page's
// unsaved edits sent first: a comment on a note or a highlight made a
// moment ago names a block the server must already have).
export function useComments({ pageId, enabled, me, onStatus, flush }) {
  const [state, setState] = useState({ pageId: "", threads: NO_THREADS, loaded: false, readable: true });
  const live = enabled && pageId ? pageId : "";
  const pageRef = useRef(live);
  pageRef.current = live;
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;
  const flushRef = useRef(flush);
  flushRef.current = flush;
  // The writes on their way, by comment id: shown again over a refetch
  // that answered before the server had them.
  const pendingRef = useRef(new Map());
  // `waiting`: the refresh() calls the next request answers. `ahead`: the
  // request a page's opening asked for early ({page, request}), which the
  // first fetch for that page takes instead of asking again.
  const fetchRef = useRef({ busy: false, again: false, waiting: [], ahead: null });
  // The one prefetch() started last, until the page it names opens.
  const aheadRef = useRef(null);

  const set = useCallback((page, fn) => {
    setState((s) => (s.pageId === page ? { ...s, threads: fn(s.threads) } : s));
  }, []);

  // Resolves with what the request it waited for answered, `{pageId,
  // threads}`, or null (no page, the page changed, the request failed).
  const fetchNow = useCallback(() => {
    const page = pageRef.current;
    const f = fetchRef.current;
    const waiting = f.waiting;
    f.waiting = [];
    const done = (out) => { for (const w of waiting) w(out); };
    if (!page) { done(null); return; }
    const ahead = f.ahead?.page === page ? f.ahead.request : null;
    f.ahead = null;
    f.busy = true;
    (ahead || threadsOf(page)).then((d) => {
      if (pageRef.current !== page) { done(null); return; }
      let threads = d.threads || [];
      for (const p of pendingRef.current.values()) {
        if (p.pageId === page) threads = addComment(threads, p.comment, p);
      }
      setState((s) => ({ pageId: page, loaded: true, readable: true, threads: keepThreads(s.pageId === page ? s.threads : null, threads) }));
      done({ pageId: page, threads });
    }).catch((err) => {
      done(null);
      if (pageRef.current !== page) return;
      // A page in Recently deleted or gone (404), a share that stopped
      // (403): no threads, and none to write. Offline or a server error:
      // what is shown stays.
      const gone = err?.status === 404 || err?.status === 403;
      setState((s) => ({
        pageId: page, loaded: true, readable: gone ? false : s.pageId !== page || s.readable,
        threads: gone || s.pageId !== page ? NO_THREADS : s.threads,
      }));
    }).finally(() => {
      f.busy = false;
      if (f.again) { f.again = false; fetchNow(); }
    });
  }, []);
  const refresh = useCallback(() => new Promise((resolve) => {
    const f = fetchRef.current;
    f.waiting.push(resolve);
    // One request at a time; calls meanwhile make one more after it.
    if (f.busy) { f.again = true; return; }
    fetchNow();
  }), [fetchNow]);

  // A page about to open (App's openBlock): its threads are asked for now,
  // in parallel with its tree, so they come with the page rather than a
  // round trip after it. Only the fetch its opening makes takes the answer;
  // one for a page that did not open is dropped at the next page change.
  const prefetch = useCallback((page) => {
    const request = threadsOf(page);
    request.catch(() => {}); // the fetch that takes it says what failed
    aheadRef.current = { page, request };
  }, []);

  useEffect(() => {
    pendingRef.current.clear();
    const f = fetchRef.current;
    f.again = false;
    const ahead = aheadRef.current;
    aheadRef.current = null;
    f.ahead = live && ahead?.page === live ? ahead : null;
    setState({ pageId: live, threads: NO_THREADS, loaded: false, readable: true });
    if (live) refresh();
  }, [live, refresh]);

  const threads = state.pageId === live ? state.threads : NO_THREADS;
  const byBlockRef = useRef(null);
  const countsRef = useRef(null);
  const { threadsByBlock, countByBlock } = useMemo(() => {
    const { byBlock, counts } = foldThreads(threads);
    return {
      // (a block's list is new on every fold; its threads keep their identity: keepThreads)
      threadsByBlock: keepIfSame(byBlockRef, byBlock, (x, y) => sameMap(x, y, sameList)),
      countByBlock: keepIfSame(countsRef, counts, sameMap),
    };
  }, [threads]);

  const failed = (what, err) => statusRef.current?.(t("Couldn't save the comment: {message}", { message: err?.message || what }));

  // A new thread on `blockId`, or a reply in `threadId`. Resolves the
  // comment's id once stored (a new thread's id is its first comment's),
  // false when refused (the composer then keeps the text).
  const add = useCallback(async (blockId, body, threadId = "") => {
    const page = pageRef.current;
    if (!page) return false;
    const id = makeId();
    const at = nowStamp();
    const comment = { id, thread_id: threadId || id, author: me || "", body, created_at: at, updated_at: at, deleted: false };
    const p = { comment, blockId, pageId: page };
    pendingRef.current.set(id, p);
    set(page, (list) => addComment(list, comment, p));
    try {
      try { await flushRef.current?.(); } catch { /* the save says so itself; the server answers for the block */ }
      const out = await apiJson(`${API}/pages/${encodeURIComponent(page)}/comments`,
        json("POST", { id, block_id: blockId, body, thread_id: threadId, client: CLIENT_ID }));
      pendingRef.current.delete(id);
      set(page, (list) => upsertThread(list, out.thread));
      guideEvents.emit("comment.created", { reply: !!threadId });
      return id;
    } catch (err) {
      pendingRef.current.delete(id);
      set(page, (list) => deleteComment(list, id));
      failed("", err);
      return false;
    }
  }, [me, set]); // eslint-disable-line react-hooks/exhaustive-deps

  // Where a comment's write goes, and how to put the page back when it is
  // refused: the threads as they were, while the list is still the one the
  // change made (a refetch or another write since: the refetch that
  // follows repairs it). The change is kept by its input, so React's
  // second call of the updater (StrictMode) makes the same list.
  const write = useCallback(async (change, request, after) => {
    const page = pageRef.current;
    if (!page) return false;
    let before = null;
    let made = null;
    setState((s) => {
      if (s.pageId !== page) return s;
      if (before !== s.threads) { before = s.threads; made = change(s.threads); }
      return { ...s, threads: made };
    });
    try {
      const out = await request();
      if (after) set(page, (list) => after(list, out));
      return true;
    } catch (err) {
      setState((s) => (s.pageId === page && made && s.threads === made ? { ...s, threads: before } : s));
      failed("", err);
      refresh();
      return false;
    }
  }, [set, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const edit = useCallback((id, body) => write(
    (list) => editComment(list, id, body, nowStamp()),
    () => apiJson(`${API}/comments/${encodeURIComponent(id)}`, json("PATCH", { body, client: CLIENT_ID })),
    (list, out) => {
      const found = out?.comment && findComment(list, id);
      return found ? upsertThread(list, { ...found.thread, comments: found.thread.comments.map((c) => (c.id === id ? out.comment : c)) }) : list;
    },
  ), [write]);

  const remove = useCallback((id) => write(
    (list) => deleteComment(list, id),
    () => apiJson(`${API}/comments/${encodeURIComponent(id)}?client=${encodeURIComponent(CLIENT_ID)}`, { method: "DELETE" }),
  ), [write]);

  const resolve = useCallback((threadId, resolved) => write(
    (list) => resolveThread(list, threadId, resolved, me || "", nowStamp()),
    () => apiJson(`${API}/comments/${encodeURIComponent(threadId)}/resolve`, json("POST", { resolved, client: CLIENT_ID })),
    (list, out) => upsertThread(list, out?.thread),
  ), [write, me]);

  const here = state.pageId === live;
  return {
    threads, threadsByBlock, countByBlock, loaded: state.loaded && here, readable: !here || state.readable,
    refresh, prefetch, add, edit, remove, resolve,
  };
}
