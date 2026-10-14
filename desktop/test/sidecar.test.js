// The port a local server starts on (lib/sidecar.js pickPort): the one it
// had last time while that is free, else a new one from the dynamic range
// that no other server has remembered. `npm test` in desktop/.

const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('net');
const { pickPort } = require('../lib/sidecar');

const listen = (port = 0) => new Promise((resolve, reject) => {
  const srv = net.createServer();
  srv.once('error', reject);
  srv.listen(port, '127.0.0.1', () => resolve(srv));
});
const close = (srv) => new Promise((resolve) => srv.close(resolve));
const dynamic = (p) => Number.isInteger(p) && p >= 49152 && p <= 65535;

test('a free remembered port is kept', async () => {
  const srv = await listen();
  const port = srv.address().port;
  await close(srv);
  assert.equal(await pickPort({ port }, []), port);
});

test('a remembered port another program holds is given up for a dynamic one', async () => {
  const srv = await listen();
  const port = srv.address().port;
  try {
    const got = await pickPort({ port }, []);
    assert.notEqual(got, port);
    assert(dynamic(got), `in the dynamic range: ${got}`);
  } finally {
    await close(srv);
  }
});

test('a new server gets a dynamic port, never one another server remembered', async () => {
  for (let i = 0; i < 20; i++) assert(dynamic(await pickPort({}, [])));
  const avoid = Array.from({ length: 16384 - 8 }, (_, i) => 49152 + i); // all but the top eight
  for (let i = 0; i < 5; i++) {
    const got = await pickPort({ port: 'nonsense' }, avoid);
    assert(!avoid.includes(got), `not remembered by another: ${got}`);
  }
});
