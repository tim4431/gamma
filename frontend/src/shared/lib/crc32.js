// CRC-32 as zlib computes it, the checksum a .zip entry carries
// (transfers/folderZip.js) and a PNG chunk ends with (the pencil's grain
// tile, ink/grain.js). Pure.
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
