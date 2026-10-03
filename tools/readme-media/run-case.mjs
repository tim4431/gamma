// Run a checked-in legacy shot against the disposable suite workspace.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { ROOT } from './runtime.mjs';
import { Server, Account } from '../../frontend/tests/e2e/harness.mjs';
const name = process.argv[2];
const names = ['notes', 'library', 'search', 'metadata', 'agent', 'download-and-chat', 'reference-links', 'connector'];
if (!names.includes(name)) throw new Error(`Choose: ${names.join(', ')}`);
const suite = path.join(ROOT, 'artifacts/readme-media/suite');
const isolatedNotes = name === 'notes';
const isolated = isolatedNotes || name === 'connector';
const state = isolated ? {} : JSON.parse(fs.readFileSync(path.join(suite, 'workspace.json')));
if (state.removed) throw new Error('Prepare a suite workspace first');
const dir = path.join(suite, name);
fs.mkdirSync(dir, { recursive: true });
let server;
let noteId;
try {
  if (isolated) {
    // Notes needs no AI, and the extension targets the default workspace.
    // Both can use the curated export on an isolated account/server; the
    // connector's popup previews the paper, which takes registry lookups.
    server = new Server(isolatedNotes ? {} : { env: { GAMMA_METADATA_LOOKUP: 'on' } });
    await server.start();
    server.manage('create-user', 'demo', 'isolated-media-only');
    const account = await new Account(server, 'demo', 'isolated-media-only').login();
    await account.upload('/api/import-data', fs.readFileSync(path.join(ROOT, 'artifacts/readme-media/demo.zip')), 'demo.zip', 'application/zip');
    await account.file('fy0-h_BqOHcH', { folders: ['Quantum/Neutral atoms'] });   // the folder made where missing (harness.mjs Account.file)
    state.base = server.base; state.workspace = account.ws;
    if (isolatedNotes) {
      const note = await account.api('/api/pages', { method: 'POST', body: { title: 'Rabi oscillations' } });
      noteId = note.id;
    }
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
