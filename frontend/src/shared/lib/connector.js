// Is Gamma Connector in this browser? The extension's content script
// (extension/bridge.js) answers a "connector-hello" posted on the Gamma
// page's own origin, and its worker answers only the app of the server the
// Connector is set to — so an answer means a Connector that can work with
// this Gamma, not merely some extension.
//
// Used to stop suggesting the extension to a browser that has it
// (guide/tours/hints.js). The handoff cards ask their own per-request
// question instead (chat/FetchHandoffCards.jsx): what they need to know is
// whether the Connector may fetch for THAT request.
//
// The content script loads after the page, so the question is asked a few
// times before no answer counts as "not there". Resolves true / false.
//
// The desktop app is a window of its own: it opens links in the system
// browser, where a Connector may well be, and no content script reaches
// this page — so nothing here can tell, and callers treat it as unknown.
export const IS_DESKTOP = typeof navigator !== "undefined" && /\bElectron\//.test(navigator.userAgent);

export function askConnectorHere({ tries = [0, 1500, 3000], timeout = 4500 } = {}) {
  return new Promise((resolve) => {
    if (typeof window === "undefined") { resolve(false); return; }
    const origin = window.location.origin;
    const done = (here) => {
      window.removeEventListener("message", onMessage);
      timers.forEach(clearTimeout);
      resolve(here);
    };
    const onMessage = (e) => {
      const d = e.data;
      if (e.source !== window || e.origin !== origin || d?.source !== "gamma-connector"
          || d.type !== "connector-here") return;
      done(true);
    };
    window.addEventListener("message", onMessage);
    const ask = () => window.postMessage({ source: "gamma-app", type: "connector-hello" }, origin);
    const timers = tries.map((ms) => setTimeout(ask, ms));
    timers.push(setTimeout(() => done(false), timeout));
  });
}
