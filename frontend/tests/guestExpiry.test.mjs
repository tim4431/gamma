// The guest account card names when the workspace goes (src/auth/guestExpiry.js).
import test from "node:test";
import assert from "node:assert/strict";
import { guestExpiryLabel } from "../src/auth/guestExpiry.js";

test("a guest's card says when the workspace goes", () => {
  const now = Date.parse("2026-09-25T10:00:00Z");
  assert.equal(guestExpiryLabel("2026-09-25T15:00:00Z", now), "Temporary workspace · gone in 5 hours");
  assert.equal(guestExpiryLabel("2026-09-25T11:10:00Z", now), "Temporary workspace · gone in 1 hour");
  assert.equal(guestExpiryLabel("2026-09-25T10:20:00Z", now), "Temporary workspace · gone in 20 minutes");
  assert.equal(guestExpiryLabel("2026-09-25T09:00:00Z", now), "Temporary workspace · gone in 1 minute", "past due: about to go");
  assert.equal(guestExpiryLabel("", now), "Temporary workspace");
  assert.equal(guestExpiryLabel("not a date", now), "Temporary workspace");
});
