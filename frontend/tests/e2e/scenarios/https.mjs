import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { Account, PYTHON, Server, wanted } from "../harness.mjs";

// A NAS over HTTPS with a certificate it made itself (docs/dev/https.md):
// two Gammas, the NAS served by uvicorn with a self-signed certificate for
// 127.0.0.1 and localhost, and a computer (the desktop app's own server: the
// vaults recipe of GAMMA_FOLDER_LINKS, the shell's secret set, as the shell
// starts it, no agents). The computer refuses the NAS's certificate until the
// shell pins it (PUT /api/remote-servers/pins, as the desktop app does once
// the person trusts it by its fingerprint), and its refusal names the
// certificate it was served. Pinned, a vault of a NAS folder syncs, the
// computer is shown there, and the maker's browser (which accepts the
// certificate itself) shows the computer's chip, the page's live socket on
// wss://. The links move from https://127.0.0.1 to https://localhost (the
// shell's address change): the counts are right, the NAS sees the same
// computer after it, and the vault syncs at the new address. The NAS started
// again with another certificate, the vault and the channel say the
// certificate changed. A redirect is never followed: another address is only
// named, and a sign-in page's redirect is the sign-in in the way.
const SECRET = "e2e-https";
const SHELL = { "X-Gamma-Shell": SECRET };
const execFileAsync = promisify(execFile);

// A self-signed EC P-256 certificate for 127.0.0.1 and localhost (a year:
// what macOS takes from a leaf), written as cert.pem and key.pem in the
// directory argv[1]. No gamma import: venv python for its cryptography.
const MAKE_CERT = `
import datetime, ipaddress, os, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Gamma e2e NAS")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                                    x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256()))
with open(os.path.join(sys.argv[1], "cert.pem"), "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))
with open(os.path.join(sys.argv[1], "key.pem"), "wb") as f:
    f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))
`;

// A new certificate in its own directory under `dir`: {cert, key} (the
// paths) and what the shell pins of it, its digest as the sidecar computes it.
async function certificate(dir, name) {
  const at = path.join(dir, name);
  fs.mkdirSync(at);
  await execFileAsync(PYTHON, ["-c", MAKE_CERT, at]);
  const pem = fs.readFileSync(path.join(at, "cert.pem"), "utf8");
  const x509 = new crypto.X509Certificate(pem);
  return {
    tls: { cert: path.join(at, "cert.pem"), key: path.join(at, "key.pem") },
    sha256: x509.fingerprint256.replace(/:/g, "").toLowerCase(),
    pin: (origin) => ({ origin, sha256: x509.fingerprint256.replace(/:/g, "").toLowerCase(), subject: x509.subject,
      issuer: x509.issuer, not_before: new Date(x509.validFrom).toISOString(), not_after: new Date(x509.validTo).toISOString() }),
  };
}

export async function httpsScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("https")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-https-"));
  const first = await certificate(disk, "first");
  const nas = new Server({ tls: first.tls });
  const computer = new Server({ env: { GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: SECRET } });
  // the other server of the last steps: plain http, answering every request with `answer`
  const front = { seen: [], answer: null };
  const proxy = http.createServer((req, res) => {
    front.seen.push({ method: req.method, url: req.url, auth: req.headers.authorization || "" });
    req.resume();
    front.answer(req, res);
  });
  try {
    await nas.start();
    await computer.start();
    await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
    const proxyUrl = `http://127.0.0.1:${proxy.address().port}`;
    const port = nas.port;
    const [ip, named] = [`https://127.0.0.1:${port}`, `https://localhost:${port}`];
    await nas.manage("create-user", "hs-alice", "hs-alice-pw");
    const alice = await new Account(nas, "hs-alice", "hs-alice-pw").login();
    await computer.manage("create-user", "hs-owner", "hs-owner-pw");
    const owner = await new Account(computer, "hs-owner", "hs-owner-pw").login();
    const shell = (pathname, opts = {}) => owner.api(pathname, { ...opts, headers: SHELL });

    const pg = await alice.api("/api/pages", { method: "POST", body: { title: "Lattice notes" } });
    await alice.file(pg.id, { folders: ["Lattice"] });
    const folderId = await alice.folder("Lattice");
    const read = await alice.api("/api/integrations/tokens", { method: "POST",
      body: { name: "Gamma desktop: Lattice synced vault", scope: "read", expires_in_days: 365 } });
    const dir = path.join(disk, "Lattice");
    const vaultBody = { folder: folderId, path: dir, notes: true, pull: true, push: "off", remote_url: ip,
      workspace: alice.ws, token: read.token, token_id: read.id };
    let vault = null, link = null, computerId = "";
    const syncVault = async () => (await owner.api(`/api/folder-links/${vault.id}/sync?wait=1`, { method: "POST" })).status;

    await step("https: a vault of a NAS whose certificate this computer does not trust is refused, naming the certificate", async () => {
      const r = await shell("/api/folder-links", { method: "POST", body: vaultBody }).catch((e) => e);
      assertEq(r.status, 502, `refused: ${JSON.stringify(r.data)}`);
      assert(/certificate (is not trusted|does not lead to an authority)/.test(r.data.detail), `the words: ${r.data.detail}`);
      assertEq(r.data.error_kind, "certificate", "what was in the way");
      assertEq(r.data.cert?.sha256, first.sha256, "the certificate it was served, as the shell pins it");
      assertEq((await owner.api("/api/folder-links")).links.length, 0, "nothing is made");
    });

    await step("https: pinned by the shell, the vault is made and a round writes the page's file", async () => {
      const { pins } = await shell("/api/remote-servers/pins", { method: "PUT", body: { pins: [first.pin(ip), first.pin(named)] } });
      assertEq(JSON.stringify(pins.map((p) => [p.origin, p.sha256]).sort()),
        JSON.stringify([[ip, first.sha256], [named, first.sha256]].sort()), "both origins pinned");
      vault = await shell("/api/folder-links", { method: "POST", body: vaultBody });
      assertEq(vault.remote_url, ip, "the vault reads the NAS over https");
      const status = await syncVault();
      assertEq(status.last_error, "", "the round ran");
      assert(fs.existsSync(path.join(dir, "Lattice notes.md")), "the page's notes file is on disk");
    });

    await step("https: this computer is shown on the NAS over https", async () => {
      const minted = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: `Gamma desktop: ${os.hostname()}`, scope: "computer", expires_in_days: 365 } });
      ({ link } = await shell("/api/computer-links", { method: "POST",
        body: { remote_url: ip, workspace: alice.ws, token: minted.token, token_id: minted.id } }));
      await until(async () => (await owner.api("/api/computer-links")).links.find((l) => l.id === link.id)?.status?.state === "connected",
        { timeout: 45000, every: 500, what: "the channel connected" });
      const [seen] = await until(async () => {
        const list = (await alice.api(`/api/pages/${pg.id}/computer`)).computers;
        return list.length && list[0].online ? list : null;
      }, { timeout: 20000, what: "the page's computer, online" });
      computerId = seen.id;
    });

    await step("https: the maker's browser on the NAS shows the computer's chip, the page's live socket on wss://", async () => {
      const ctx = await alice.context(browser, { ignoreHTTPSErrors: true });
      try {
        const page = await openPage(ctx);
        const sockets = [];
        page.on("websocket", (ws) => sockets.push(ws.url()));
        await page.goto(`${nas.base}/?ws=${alice.ws}&page=${pg.id}`);
        await page.locator(".pageTitleRow", { hasText: "Lattice notes" }).waitFor();
        const chip = page.locator(".vaultComputerChip");
        await chip.waitFor({ timeout: 15000 });
        assertEq(await chip.getAttribute("data-computer"), "online", "the chip says online");
        await until(() => sockets.some((u) => u.startsWith(`wss://127.0.0.1:${port}/`)), { timeout: 10000, what: "a wss:// socket" });
        assert(!sockets.some((u) => u.startsWith("ws://")), `no plain socket: ${sockets.join(", ")}`);
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("https: the links move from https://127.0.0.1 to https://localhost, and the NAS sees the same computer", async () => {
      const plan = await shell("/api/remote-servers/move", { method: "POST", body: { from: ip, to: named, dry_run: true } });
      assertEq(JSON.stringify([plan.kind, plan.plain, plan.moved]), JSON.stringify(["other", false, { vaults: 1, computers: 1, mirrors: 0 }]),
        "the dry run's counts");
      const moved = await shell("/api/remote-servers/move", { method: "POST", body: { from: ip, to: named } });
      assertEq(JSON.stringify([moved.moved, moved.revoke]), JSON.stringify([{ vaults: 1, computers: 1, mirrors: 0 }, []]), "what moved");
      const links = (await owner.api("/api/folder-links")).links;
      assertEq(links.find((l) => l.id === vault.id)?.remote_url, named, "the vault's new address");
      await until(async () => {
        const kept = (await owner.api("/api/computer-links")).links.find((l) => l.id === link.id);
        return kept?.remote_url === named && kept.status?.state === "connected";
      }, { timeout: 45000, every: 500, what: "the channel connected at the new address" });
      const computers = await until(async () => {
        const list = (await alice.api(`/api/pages/${pg.id}/computer`)).computers;
        return list.length ? list : null;
      }, { timeout: 20000, what: "the page's computer, announced from the new address" });
      assertEq(JSON.stringify(computers.map((c) => c.id)), JSON.stringify([computerId]), "one computer there, the same one");
      assertEq((await syncVault()).last_error, "", "the next round syncs");
    });

    await step("https: the NAS started again with another certificate: the vault and the channel say it changed", async () => {
      const second = await certificate(disk, "second");
      await nas.halt();
      nas.tls = second.tls;
      await nas.start({ port });
      const status = await syncVault();
      assertEq(status.error_kind, "certificate_changed", `the vault's status: ${JSON.stringify(status)}`);
      assertEq(status.cert?.sha256, second.sha256, "the certificate served now");
      const kept = await until(async () => {
        const l = (await owner.api("/api/computer-links")).links.find((x) => x.id === link.id);
        return l?.status?.error_kind === "certificate_changed" ? l : null;
      }, { timeout: 60000, every: 500, what: "the channel says the certificate changed" });
      assertEq(kept.status.cert?.sha256, second.sha256, "the channel names the certificate served now");
    });

    await step("https: a server that redirects to another address is named, never followed", async () => {
      front.answer = (req, res) => { res.writeHead(301, { Location: `${named}${req.url}`, "Content-Length": "0" }); res.end(); };
      front.seen = [];
      const r = await shell("/api/folder-links", { method: "POST",
        body: { ...vaultBody, path: path.join(disk, "Redirected"), remote_url: proxyUrl } }).catch((e) => e);
      assertEq(r.status, 409, `refused: ${JSON.stringify(r.data)}`);
      assertEq(r.data.detail, `That server redirects to another address (${named}). If you moved it, change its address in the launcher.`);
      assertEq(r.data.move_to, named, "where it pointed");
      assertEq(front.seen.length, 1, "asked once, nothing followed");
    });

    await step("https: a sign-in page's redirect is the sign-in in the way, and no token goes on", async () => {
      front.answer = (req, res) => {
        res.writeHead(302, { Location: `https://auth.example/login?rd=${encodeURIComponent(proxyUrl + req.url)}`, "Content-Length": "0" });
        res.end();
      };
      front.seen = [];
      const r = await shell("/api/folder-links", { method: "POST",
        body: { ...vaultBody, path: path.join(disk, "Signed"), remote_url: proxyUrl } }).catch((e) => e);
      assertEq(r.status, 502, `refused: ${JSON.stringify(r.data)}`);
      assertEq(r.data.detail, "a sign-in or proxy in front of 127.0.0.1 redirected this computer to auth.example");
      assertEq(JSON.stringify([r.data.error_kind, r.data.blocked]), JSON.stringify(["blocked", "signin"]), "what was in the way");
      assertEq(front.seen.filter((s) => s.auth).length, 1, "the token went to the address the link names, once");
      assertEq((await owner.api("/api/folder-links")).links.length, 1, "nothing more is made");
    });
  } finally {
    proxy.close();
    await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
