// A zip of `{ name: text }`, its entries stored as they are, for the tests
// that hand the shell an export (zipNames.test.js, main.test.js). The CRCs are
// left at 0: nothing here unpacks it. `comment` goes after the end record, as
// a zip's own comment does.

function zipOf(files, comment = '') {
  const locals = [];
  const entries = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const n = Buffer.from(name);
    const data = Buffer.from(text);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(n.length, 26);
    locals.push(local, n, data);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt32LE(data.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(n.length, 28);
    entry.writeUInt32LE(offset, 42);
    entries.push(entry, n);
    offset += local.length + n.length + data.length;
  }
  const directory = Buffer.concat(entries);
  const c = Buffer.from(comment);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(files).length, 8);
  end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(c.length, 20);
  return Buffer.concat([...locals, directory, end, c]);
}

module.exports = { zipOf };
