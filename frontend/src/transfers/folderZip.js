// A picked or dropped folder as one .zip File, for the imports that read an
// archive: the server's review takes the same upload either way
// (docs/dev/import_export.md). Entries are stored, not compressed — PDFs
// barely shrink, so packing is one CRC pass and the zip is a Blob over the
// picked files rather than a copy of them — under the folder's own name
// with UTF-8 names, as Explorer's or Finder's Compress writes it. Neither a
// pick nor a drop here lists empty folders. Past 4 GB or 65,535 files the
// sizes, offsets and counts that no longer fit go in ZIP64 records.
const MAX32 = 0xFFFFFFFF;

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

// CRC-32 continued over `bytes`; start from 0, as zlib's crc32 does.
export function crc32(bytes, crc = 0) {
  let c = ~crc >>> 0;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return ~c >>> 0;
}

// Safari on an iPhone or iPad has no folder picker (it would pick files).
export function canPickFolder() {
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  return !(/iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1));
}

// What the import dialog shows for a folder before it is packed: its name,
// its size and its files, `entries` of {path, file} with the path from the
// folder's own name down.
export function folderUpload(entries) {
  const first = entries[0]?.path || "";
  const name = first.includes("/") ? first.slice(0, first.indexOf("/")) : "folder";
  return { name, size: entries.reduce((sum, { file }) => sum + file.size, 0), entries, folder: true };
}

// A folder picked with pickFiles({ directory: true }): each file carries its
// path as webkitRelativePath.
export const folderPick = (fileList) => folderUpload([...fileList].map((file) => ({ path: file.webkitRelativePath || file.name, file })));

// A folder dropped as one entry (dropEntries.js collectEntryFiles' pairs).
export const folderDrop = (found) => folderUpload(found.map(({ file, folder }) => ({ path: [...folder, file.name].join("/"), file })));

function dosStamp(ms) {
  const d = new Date(ms || Date.now());
  const year = Math.min(Math.max(d.getFullYear(), 1980), 2107);
  return [(d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
    ((year - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate()];
}

function record(size, fields) {
  const bytes = new Uint8Array(size);
  const view = new DataView(bytes.buffer);
  let at = 0;
  for (const [width, value] of fields) {
    if (width === 2) view.setUint16(at, value, true);
    else if (width === 4) view.setUint32(at, value, true);
    else view.setBigUint64(at, BigInt(value), true);
    at += width;
  }
  return bytes;
}

// The ZIP64 extra field carrying `values` (8 bytes each), or none.
const zip64Extra = (values) => (values.length
  ? record(4 + 8 * values.length, [[2, 0x0001], [2, 8 * values.length], ...values.map((value) => [8, value])])
  : new Uint8Array());

const join = (...arrays) => {
  const out = new Uint8Array(arrays.reduce((sum, a) => sum + a.length, 0));
  let at = 0;
  for (const a of arrays) { out.set(a, at); at += a.length; }
  return out;
};

// → a File `<folder>.zip`. `onProgress(read, total)` while the files are read.
export async function zipFolder(pick, { signal, onProgress } = {}) {
  const encoder = new TextEncoder();
  const parts = [], central = [];
  let offset = 0, read = 0;
  for (const { path, file } of pick.entries) {
    const name = encoder.encode(path);
    let crc = 0;
    const reader = file.stream().getReader();
    for (;;) {
      if (signal?.aborted) { reader.cancel(); const err = new Error("stopped"); err.aborted = true; err.name = "AbortError"; throw err; }
      const { done, value } = await reader.read();
      if (done) break;
      crc = crc32(value, crc);
      read += value.length;
      onProgress?.(read, pick.size);
    }
    const [time, date] = dosStamp(file.lastModified);
    // A size or offset of 4 GB or more reads 0xFFFFFFFF here, the real value
    // in a ZIP64 extra field (version 4.5): the local header's holds both
    // sizes, the central record's the sizes and offset that overflowed.
    const bigFile = file.size >= MAX32, bigOffset = offset >= MAX32;
    const size32 = bigFile ? MAX32 : file.size;
    const version = bigFile || bigOffset ? 45 : 20;
    const localExtra = zip64Extra(bigFile ? [file.size, file.size] : []);
    const centralExtra = zip64Extra([...(bigFile ? [file.size, file.size] : []), ...(bigOffset ? [offset] : [])]);
    // flag 0x800: the name is UTF-8; method 0: stored
    const common = [[2, version], [2, 0x800], [2, 0], [2, time], [2, date], [4, crc], [4, size32], [4, size32], [2, name.length]];
    const local = join(record(30, [[4, 0x04034B50], ...common, [2, localExtra.length]]), name, localExtra);
    central.push(join(record(46, [[4, 0x02014B50], [2, version], ...common, [2, centralExtra.length], [2, 0], [2, 0], [2, 0], [4, 0],
      [4, bigOffset ? MAX32 : offset]]), name, centralExtra));
    parts.push(local, file);
    offset += local.length + file.size;
  }
  const size = central.reduce((sum, entry) => sum + entry.length, 0);
  const count = central.length;
  const tail = [];
  if (count >= 0xFFFF || size >= MAX32 || offset >= MAX32) {
    // the ZIP64 end record, right after the central directory, then its locator
    tail.push(record(56, [[4, 0x06064B50], [8, 44], [2, 45], [2, 45], [4, 0], [4, 0], [8, count], [8, count], [8, size], [8, offset]]),
      record(20, [[4, 0x07064B50], [4, 0], [8, offset + size], [4, 1]]));
  }
  tail.push(record(22, [[4, 0x06054B50], [2, 0], [2, 0], [2, Math.min(count, 0xFFFF)], [2, Math.min(count, 0xFFFF)],
    [4, Math.min(size, MAX32)], [4, Math.min(offset, MAX32)], [2, 0]]));
  return new File([...parts, ...central, ...tail], `${pick.name}.zip`, { type: "application/zip" });
}
