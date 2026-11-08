# frontend/

React + Vite SPA. Talks same-origin `/api/*`; in dev Vite proxies to `:9001`.

The oldest browsers it runs in are Safari 16.4, Chrome and Edge 111 and Firefox 121: its CSS uses `color-mix()` and `:has()`, and its *Full screen* button the unprefixed Fullscreen API (a browser without it gets the app's own full screen). The desktop app's Electron, the iPad app's WebKit and an up-to-date Android System WebView are all past that.

```bash
npm install
npm run dev      # :5173
npm run build    # → dist/  (FastAPI serves this in prod)
npm test         # pure-module tests
npm run lint     # Biome: bug-finding rules only (docs/dev/debugging.md)
npm run e2e      # browser suite against an isolated backend (build first)
```

- `src/` — app code grouped by function (`editor/`, `pdf/`, `settings/`, etc.), with application orchestration in `app/` and reused code in `shared/`; see the [source map](src/README.md)
- `public/media/icons/` — app icons served as-is (`/media/icons/favicon.svg`)
- `dist/assets/` — Vite-generated, content-hashed bundles; do not put unversioned public files here
- `vite.config.js` — dev proxy + build config
- `biome.json` — the lint's rules ([Frontend lint](../docs/dev/debugging.md#frontend-lint))

View modes are derived from the URL (no router lib): `/` home · `/?page=<id>` page · `/?share=<token>` public · `/?block=<id>` jump-to-block.

Repository and asset locations: [docs/dev/repository.md](../docs/dev/repository.md).
File organization is implemented; the larger application decomposition remains
planned in [docs/dev/frontend-refactor.md](../docs/dev/frontend-refactor.md).
