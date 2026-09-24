// A stand-in for the Gamma Cloud account server (cloud/, docs/dev/cloud_accounts.md)
// for the browser suite, on a free loopback port: what a Gamma server asks of
// it and nothing more. The discovery document (naming the share host), the
// authorization endpoint (it answers at once, as the person `login` names,
// as if they were signed in there), the token endpoint (authorization code
// with PKCE, refresh with rotation), EdDSA ID tokens and their JWKS,
// /userinfo (what a share host checks a publishing server's token against),
// revocation, and the preference profile and server list cloud_sync calls.
import crypto from "node:crypto";
import http from "node:http";

const b64url = (buf) => Buffer.from(buf).toString("base64url");
const random = () => b64url(crypto.randomBytes(18));

export class FakeCloud {
  constructor() {
    this.people = new Map();   // subject -> the claims /userinfo answers
    this.codes = new Map();    // authorization code -> {sub, clientId, nonce, redirectUri, challenge}
    this.access = new Map();   // access token -> subject
    this.refresh = new Map();  // live refresh token -> subject
    this.prefs = new Map();    // `${subject} ${path}` -> {value, updated_at}
    this.shareHost = "";
    this.login = "";           // the subject the authorization endpoint answers for
    this.kid = "e2e-key";
    ({ publicKey: this.publicKey, privateKey: this.privateKey } = crypto.generateKeyPairSync("ed25519"));
  }
  get issuer() { return `http://127.0.0.1:${this.port}`; }

  // A verified cloud account; the next authorization signs in as it.
  person(sub, username) {
    this.people.set(sub, { sub, preferred_username: username, email: `${username}@example.org`, email_verified: true,
      plan: "free", name: username });
    this.login = sub;
  }

  async start() {
    this.server = http.createServer((req, res) => {
      this.handle(req, res).catch((e) => { res.writeHead(500); res.end(String(e)); });
    });
    await new Promise((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.port = this.server.address().port;
  }
  stop() { return new Promise((resolve) => (this.server ? this.server.close(() => resolve()) : resolve())); }

  idToken(sub, clientId, nonce) {
    const now = Math.floor(Date.now() / 1000);
    const header = b64url(JSON.stringify({ alg: "EdDSA", typ: "JWT", kid: this.kid }));
    const body = b64url(JSON.stringify({ ...this.people.get(sub), iss: this.issuer, aud: clientId, iat: now,
      exp: now + 300, nonce }));
    const signature = crypto.sign(null, Buffer.from(`${header}.${body}`), this.privateKey);
    return `${header}.${body}.${b64url(signature)}`;
  }
  tokens(sub) {
    const access = random();
    const refresh = random();
    this.access.set(access, sub);
    this.refresh.set(refresh, sub);
    return { access_token: `at-${access}`, refresh_token: `rt-${refresh}`, token_type: "Bearer", expires_in: 3600 };
  }

  async handle(req, res) {
    const url = new URL(req.url, this.issuer);
    const raw = await new Promise((resolve) => { let d = ""; req.on("data", (c) => { d += c; }); req.on("end", () => resolve(d)); });
    const json = (status, value) => { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(value)); };
    const bearer = () => {
      const token = (req.headers.authorization || "").replace(/^Bearer /, "");
      return token.startsWith("at-") ? this.access.get(token.slice(3)) : undefined;
    };
    const path = url.pathname;
    if (path === "/.well-known/openid-configuration") {
      return json(200, {
        issuer: this.issuer, authorization_endpoint: `${this.issuer}/authorize`, token_endpoint: `${this.issuer}/token`,
        jwks_uri: `${this.issuer}/jwks`, userinfo_endpoint: `${this.issuer}/userinfo`,
        revocation_endpoint: `${this.issuer}/revoke`, gamma_share_host: this.shareHost,
      });
    }
    if (path === "/jwks") {
      return json(200, { keys: [{ ...this.publicKey.export({ format: "jwk" }), kid: this.kid, alg: "EdDSA", use: "sig" }] });
    }
    if (path === "/authorize") {
      const q = url.searchParams;
      const code = random();
      this.codes.set(code, { sub: this.login, clientId: q.get("client_id"), nonce: q.get("nonce"),
        redirectUri: q.get("redirect_uri"), challenge: q.get("code_challenge") });
      const back = new URL(q.get("redirect_uri"));
      back.searchParams.set("code", code);
      back.searchParams.set("state", q.get("state"));
      res.writeHead(302, { Location: back.toString() });
      return res.end();
    }
    if (path === "/token" && req.method === "POST") {
      const form = new URLSearchParams(raw);
      if (form.get("grant_type") === "authorization_code") {
        const pending = this.codes.get(form.get("code"));
        this.codes.delete(form.get("code"));
        const verifier = form.get("code_verifier") || "";
        if (!pending || pending.redirectUri !== form.get("redirect_uri")
            || b64url(crypto.createHash("sha256").update(verifier).digest()) !== pending.challenge) {
          return json(400, { error: "invalid_grant" });
        }
        return json(200, { ...this.tokens(pending.sub), id_token: this.idToken(pending.sub, pending.clientId, pending.nonce) });
      }
      if (form.get("grant_type") === "refresh_token") {
        const used = (form.get("refresh_token") || "").replace(/^rt-/, "");
        const sub = this.refresh.get(used);
        if (!sub) return json(400, { error: "invalid_grant" });
        this.refresh.delete(used);
        return json(200, this.tokens(sub));
      }
      return json(400, { error: "unsupported_grant_type" });
    }
    if (path === "/revoke") return json(200, {});
    const sub = bearer();
    if (path === "/userinfo") return sub ? json(200, this.people.get(sub)) : json(401, { error: "invalid_token" });
    if (path.startsWith("/api/me/")) {
      if (!sub) return json(401, { detail: "not signed in" });
      if (path.startsWith("/api/me/prefs/")) {
        const key = `${sub} ${path}`;
        if (req.method === "PUT") {
          const body = JSON.parse(raw || "{}");
          this.prefs.set(key, { value: body.value, updated_at: body.updated_at });
          return json(200, this.prefs.get(key));
        }
        return this.prefs.has(key) ? json(200, this.prefs.get(key)) : json(404, { detail: "no profile" });
      }
      return json(200, { ok: true }); // the server list
    }
    return json(404, { detail: "not here" });
  }
}
