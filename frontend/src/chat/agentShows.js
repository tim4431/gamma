// What the chat's show_page asks the app to show (gamma/ai_tools.py
// _run_show_page: {page_id, pdf_page?, quote?, block_id?, line?}), and when.
// The chat follows the open page (docs/dev/chat_history.md "Buckets"), so
// opening another page while the reply streams would hide the reply. A show
// of the page the chat is beside lands at once; one of another page waits
// for its reply's last `done` and lands only if the reply ended on its own
// and its chat is still the one on screen. A reply whose chat is not on
// screen (running on behind another) shows nothing. App's handleAgentEvent
// asks; one store serves the app, as one dock is mounted at a time.

// `{kind, show}` for a stream event: "now" to land on `show`, "later"
// when it waits for the reply's end, null for nothing. `dockKey` is the
// conversation the dock shows (ChatDock's chatKey), `openPage` the page
// open beside it ("" for none).
export function createShows() {
  const waiting = new Map(); // a reply's conversation → the page it asked to show
  return {
    action(ev, dockKey, openPage) {
      const show = ev?.action?.kind === "show" && !ev.action.error ? ev.action.show : null;
      if (!show?.page_id || ev.key !== dockKey) return null;
      if (show.page_id === openPage) return { kind: "now", show };
      waiting.set(ev.key, show);
      return { kind: "later", show };
    },
    // A reply's done: its last one says whether it ended on its own
    // (`ended`), a steer's says nothing and keeps what waits.
    done(ev, dockKey) {
      if (ev?.ended === undefined) return null;
      const show = waiting.get(ev.key);
      waiting.delete(ev.key);
      return show && ev.ended && ev.key === dockKey ? { kind: "now", show } : null;
    },
  };
}

// Where a show lands, as the reply's own links land (shared/ui/Widgets.jsx
// GammaNavContext): `{block}` for a note, else `{page, target}`.
export function landing(show) {
  if (show.block_id) return { block: show.block_id, page: show.page_id };
  if (show.line) return { page: show.page_id, target: { kind: "line", pageId: show.page_id, line: show.line } };
  if (show.pdf_page) {
    return { page: show.page_id,
      target: { kind: "citation", pageId: show.page_id, page: show.pdf_page, quote: show.quote || "" } };
  }
  return { page: show.page_id, target: null };
}

export const agentShows = createShows();
