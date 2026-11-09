// The names of the entries a zip holds, read from its central directory: what
// the shell looks for in a Gamma export before it takes it to another server
// (main.js carryPage), the bytes of the page's PDF among them. Pure. A file
// that is no zip, or a damaged one, holds nothing.

const EOCD = 0x06054b50; // end of central directory
const ZIP64_LOCATOR = 0x07064b50;
const ZIP64_EOCD = 0x06064b50;
const ENTRY = 0x02014b50; // a central directory entry
const MAX_COMMENT = 0xffff;

// The end record: the last one, past which only its comment may lie.
function endRecord(buf) {
  const floor = Math.max(0, buf.length - 22 - MAX_COMMENT);
  for (let at = buf.length - 22; at >= floor; at--) {
    if (buf.readUInt32LE(at) === EOCD) return at;
  }
  return -1;
}

// `{ count, offset }` of the central directory, from the end record or, past
// what its fields hold (a zip of 4 GB or of 65,535 entries), from the zip64 one.
function directory(buf) {
  const end = endRecord(buf);
  if (end < 0) return null;
  let count = buf.readUInt16LE(end + 10);
  let offset = buf.readUInt32LE(end + 16);
  if ((count === 0xffff || offset === 0xffffffff) && end >= 20 && buf.readUInt32LE(end - 20) === ZIP64_LOCATOR) {
    const at = Number(buf.readBigUInt64LE(end - 20 + 8));
    if (at + 56 > buf.length || buf.readUInt32LE(at) !== ZIP64_EOCD) return null;
    count = Number(buf.readBigUInt64LE(at + 32));
    offset = Number(buf.readBigUInt64LE(at + 48));
  }
  return { count, offset };
}

function zipNames(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 22) return [];
  const dir = directory(buf);
  if (!dir) return [];
  const names = [];
  let at = dir.offset;
  for (let i = 0; i < dir.count; i++) {
    if (at + 46 > buf.length || buf.readUInt32LE(at) !== ENTRY) return [];
    const nameLength = buf.readUInt16LE(at + 28);
    const next = at + 46 + nameLength + buf.readUInt16LE(at + 30) + buf.readUInt16LE(at + 32);
    if (next > buf.length) return [];
    names.push(buf.toString('utf8', at + 46, at + 46 + nameLength));
    at = next;
  }
  return names;
}

module.exports = { zipNames };
