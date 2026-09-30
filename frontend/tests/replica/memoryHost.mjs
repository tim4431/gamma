// A replica host in memory (src/replica/round.js's host interface, plus
// the editor's writeEdit / deleteHere from src/replica/edits.js), talking
// to a real Gamma server over fetch with a write token — what the iPad's
// Swift host does with SQLite and URLSession (ipad/GammaIPad/ReplicaHost.swift).
// `hooks.request(method, path, send)` may wrap a request (a test dropping
// an answer).
import crypto from "node:crypto";
import { serializeInk } from "../../src/ink/ink.js";
import { uploadRefs } from "../../src/replica/tree.js";

const clone = (v) => (v == null ? v : JSON.parse(JSON.stringify(v)));
const digest = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex").slice(0, 24);
const isPdf = (b) => b.length >= 4 && b.subarray(0, 4).toString("latin1") === "%PDF";
// A pulled file is kept only when it is what its name says (storage.matches_name).
const matches = (name, bytes) => (name.endsWith(".pdf") ? isPdf(bytes) : name.split(".")[0].length !== 24 || digest(bytes) === name.split(".")[0]);

export class MemoryHost {
  constructor({ base, token, remoteWs, user = "", mode = "two-way", hooks = {} }) {
    this.base = base;
    this.token = token;
    this.config = { remoteWs, user, mode };
    this.hooks = hooks;
    this.pages = new Map();     // id → snapshot
    this.versions = new Map();  // id → version (kept after a page goes)
    this.edited = new Map();    // id → the version of the last edit made here
    this.tombstones = new Set();
    this.states = new Map();
    this.meta = {};
    this.files = new Map();     // upload name → Buffer
    this.conflicts = [];
    this.notes = [];
  }

  headers(extra = {}) {
    return { Authorization: `Bearer ${this.token}`, "X-Gamma-Workspace": this.config.remoteWs, ...extra };
  }

  async request(method, path, body) {
    const send = async () => {
      const r = await fetch(`${this.base}${path}`, { method, headers: this.headers(body != null ? { "Content-Type": "application/json" } : {}),
        body: body != null ? JSON.stringify(body) : undefined });
      const text = await r.text();
      let parsed = text;
      try { parsed = JSON.parse(text); } catch { /* not JSON */ }
      return { status: r.status, body: parsed };
    };
    return this.hooks.request ? this.hooks.request(method, path, send) : send();
  }

  async getMeta() { return clone(this.meta); }
  async setMeta(meta) { this.meta = clone(meta); }

  async localChanges() {
    return { pages: [...this.edited.keys()].filter((id) => this.pages.has(id)), deleted: [...this.tombstones] };
  }
  async acknowledge(id, version) {
    if ((this.edited.get(id) ?? Infinity) <= version) this.edited.delete(id);
    if (!this.pages.has(id)) this.tombstones.delete(id);
  }

  async page(id) {
    return { snapshot: clone(this.pages.get(id) ?? null), version: this.versions.get(id) || 0 };
  }
  async writePage(id, snapshot, version) {
    if ((this.versions.get(id) || 0) !== version) return 0;
    const v = version + 1;
    this.pages.set(id, clone(snapshot));
    this.versions.set(id, v);
    return v;
  }
  async writeEdit(id, snapshot, version) {
    const v = await this.writePage(id, snapshot, version);
    if (v) { this.edited.set(id, v); this.tombstones.delete(id); }
    return v;
  }
  async removePage(id) {
    this.pages.delete(id);
    this.versions.set(id, (this.versions.get(id) || 0) + 1);
    this.edited.delete(id);
  }
  async deleteHere(id) {
    await this.removePage(id);
    this.tombstones.add(id);
  }

  async state(id) { return clone(this.states.get(id) ?? null); }
  async saveState(id, state) {
    if (state == null) this.states.delete(id);
    else this.states.set(id, clone(state));
  }

  async pageOfBlock(id) {
    for (const [pid, snap] of this.pages) if (id in snap) return pid;
    return null;
  }

  async readInk(name) {
    const b = this.files.get(name);
    return b ? JSON.parse(b.toString("utf8")) : null;
  }
  async storeInk(ink) {
    const bytes = Buffer.from(serializeInk(ink), "utf8");
    const name = `${digest(bytes)}.ink`;
    this.files.set(name, bytes);
    return name;
  }

  async pullFiles(names) {
    let n = 0;
    for (const name of names) {
      if (this.files.has(name)) continue;
      const r = await fetch(`${this.base}/api/uploads/${name}`, { headers: this.headers() });
      if (r.status === 404) continue;
      if (!r.ok) throw new Error(`GET ${name}: ${r.status}`);
      const bytes = Buffer.from(await r.arrayBuffer());
      if (!matches(name, bytes)) continue;
      this.files.set(name, bytes);
      n++;
    }
    return n;
  }
  async pushFiles(names) {
    let n = 0;
    for (const name of names) {
      const bytes = this.files.get(name);
      if (!bytes) continue;
      const head = await fetch(`${this.base}/api/uploads/${name}`, { method: "HEAD", headers: this.headers() });
      if (head.ok) continue;
      const form = new FormData();
      form.append("file", new Blob([bytes]), name);
      const r = await fetch(`${this.base}${name.endsWith(".pdf") ? "/api/uploads" : "/api/upload-file"}`, { method: "POST", headers: this.headers(), body: form });
      if (!r.ok) throw new Error(`upload ${name}: ${r.status} ${await r.text()}`);
      const out = await r.json();
      const got = out.source_url || out.url || "";
      if (!got.endsWith(`/${name}`)) throw new Error(`uploaded ${name} but the remote stored it as ${got}`);
      n++;
    }
    return n;
  }
  async referencedFiles() {
    const names = new Set();
    for (const snap of this.pages.values()) for (const n of uploadRefs(Object.values(snap))) names.add(n);
    return [...names];
  }

  async conflict(c) { this.conflicts.push(c); }
  async note(entry) { this.notes.push(entry); }
}
