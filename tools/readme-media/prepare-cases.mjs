// Setup for the recorded scenarios, only in the newly created suite workspace.
import fs from 'node:fs';
import path from 'node:path';
import { account, suiteDir } from './suite-api.mjs';
const file = path.join(suiteDir, 'cases.json');
const cases = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file)) : {};
const requested = new Set(process.argv.slice(2).map(s => s.replace(/^--/, '')));
if (!cases.notes) {
  const note = await account.api('/api/pages', { method: 'POST', body: { title: 'Rabi oscillations' } });
  cases.notes = note.id;
  fs.writeFileSync(file, JSON.stringify(cases, null, 2));
}
if (requested.has('notes')) {
  await account.api(`/api/blocks/${cases.notes}/children`, { method: 'PUT', body: { blocks: [] } });
}
if (['metadata', 'download-and-chat', 'reference-links', 'agent'].some(n => requested.has(n))) {
  const { children } = await account.api('/api/blocks/root/children');
  for (const p of children) {
    const source = p.properties?.source_url || '';
    const arxiv = p.properties?.meta?.arxiv_id;
    const remove = (requested.has('metadata') && (arxiv === '2312.03982' || source.includes('2312.03982')))
      || (requested.has('reference-links') && (arxiv === '0904.2557' || source.includes('0904.2557')))
      || (requested.has('download-and-chat') && (p.content === 'Attention Is All You Need' || source.includes('3f5ee243547dee91fbd053c1c4a845aa')));
    if (remove) await account.api(`/api/blocks/${p.id}`, { method: 'DELETE' });
    else if (requested.has('agent')) await account.api(`/api/blocks/${p.id}`, { method: 'PUT', body: { properties: { folders: null } } });
  }
  await account.api('/api/prefs/open-tabs', { method: 'PUT', body: { value: [] } });
  if (requested.has('agent') || requested.has('download-and-chat')) await account.api('/api/chats/home', { method: 'DELETE' });
}
if (requested.has('library') || requested.has('search')) {
  const { children: roots } = await account.api('/api/blocks/root/children');
  const qec = roots.find(p => p.properties?.meta?.arxiv_id === '0904.2557');
  if (!qec) throw new Error('The curated QEC paper is missing');
  cases.qec = qec.id;
  fs.writeFileSync(file, JSON.stringify(cases, null, 2));
  for (const [id, folder] of [['fy0-h_BqOHcH', 'Quantum/Neutral atoms'], [cases.qec, 'Quantum/Error correction']]) {
    await account.file(id, { folders: [folder] });   // the folders made where missing (harness.mjs Account.file)
  }
  await account.api('/api/prefs/recent-views', { method: 'PUT', body: { value: [] } });
}
if (requested.has('search')) {
  // The paper view's default find bar, matches shown in the page only (the
  // account keeps this choice; the library shot turns the details on).
  const profile = (await account.api('/api/prefs/profile')).value || {};
  await account.api('/api/prefs/profile', { method: 'PUT', body: { value: { ...profile, searchDetailsPaper: false } } });
  // Topic labels, for Ctrl+P to find one by name.
  const { children: roots } = await account.api('/api/blocks/root/children');
  const topics = { 'fy0-h_BqOHcH': ['quantum computing', 'neutral atoms'], [cases.qec]: ['quantum computing', 'error correction'] };
  for (const p of roots) {
    const labels = topics[p.id] || (p.content === 'Attention Is All You Need' ? ['machine learning'] : null);
    if (labels) await account.file(p.id, { labels });
  }
}
console.log('Prepared case pages:', cases);
