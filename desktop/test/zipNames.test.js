// lib/zipNames.js: the names a zip's central directory lists, and none for
// what is no zip. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const { zipNames } = require('../lib/zipNames');
const { zipOf } = require('./zipOf');

test('a zip lists its entries, a comment after its end record or not', () => {
  const files = { 'manifest.json': '{}', 'pages.db': 'x'.repeat(300), 'uploads/d1.pdf': '%PDF-1.7', 'uploads/ünï.png': 'p' };
  assert.deepEqual(zipNames(zipOf(files)), Object.keys(files));
  assert.deepEqual(zipNames(zipOf(files, 'made by Gamma')), Object.keys(files));
  assert.deepEqual(zipNames(zipOf({})), []);
});

test('what is no zip, or a cut one, lists nothing', () => {
  const whole = zipOf({ 'uploads/d1.pdf': '%PDF-1.7' });
  for (const bad of [Buffer.alloc(0), Buffer.from('{"detail": "page not found"}'), Buffer.alloc(64), whole.subarray(0, whole.length - 30),
    whole.subarray(40), null, 'uploads/d1.pdf']) {
    assert.deepEqual(zipNames(bad), []);
  }
  // an end record that points past the end
  const wrong = Buffer.from(whole);
  wrong.writeUInt32LE(wrong.length, wrong.length - 6);
  assert.deepEqual(zipNames(wrong), []);
});
