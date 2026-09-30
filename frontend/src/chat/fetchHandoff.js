// Fetches handed to the user's browser (gamma/fetch_handoff.py): when the
// agent's fetch_paper meets a CAPTCHA, a sign-in page or a paywall, the
// chat shows a card for it (FetchHandoffCards.jsx). The rules here are the
// card's pure part: which requests a reply opened, what each one's state
// reads as, and how often to ask the server.
//
// A card appears twice over, from the same component:
//
// - live, while the reply runs: the server sent a {"handoff"} line and is
//   waiting on this card (gamma/ai_agent.PaperWait), so the PDF becomes the
//   fetch's own result. Skip ends the wait, and can say what to do instead.
// - under a finished reply, for a request that outlived it (the wait gave
//   up, or the chat was told not to wait): the action carries the
//   `handoff`, and Continue asks the chat to go on once the PDF arrives.
import { t, tn } from "../shared/i18n/i18n.js";

// The requests a reply's tool calls opened, once each, in call order (a
// model retrying a blocked source gets the same request back). A request the
// reply already settled carries `settled`, so its card shows that outcome
// without asking the server about a request nobody is waiting for.
export function replyHandoffs(actions) {
  const seen = new Map();
  for (const a of actions || []) {
    const h = a?.handoff;
    if (h?.id && !seen.has(h.id)) {
      seen.set(h.id, a.skipped ? { ...h, settled: "dismissed" } : h);
    }
  }
  return [...seen.values()];
}

// The card's state from the server's view of the request (null while the
// first answer is out) and whether the user opened the page from it. A tab
// the user closed before the PDF came (note "closed") is watched no more.
export function handoffState(view, { opened = false } = {}) {
  if (!view) return "loading";
  if (view.status === "done") return "done";
  if (view.status === "dismissed") return "dismissed";
  if (view.status !== "waiting") return "gone"; // expired, or unknown to the server
  if (view.watched && view.note !== "closed") return "watching";
  return opened ? "opened" : "waiting";
}

// How "Open" reaches the publisher: Gamma Connector opens the tab itself
// when it answered the card ("connector" — no Gamma page on the way); with
// no Connector to take the tab the page opens directly ("direct"); the
// desktop app (the system browser's Connector knows the tab by its address)
// and a Connector that has not answered yet go through the request's /go
// page ("go").
export function openRoute(connector) {
  if (connector === "ok") return "connector";
  if (connector === "desktop" || connector === "unknown") return "go";
  return "direct";
}

// Whether the card hands its request to the Connector by itself, to fetch
// out of sight (the "Fetch blocked papers in the background" setting): the
// Connector can serve it, the request waits untouched in the conversation's
// last reply, and no tab of it was closed before.
export function autoOpens({ auto, connector, state, isLast, readOnly, note = "" }) {
  return !!auto && connector === "ok" && state === "waiting" && !!isLast && !readOnly && !note;
}

// What the Connector reports that waits for the user in the tab: the card
// then offers to show it.
export const NEEDS_YOU = new Set(["signin", "refused", "looking", "check", "other"]);

export const SETTLED = new Set(["done", "dismissed", "gone"]);
const ACTIVE = new Set(["opened", "watching"]);

// Milliseconds until the card asks the server again (null: it stops). Often
// while the user is at the page; now and then while the request waits in
// the conversation's last reply, so a Connector delivery shows up; older
// replies ask only when the window regains focus.
export function pollDelay(state, { isLast = true } = {}) {
  if (SETTLED.has(state)) return null;
  if (ACTIVE.has(state)) return 2500;
  return isLast ? 10000 : null;
}

// Whether the card offers to continue the conversation with what arrived:
// the reply is the conversation's last, it is not running, and every
// request it left behind has settled with at least one PDF delivered. The
// chat never sends that by itself — a message in the user's name is the
// user's to send.
export function canContinue({ states, isLast, idle }) {
  if (!isLast || !idle || !states.length) return false;
  return states.every((s) => SETTLED.has(s)) && states.includes("done");
}

// The card's headline: who stopped the server, and how.
export function wallHeadline(wall, host) {
  switch (wall) {
    case "captcha": return t("{host} asked for a CAPTCHA or bot check", { host });
    case "login": return t("{host} asked to sign in", { host });
    case "rate": return t("{host} is limiting Gamma's requests", { host });
    case "abstract": return t("{host} showed only the article page", { host });
    case "script": return t("{host} only shows the paper in a browser", { host });
    default: return t("{host} refused Gamma's download", { host });
  }
}

// What the user does next, per state. `background`: the Connector keeps the
// tab out of sight; `queued`: it waits for a turn to open one; `live`: the
// reply is held open on this card, so what arrives lands in the answer.
export function handoffHint(state, { pages = 0, background = false, queued = false, live = false } = {}) {
  switch (state) {
    case "watching":
      return background
        ? t("Gamma Connector is getting it in a minimized window — the PDF comes back here by itself.")
        : t("Gamma Connector is watching the tab. Sign in or pass the check there — the PDF comes back here by itself.");
    case "opened":
      return queued
        ? t("Gamma Connector gets it in the background once the papers before it are done.")
        : t("Finish in the tab that opened.");
    case "done":
      return tn("Got the PDF from your browser ({n} page).", "Got the PDF from your browser ({n} pages).", pages);
    case "dismissed":
      return live ? t("Skipped — the reply carries on without it.") : t("Dismissed.");
    case "gone":
      return t("This request has expired — ask the chat again.");
    default:
      return live
        ? t("The reply is waiting for this. Open the page and sign in or pass the check there.")
        : t("Open the page and sign in or pass the check there.");
  }
}

// Whether the PDF can come back by itself: Gamma Connector's answer to the
// card's probe (the extension's bridge.js asks the Connector's own server
// about the request) — "ok", "signed-out", "other-account", "unreachable" —
// or "missing" when none came (not in this browser, a version from before
// chat fetching, or set to another server); "desktop" in the desktop app,
// which opens pages in the system browser; "unknown" while it asks.
export function connectorNote(connector, { origin = "" } = {}) {
  switch (connector) {
    case "ok":
      return t("Gamma Connector sends the PDF back from that tab by itself.");
    case "signed-out":
      return t("Gamma Connector is signed out of this server: sign in from its toolbar button, then open the page again — or download the PDF and drop it here.");
    case "other-account":
      return t("Gamma Connector is signed in to another account here: sign in as you, then open the page again — or download the PDF and drop it here.");
    case "unreachable":
      return t("Gamma Connector can't reach this server right now. Download the PDF and drop it here.");
    case "desktop":
      return t("The page opens in your default browser: Gamma Connector there, set to {origin}, sends the PDF back by itself. Or download the PDF and drop it here.", { origin });
    case "missing":
      return t("Gamma Connector isn't answering in this browser — install it, or reload it after an update, and set it to {origin}. Or download the PDF and drop it here.", { origin });
    default:
      return "";
  }
}

// What Gamma Connector reports doing in the watched tab (the request's
// `note`, set by the extension's harvest): "" says nothing more.
export function watchNote(note) {
  switch (note) {
    case "looking":
      return t("It doesn't see the paper's PDF link on that page yet — sign in if needed, or open the PDF there.");
    case "check":
      return t("The site is showing a bot check or CAPTCHA: pass it in that tab, and it takes the PDF after.");
    case "closed":
      return t("The tab closed before the PDF came. Open the page again, or drop the PDF here.");
    case "other":
      return t("That tab shows a paper with another DOI, so it waits. Open this paper there, or drop its PDF here.");
    case "signin":
      return t("The PDF link there leads to a sign-in: sign in on that page (or through your institution), and it takes the PDF once you are in.");
    case "opening":
      return t("It is opening the PDF in that tab.");
    case "refused":
      return t("The site didn't hand it the PDF. Open the PDF there, save it, and drop it here.");
    default:
      return "";
  }
}

// The message the chat continues with, naming what arrived.
export function continuePrompt(handoffs) {
  const what = handoffs.map((h) => h.source || h.host).filter(Boolean).join(", ");
  return t("I got it in my browser — {what} is available now. Please continue.", { what });
}
