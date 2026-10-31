// Folders outside the Mac App Store app's sandbox (docs/release.md "Mac App
// Store"). A sandboxed app reads and writes its own container, plus what
// the user picks in a native dialog, and that only until it quits. The
// dialog can also hand back a security-scoped bookmark of the folder
// (`securityScopedBookmarks`); `app.startAccessingSecurityScopedResource`
// opens the same folder again on a later run. The bookmarks are kept in
// the registry (`access`: { path, bookmark }), and every one is opened at
// launch, before any local server starts. A PDF a page follows (main.js
// followFile) is picked and kept the same way, a file in place of a folder.
//
// The local servers are child processes that inherit the app's sandbox,
// together with the folders the app holds open at the moment they start;
// a folder opened later does not reach a server already running. So each
// grant has a number (`since`), a running server remembers the number it
// started at (`appInfo().access` → the sidecar's entry), and `grantedSince`
// says when a server must start again to reach a folder: main.js
// reachFolder, after the user picks where to keep a folder.
//
// A bookmark that no longer opens (the folder moved or was deleted, the
// disk is not connected) leaves its folder closed for this run and stays
// stored, so a reconnected disk works again at the next launch; picking
// the folder again replaces it. No Electron in here: `start` is the app's
// startAccessingSecurityScopedResource. Unit tested by
// test/folderAccess.test.js (`npm test`).

const fs = require('fs');
const path = require('path');

function within(dir, root) {
  const d = path.resolve(dir);
  const r = path.resolve(root);
  return d === r || d.startsWith(r + path.sep);
}

// `load`/`save` read and write the stored list; `container` is the app's
// own data folder, which needs no grant.
function create({ load, save, start, container }) {
  const held = new Map(); // folder -> { stop, since, kept }
  let count = 0;

  const covering = (dir) => [...held].filter(([root]) => within(dir, root)).map(([, h]) => h);

  function hold(dir, h) {
    if (held.has(dir)) held.get(dir).stop();
    held.set(dir, h);
  }

  return {
    // At launch: open every stored folder, and every followed file. A
    // bookmark that resolves somewhere else (the folder moved) does not
    // open the stored path.
    restore() {
      for (const g of load()) {
        let stop = null;
        try {
          stop = start(g.bookmark);
          if (fs.statSync(g.path).isDirectory()) fs.readdirSync(g.path);
          else fs.accessSync(g.path, fs.constants.R_OK);
          hold(g.path, { stop, since: 0, kept: true });
        } catch (e) {
          if (stop) stop();
          console.error(`[shell] no access to ${g.path} this run: ${e.message || e}`);
        }
      }
    },

    // A folder the user just picked. The dialog's own grant lasts this run;
    // the bookmark keeps it for the next ones, and the folders the new one
    // contains need no bookmark of their own any more. A folder inside one
    // already kept needs nothing.
    add(dir, bookmark) {
      if (within(dir, container) || covering(dir).some((h) => h.kept)) return;
      let stop = null;
      try {
        if (bookmark) stop = start(bookmark);
      } catch (e) {
        console.error(`[shell] could not keep access to ${dir}: ${e.message || e}`);
      }
      hold(dir, { stop: stop || (() => {}), since: ++count, kept: Boolean(stop) });
      if (!stop) return; // reachable this run through the dialog's grant, asked for again next time
      for (const [root, h] of held) {
        if (root !== dir && within(root, dir)) {
          h.stop();
          held.delete(root);
        }
      }
      save([...load().filter((g) => !within(g.path, dir)), { path: dir, bookmark }]);
    },

    // The number a server starting now remembers.
    generation: () => count,

    reachable: (dir) => within(dir, container) || covering(dir).length > 0,

    // Whether a server that started at `since` cannot reach `dir`, but
    // would if it started again: every grant around `dir` is newer.
    grantedSince(dir, since) {
      if (within(dir, container)) return false;
      const around = covering(dir);
      return around.length > 0 && around.every((h) => h.since > (since || 0));
    },
  };
}

module.exports = { create };
