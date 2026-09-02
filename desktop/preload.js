// Bridge for the launcher page ONLY. This preload runs on every page the
// window loads (including remote workspaces), so the API is exposed strictly
// on file: URLs — a workspace server never sees window.gammaShell.

const { contextBridge, ipcRenderer } = require('electron');

if (window.location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('gammaShell', {
    list: () => ipcRenderer.invoke('shell:list'),
    addLocal: (name) => ipcRenderer.invoke('shell:add-local', name),
    addRemote: (name, url) => ipcRenderer.invoke('shell:add-remote', name, url),
    remove: (id, opts) => ipcRenderer.invoke('shell:remove', id, opts),
    open: (id) => ipcRenderer.invoke('shell:open', id),
    revealData: (id) => ipcRenderer.invoke('shell:reveal-data', id),
    setSettings: (patch) => ipcRenderer.invoke('shell:set-settings', patch),
  });
}
