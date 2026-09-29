// Content script: answers a Gamma chat card that asks whether this browser's
// Connector can fetch for its request (frontend chat/FetchHandoffCards.jsx).
// The page posts {source: "gamma-app", type: "connector-probe", id}; the
// worker checks the request with the Connector's own Gamma server and decides
// what may be said — nothing at all to a page that is neither that server's
// app nor holding a request of its account, so other sites learn nothing.
(() => {
  const asked = new Map(); // request id → when it was last asked about
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (event.source !== window || event.origin !== location.origin || !data
        || data.source !== "gamma-app" || data.type !== "connector-probe") return;
    // One question a second per request, and a few requests at a time.
    const id = String(data.id || "");
    if (Date.now() - (asked.get(id) || 0) < 1000 || asked.size > 50) return;
    asked.set(id, Date.now());
    chrome.runtime.sendMessage({ type: "connector-probe", id }).then((reply) => {
      const result = reply && reply.ok ? reply.result : null;
      if (result) window.postMessage({ source: "gamma-connector", type: "connector-status", id: data.id, ...result }, location.origin);
    }).catch(() => {});
  });
})();
