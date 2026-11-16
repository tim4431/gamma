// The names of the entries a .zip File holds, read from its central directory
// without unpacking anything (the desktop reads a Buffer the same way:
// desktop/lib/zipNames.js): what the import dialog guesses a .zip a native
// app was given from (transferFormats.js archiveSource). A file that is no
// zip, a damaged one, or one whose directory is past MAX_DIRECTORY holds
// nothing; so does any File that cannot be read.

const EOCD = 0x06054B50; // end of central directory
const ZIP64_LOCATOR = 0x07064B50;
const ZIP64_EOCD = 0x06064B50;
const ENTRY = 0x02014B50; // a central directory entry
const MAX32 = 0xFFFFFFFF;
const MAX_COMMENT = 0xFFFF;
// Far above a vault of 20,000 notes (about 100 bytes an entry).
const MAX_DIRECTORY = 16 << 20;

const bytesOf = async (file, start, end) => new DataView(await file.slice(start, end).arrayBuffer());

export async function zipEntryNames(file) {
  try {
    if (!file || file.size < 22) return [];
    // The end record is the last one, past which only its comment may lie;
    // the zip64 locator sits right before it.
    const from = Math.max(0, file.size - 22 - MAX_COMMENT - 20);
    const tail = await bytesOf(file, from, file.size);
    let end = -1;
    for (let at = tail.byteLength - 22; at >= 0; at--) {
      if (tail.getUint32(at, true) === EOCD) { end = at; break; }
    }
    if (end < 0) return [];
    let count = tail.getUint16(end + 10, true);
    let size = tail.getUint32(end + 12, true);
    let offset = tail.getUint32(end + 16, true);
    if ((count === 0xFFFF || size === MAX32 || offset === MAX32) && end >= 20
      && tail.getUint32(end - 20, true) === ZIP64_LOCATOR) {
      const at = Number(tail.getBigUint64(end - 20 + 8, true));
      const record = await bytesOf(file, at, at + 56);
      if (record.byteLength < 56 || record.getUint32(0, true) !== ZIP64_EOCD) return [];
      count = Number(record.getBigUint64(32, true));
      size = Number(record.getBigUint64(40, true));
      offset = Number(record.getBigUint64(48, true));
    }
    if (size > MAX_DIRECTORY || offset + size > file.size) return [];
    const dir = await bytesOf(file, offset, offset + size);
    const decoder = new TextDecoder();
    const names = [];
    let at = 0;
    for (let i = 0; i < count; i++) {
      if (at + 46 > dir.byteLength || dir.getUint32(at, true) !== ENTRY) return [];
      const nameLength = dir.getUint16(at + 28, true);
      const next = at + 46 + nameLength + dir.getUint16(at + 30, true) + dir.getUint16(at + 32, true);
      if (next > dir.byteLength) return [];
      names.push(decoder.decode(new Uint8Array(dir.buffer, dir.byteOffset + at + 46, nameLength)));
      at = next;
    }
    return names;
  } catch {
    return [];
  }
}
