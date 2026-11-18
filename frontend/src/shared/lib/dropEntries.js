// Recursively walk directory entries from a drop into {file, folder} pairs,
// `folder` the directory names down to the file. The entries themselves
// must be captured synchronously in the drop handler (webkitGetAsEntry) —
// the DataTransfer is neutered once the event returns. Shared by the
// library's folder drop (App.jsx) and the import dialog's (ImportExport.jsx).
//
// `skip(name)`: a directory not gone into (a project's .git or
// node_modules, which the library's drop leaves out anyway); each one is
// pushed to `left` as {folder, name}, `folder` the names above it.
// `onProgress(n)` hears the count of files found, every 200, since a
// large tree takes a while to read.
export async function collectEntryFiles(entries, { skip = null, left = null, onProgress = null } = {}) {
  const out = [];
  async function walk(entry, folder) {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej)).catch(() => null);
      if (file) {
        out.push({ file, folder });
        if (onProgress && out.length % 200 === 0) onProgress(out.length);
      }
    } else if (entry.isDirectory) {
      const name = entry.name.trim();
      if (name && skip?.(name)) { left?.push({ folder, name }); return; }
      const sub = name ? [...folder, name] : folder;
      const reader = entry.createReader();
      for (;;) { // readEntries returns ≤100 entries per call — drain until empty
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej)).catch(() => null);
        if (!batch || !batch.length) break;
        for (const e of batch) await walk(e, sub);
      }
    }
  }
  for (const entry of entries) await walk(entry, []);
  return out;
}
