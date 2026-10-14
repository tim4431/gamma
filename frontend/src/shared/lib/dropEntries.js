// Recursively walk directory entries from a drop into {file, folder} pairs,
// `folder` the directory names down to the file. The entries themselves
// must be captured synchronously in the drop handler (webkitGetAsEntry) —
// the DataTransfer is neutered once the event returns. Shared by the
// library's folder drop (App.jsx) and the import dialog's (ImportExport.jsx).
export async function collectEntryFiles(entries) {
  const out = [];
  async function walk(entry, folder) {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej)).catch(() => null);
      if (file) out.push({ file, folder });
    } else if (entry.isDirectory) {
      const sub = entry.name.trim() ? [...folder, entry.name.trim()] : folder;
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
