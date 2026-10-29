// Run a checked-in legacy shot against the disposable suite workspace.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { ROOT } from './runtime.mjs';
import { library, ATOMS } from './take.mjs';
const name = process.argv[2];
// The connector records on its own (record-connector.mjs on take.mjs).
const names = ['notes', 'library', 'search', 'metadata', 'agent', 'download-and-chat', 'reference-links'];
if (!names.includes(name)) throw new Error(`Choose: ${names.join(', ')}`);
const suite = path.join(ROOT, 'artifacts/readme-media/suite');
const isolated = name === 'notes';
const state = isolated ? {} : JSON.parse(fs.readFileSync(path.join(suite, 'workspace.json')));
if (state.removed) throw new Error('Prepare a suite workspace first');
const dir = path.join(suite, name);
fs.mkdirSync(dir, { recursive: true });
let server;
let noteId;
try {
  if (isolated) {
    // Notes needs no AI: the curated export on an isolated account/server.
    const lib = await library();
    server = lib.server;
    const { account } = lib;
    await account.file(ATOMS.id, { folders: ['Quantum/Neutral atoms'] });   // the folder made where missing (harness.mjs Account.file)
    state.base = server.base; state.workspace = account.ws;
    noteId = (await account.api('/api/pages', { method: 'POST', body: { title: 'Rabi oscillations' } })).id;
    fs.writeFileSync(path.join(dir, 'session.txt'), account.session);
  } else {
    await import('./prepare-cases.mjs');
    fs.copyFileSync(path.join(suite, 'session.txt'), path.join(dir, 'session.txt'));
  }
  const cases = fs.existsSync(path.join(suite, 'cases.json')) ? JSON.parse(fs.readFileSync(path.join(suite, 'cases.json'))) : {};
  process.exitCode = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(ROOT, 'tools/readme-media', `record-${name}.mjs`), ...process.argv.slice(3)], {
      cwd: dir, stdio: 'inherit', env: { ...process.env, BASE_URL: state.base, MEDIA_WORKSPACE: state.workspace, PAGE_ID: noteId || cases.notes || '', QEC_ID: cases.qec || '' },
    });
    child.once('error', reject);
    child.once('exit', code => resolve(code ?? 1));
  });
} finally {
  if (server) await server.stop();
}
