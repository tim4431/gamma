// The account card's line for a guest: when the throwaway workspace goes
// (GET /api/session's guest_expires_at, docs/dev/guests.md). Pure, so node
// tests it (tests/guestExpiry.test.mjs).
import { t, tn } from "../shared/i18n/i18n.js";

export function guestExpiryLabel(expiresAt, now = Date.now()) {
  const at = expiresAt ? Date.parse(expiresAt) : NaN;
  if (!Number.isFinite(at)) return t("Temporary workspace");
  const minutes = Math.max(1, Math.ceil((at - now) / 60000));
  if (minutes < 60) return tn("Temporary workspace · gone in {n} minute", "Temporary workspace · gone in {n} minutes", minutes);
  return tn("Temporary workspace · gone in {n} hour", "Temporary workspace · gone in {n} hours", Math.round(minutes / 60));
}
