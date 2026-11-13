// Runs in every page the shell's views load. Two very different jobs:
//
// - the exact bundled launcher + shell bar, in the top-level frame, get
//   the `gammaShell` IPC bridge.
// - http(s) pages (a server's Gamma frontend) get NOTHING exposed. Two
//   things happen there, both one way, page to shell: the page's
//   `data-theme` attribute is reported so the shell chrome paints in the
//   same theme, and a page's requests about this computer, which the page
//   posts to itself (frontend/src/app/App.jsx), are passed on as they are
//   (PAGE_ASKS): a folder's "Sync with this computer…", a page's "Push
//   from a file on this computer…", its file shown in its folder, its
//   missing file located, an opened PDF carried to another server's
//   library, its computer chat continued in a terminal. The shell checks
//   each and asks the user for the directory, the file or the server, or
//   its own server for the terminal's command. Gamma stays a black box.

const { contextBridge, ipcRenderer } = require('electron');

// The main process supplies the bundled URLs: sandboxed preloads cannot
// resolve filesystem paths. Queries (theme/error) and fragments are harmless.
const pageArgument = process.argv.find((arg) => arg.startsWith('--gamma-shell-pages='));
let shellPages = [];
try { shellPages = JSON.parse(pageArgument.slice('--gamma-shell-pages='.length)); } catch {}
const pageUrl = new URL(window.location.href);
pageUrl.search = '';
pageUrl.hash = '';
if (process.isMainFrame && shellPages.includes(pageUrl.href)) {
  contextBridge.exposeInMainWorld('gammaShell', {
    platform: process.platform,
    state: () => ipcRenderer.invoke('shell:state'),
    list: () => ipcRenderer.invoke('shell:list'),
    addLocal: (name) => ipcRenderer.invoke('shell:add-local', name),
    addRemote: (name, url) => ipcRenderer.invoke('shell:add-remote', name, url),
    rename: (id, name) => ipcRenderer.invoke('shell:rename', id, name),
    setUrl: (id, url) => ipcRenderer.invoke('shell:set-url', id, url),
    remove: (id, opts) => ipcRenderer.invoke('shell:remove', id, opts),
    open: (id) => ipcRenderer.invoke('shell:open', id),
    openWorkspace: (id) => ipcRenderer.invoke('shell:open-workspace', id),
    keepOffline: (id) => ipcRenderer.invoke('shell:keep-offline', id),
    folders: (wsId) => ipcRenderer.invoke('shell:folders', wsId),
    addFolderVault: (wsId, folderId) => ipcRenderer.invoke('shell:add-folder-vault', wsId, folderId),
    addVault: () => ipcRenderer.invoke('shell:add-vault'),
    // The vault dialog's answer: `{ pull, push }` to sync, null to cancel.
    answerVault: (id, answer) => ipcRenderer.invoke('shell:answer-vault', id, answer),
    keeping: () => ipcRenderer.invoke('shell:keeping'),
    keepingAction: (kind, serverId, id, action, value) => ipcRenderer.invoke('shell:keeping-action', kind, serverId, id, action, value),
    openPath: (wsId, p) => ipcRenderer.invoke('shell:open-path', wsId, p),
    openCopy: (id) => ipcRenderer.invoke('shell:open-copy', id),
    openOriginal: (id) => ipcRenderer.invoke('shell:open-original', id),
    launcher: () => ipcRenderer.invoke('shell:launcher'),
    reload: () => ipcRenderer.invoke('shell:reload'),
    revealData: (id) => ipcRenderer.invoke('shell:reveal-data', id),
    revealLog: (id) => ipcRenderer.invoke('shell:reveal-log', id),
    copyAddress: (id) => ipcRenderer.invoke('shell:copy-address', id),
    setSettings: (patch) => ipcRenderer.invoke('shell:set-settings', patch),
    pickFolder: (defaultPath) => ipcRenderer.invoke('shell:pick-folder', defaultPath),
    grantFolder: (id) => ipcRenderer.invoke('shell:grant-folder', id),
    setDataRoot: (dir, opts) => ipcRenderer.invoke('shell:set-data-root', dir, opts),
    barExpand: (on) => ipcRenderer.invoke('shell:bar-expand', on),
    updateCheck: () => ipcRenderer.invoke('shell:update-check'),
    updateInstall: () => ipcRenderer.invoke('shell:update-install'),
    onState: (cb) => {
      const handler = (_e, state) => cb(state);
      ipcRenderer.on('shell:state', handler);
      return () => ipcRenderer.removeListener('shell:state', handler);
    },
    // A storage-folder move's progress ({ name, done, total } bytes; null once it ends).
    onDataMove: (cb) => {
      const handler = (_e, move) => cb(move);
      ipcRenderer.on('shell:data-move', handler);
      return () => ipcRenderer.removeListener('shell:data-move', handler);
    },
  });
} else if (/^https?:$/.test(window.location.protocol)) {
  const report = () => {
    const t = document.documentElement.getAttribute('data-theme') || '';
    ipcRenderer.send('shell:theme', t);
  };
  const start = () => {
    report();
    new MutationObserver(report).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
  };
  if (document.documentElement) start();
  else document.addEventListener('DOMContentLoaded', start, { once: true });
  // Message type -> [channel, the id it names beside the workspace]. The
  // names before synced vaults stay, for a page of an older server opened
  // in this app.
  const PAGE_ASKS = {
    'sync-folder-on-disk': ['shell:sync-folder-from-page', 'folder'],
    'keep-folder-on-disk': ['shell:sync-folder-from-page', 'folder'],
    'push-file-on-disk': ['shell:push-file-from-page', 'page'],
    'follow-file-on-disk': ['shell:push-file-from-page', 'page'],
    'reveal-on-disk': ['shell:reveal-from-page', 'page'],
    'relocate-file': ['shell:relocate-from-page', 'page'],
    'carry-to-server': ['shell:carry-from-page', 'page'],
    'continue-in-terminal': ['shell:terminal-from-page', 'page'],
  };
  window.addEventListener('message', (e) => {
    const d = e.data;
    if (e.source !== window || e.origin !== window.location.origin || !d || d.source !== 'gamma-app') return;
    const ask = Object.prototype.hasOwnProperty.call(PAGE_ASKS, d.type) ? PAGE_ASKS[d.type] : null;
    if (ask) ipcRenderer.send(ask[0], String(d.ws || ''), String(d[ask[1]] || ''));
  });
}
