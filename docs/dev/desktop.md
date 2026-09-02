# Desktop app (`desktop/`)

The desktop app's developer docs live in the folder itself, separate from
Gamma's — the shell treats Gamma as a black box (HTTP API + env config), so
nothing in `backend/` or `frontend/` needs to know about it:

- [desktop/README.md](../../desktop/README.md) — the workspace model
  (local sidecar servers + remote URLs), the window (shell bar with the
  workspace switcher + content view), shell state, file map, run / package /
  release (the unified `release` workflow), invariants.
- [desktop/CHECKLIST.md](../../desktop/CHECKLIST.md) — the pre-release QA
  checklist and what `npm run e2e` / `npm run e2e:packaged` cover.

The only contract Gamma keeps for the shell: the env variables
`GAMMA_DATA_DIR`, `GAMMA_STATIC_DIR`, `GAMMA_ADMIN_USER`,
`GAMMA_ADMIN_PASSWORD`; `/api/health`; `/api/session` + `/api/login` for the
silent first login; and the `data-theme` attribute on `<html>` (the shell
mirrors it into its own chrome).
