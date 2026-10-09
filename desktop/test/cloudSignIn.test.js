// lib/cloudSignIn.js: the loopback return of the desktop app's Gamma Cloud
// sign-in. The whole flow, with a server and a fake account server, is a
// step of test/e2e.js.

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const cloudSignIn = require('../lib/cloudSignIn');

const get = (url) => fetch(url).then(async (r) => ({ status: r.status, text: await r.text() }));

test('the start the shell takes over, and what it asked for', () => {
  assert.equal(cloudSignIn.isStart('http://127.0.0.1:9100/api/auth/cloud/start?next=%2F'), true);
  assert.equal(cloudSignIn.isStart('http://127.0.0.1:9100/api/auth/cloud/callback?code=x'), false);
  assert.equal(cloudSignIn.isStart('not a url'), false);
  assert.deepEqual(cloudSignIn.startParams('http://h/api/auth/cloud/start?next=%2F%3Fpage%3Da&link=1'), { next: '/?page=a', link: true });
  assert.deepEqual(cloudSignIn.startParams('http://h/api/auth/cloud/start'), { next: '/', link: false });
});

test('the browser brings back a result once, to this machine; the listener then closes', async () => {
  const flow = await cloudSignIn.listen();
  const url = new URL(flow.returnTo);
  assert.equal(url.hostname, '127.0.0.1');
  assert.equal(url.pathname, cloudSignIn.RETURN_PATH);
  assert.equal(flow.challenge, crypto.createHash('sha256').update(flow.verifier).digest('base64url'));
  assert.equal(flow.challenge.length, 43);
  assert.equal((await get(`${url.origin}/elsewhere?result=r`)).status, 404, 'only its own path');
  assert.equal((await get(flow.returnTo)).status, 404, 'with something to bring');
  const page = await get(`${flow.returnTo}?result=one-time`);
  assert.equal(page.status, 200);
  assert.match(page.text, /Signed in to Gamma/);
  assert.deepEqual(await flow.answer, { result: 'one-time' });
  await assert.rejects(get(`${flow.returnTo}?result=again`), 'nothing listens any more');
});

test('a refused sign-in shows its reason in the browser, escaped, and reports it', async () => {
  const flow = await cloudSignIn.listen();
  const page = await get(`${flow.returnTo}?error=${encodeURIComponent('<b>not linked</b>')}`);
  assert.match(page.text, /Sign-in did not finish/);
  assert.match(page.text, /&#60;b&#62;not linked/);
  assert.deepEqual(await flow.answer, { error: '<b>not linked</b>' });
});

test('one sign-in at a time, and none waits forever', async () => {
  const first = await cloudSignIn.listen();
  const second = await cloudSignIn.listen();
  assert.equal(await first.answer, null, 'a newer sign-in replaces it');
  second.end(null);
  assert.equal(await second.answer, null);
  const late = await cloudSignIn.listen({ waitMs: 20 });
  assert.equal(await late.answer, null);
});

test('the claim posts the result and the verifier with the header the server asks for', () => {
  const [url, options] = cloudSignIn.claimRequest('http://127.0.0.1:9100', { verifier: 'v-1' }, 'r-1');
  assert.equal(url, 'http://127.0.0.1:9100/api/auth/cloud/app-claim');
  assert.equal(options.postData[0].bytes.toString(), 'result=r-1&verifier=v-1');
  assert.match(options.extraHeaders, /^X-Gamma-Desktop: claim$/m);
});
