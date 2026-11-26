// Content script: answers a Gamma chat card (frontend
// chat/FetchHandoffCards.jsx) that asks whether this browser's Connector can
// fetch for its request — {source: "gamma-app", type: "connector-probe", id}
// — or asks it to open, show or close the request's tab — {type:
// "connector-tab", id, do: "open" | "show" | "close", background}. It also
// answers the app's "is the Connector here?" (type: "connector-hello", no
// id), which only its own server's app hears — Gamma uses it to stop
// suggesting an extension the browser already has. The worker checks the
// request with the Connector's own Gamma server and decides what may be
// said — nothing at all to a page that is neither that server's app nor
// holding a request of its account, so other sites learn nothing.
//
// The other way, the worker's "Open in Gamma" in a Gamma tab already open
// (worker.js openApp, {type: "app-open", url}) is posted to the app as
// {source: "gamma-connector", type: "app-open", id, url}; the app's
// {source: "gamma-app", type: "app-opened", id} says it opened the link,
// and no word within a second answers false, so the worker loads it.
(() => {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.type !== "app-open") return undefined;
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const done = (opened) => {
      window.removeEventListener("message", onReply);
      clearTimeout(timer);
      sendResponse(opened);
    };
    const onReply = (event) => {
      const data = event.data;
      if (event.source === window && event.origin === location.origin && data
          && data.source === "gamma-app" && data.type === "app-opened" && data.id === id) done(true);
    };
    const timer = setTimeout(() => done(false), 1000);
    window.addEventListener("message", onReply);
    window.postMessage({ source: "gamma-connector", type: "app-open", id, url: String(msg.url || "") }, location.origin);
    return true; // async sendResponse
  });

  const REPLIES = { "connector-probe": "connector-status", "connector-tab": "connector-tab",
                    "connector-hello": "connector-here" };
  const asked = new Map(); // request id + question → when it was last asked
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (event.source !== window || event.origin !== location.origin || !data
        || data.source !== "gamma-app" || !REPLIES[data.type]) return;
    const id = String(data.id || "");
    const act = data.type === "connector-tab" ? String(data.do || "") : "";
    if (data.type === "connector-tab" && !["open", "show", "close"].includes(act)) return;
    // One question a second per request, and a few requests at a time.
    const key = `${id} ${data.type} ${act}`;
    if (Date.now() - (asked.get(key) || 0) < 1000 || asked.size > 150) return;
    asked.set(key, Date.now());
    const msg = act ? { type: data.type, id, do: act, background: !!data.background } : { type: data.type, id };
    chrome.runtime.sendMessage(msg).then((reply) => {
      const result = reply && reply.ok ? reply.result : null;
      if (result) {
        window.postMessage({ source: "gamma-connector", type: REPLIES[data.type], id: data.id,
                             ...(act ? { do: act } : {}), ...result }, location.origin);
      }
    }).catch(() => {});
  });
})();
