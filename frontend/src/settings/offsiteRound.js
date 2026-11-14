// What Settings → Backups › Bucket says of an environment's off-site round
// while it runs (BackupBucket.jsx): the status's `progress` from GET /api/admin/offsite,
// the databases looked at, then the files sent and their bytes, which the
// first round, sending every file, needs most (gamma/offsite.py `_phase`).
// Pure, so node tests it (tests/offsiteRound.test.mjs).
import { fmtBytes } from "../shared/lib/format.js";
import { t } from "../shared/i18n/i18n.js";

const count = (v) => Number(v || 0).toLocaleString();

// "databases 3 / 12", "files 120 / 3,400 · 1.2 GB / 8.0 GB"; "" with no
// progress yet.
export function offsiteProgressText(progress) {
  const p = progress || {};
  if (!(Number(p.total) > 0)) return "";
  const n = { done: count(p.done), total: count(p.total) };
  if (p.phase === "databases") return t("databases {done} / {total}", n);
  const parts = [t("files {done} / {total}", n)];
  const b = p.bytes;
  if (b && Number(b.total) > 0) {
    parts.push(`${fmtBytes(Math.min(Number(b.done) || 0, Number(b.total)))} / ${fmtBytes(Number(b.total))}`);
  }
  return parts.join(" · ");
}
