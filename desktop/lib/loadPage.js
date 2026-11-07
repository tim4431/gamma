// webContents.loadURL without its false alarms. Its promise takes any
// did-stop-loading or did-navigate-in-page for the end of its load, and
// something else can send those: the load before it, still finishing, or the
// page being left, which writes its workspace or page into the address as it
// boots (history.replaceState). A load starting just then goes on and lands,
// but is rejected as ERR_FAILED, or resolved before it has begun, and the load
// after that cancels it and is rejected for it. A second load right after the
// first (a server, then its workspace) hit this in most runs, with a "Could not
// open the clone" box over a clone that had opened.
//
// A load cancelled outright is rejected just the same, with no did-fail-load, so
// only an ending with one of those two about is the false alarm, and never one
// after the shell's redirect guard refused the load (main.js, listening first:
// its preventDefault shows here). Then this waits for how the load really ends.

const WAIT_MS = 60_000; // a load that ends in no page sends no event of its own

const failure = (errno, code, url) => Object.assign(new Error(`${code} (${errno}) loading '${url}'`), { errno, code, url });
const sameUrl = (a, b) => {
  try {
    return new URL(a).href === new URL(b).href;
  } catch {
    return a === b;
  }
};

// The load before this one, cancelled by it once it had a document: loadURL
// takes that did-fail-load for its own, and rejects as the page lands.
const notThisLoad = (errno, failedUrl, url) => errno === -3 && Boolean(failedUrl) && !sameUrl(failedUrl, url);

function landing(webContents, url, alarm) {
  let forget;
  const landed = new Promise((resolve, reject) => {
    const failed = (_event, errno, code, failedUrl, isMainFrame) => {
      if (isMainFrame && !notThisLoad(errno, failedUrl, url)) reject(failure(errno, code, failedUrl));
    };
    // loadURL's own watch for a newer load went with its rejection.
    const started = (_event, startedUrl, isSameDocument, isMainFrame) => {
      if (isMainFrame && !isSameDocument && !sameUrl(startedUrl, url)) reject(failure(-3, 'ERR_ABORTED', url));
    };
    const refused = (event) => { if (event.defaultPrevented) reject(alarm); };
    const gone = () => reject(alarm);
    const timer = setTimeout(reject, WAIT_MS, alarm);
    webContents.on('did-finish-load', resolve);
    webContents.on('did-fail-load', failed);
    webContents.on('did-start-navigation', started);
    webContents.on('will-redirect', refused);
    webContents.on('destroyed', gone);
    forget = () => {
      clearTimeout(timer);
      webContents.removeListener('did-finish-load', resolve);
      webContents.removeListener('did-fail-load', failed);
      webContents.removeListener('did-start-navigation', started);
      webContents.removeListener('will-redirect', refused);
      webContents.removeListener('destroyed', gone);
    };
  });
  return landed.finally(forget);
}

async function loadPage(webContents, url, options) {
  let overlapped = webContents.isLoading();
  let failed = false; // a real failure said so, or the guard refused the load
  let finished = false;
  const inPage = (_event, _url, isMainFrame) => { overlapped ||= isMainFrame; };
  const fail = (_event, errno, _code, failedUrl, isMainFrame) => { failed ||= isMainFrame && !notThisLoad(errno, failedUrl, url); };
  const refuse = (event) => { failed ||= event.defaultPrevented; };
  const finish = () => { finished = true; };
  webContents.on('did-navigate-in-page', inPage);
  webContents.on('did-fail-load', fail);
  webContents.on('will-redirect', refuse);
  webContents.on('did-finish-load', finish);
  try {
    await webContents.loadURL(url, options);
    // Resolved by the address the page wrote, unless that was the load itself.
    if (overlapped && !finished && !failed && !sameUrl(webContents.getURL(), url)) {
      await landing(webContents, url, new Error(`No page after loading '${url}'`));
    }
  } catch (e) {
    const before = !e.code && notThisLoad(e.errno, e.url, url); // loadURL's own -3, for a newer load, names its code
    if (before && finished) return;
    if (failed || webContents.isDestroyed() || (!before && (e.errno !== -2 || !overlapped))) throw e;
    await landing(webContents, url, e);
  } finally {
    webContents.removeListener('did-navigate-in-page', inPage);
    webContents.removeListener('did-fail-load', fail);
    webContents.removeListener('will-redirect', refuse);
    webContents.removeListener('did-finish-load', finish);
  }
}

module.exports = { loadPage, WAIT_MS };
