// The providers' logos (src/ai/providerMarks.js): every service the server
// names a connection by (tests/shared/ai_models.json `services`, which the
// pytest holds to gamma/ai_settings.py) wears a mark that is drawn, but
// those listed as falling back to the generic one, which do so on purpose.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { MARKS, serviceMark } from "../src/ai/providerMarks.js";

const SERVICES = JSON.parse(readFileSync(new URL("../../tests/shared/ai_models.json", import.meta.url), "utf8")).services;

test("every service the server offers has a logo, or knowingly falls back", () => {
  for (const id of SERVICES.ids) {
    const mark = serviceMark(id);
    if (SERVICES.fallback.includes(id)) assert.equal(mark, "", `${id} is listed as falling back, yet has a mark`);
    else assert.ok(mark && MARKS[mark].paths.length, `${id} has no logo: draw one, or list it under fallback`);
  }
  assert.equal(serviceMark(""), "");
  assert.equal(serviceMark("no-such-service"), "", "a service the browser doesn't know yet falls back too");
});

test("a mark is plain geometry: paths, their fills and its gradients", () => {
  for (const [name, mark] of Object.entries(MARKS)) {
    for (const [d, fill] of mark.paths) {
      assert.match(d, /^[MmLlHhVvCcSsQqTtAaZz0-9.,\- ]+$/, `${name}: a path is commands and numbers`);
      if (typeof fill === "number") assert.ok(mark.gradients?.[fill], `${name}: gradient ${fill} is defined`);
      else if (fill !== undefined) assert.match(fill, /^#[0-9A-Fa-f]{6}$/, `${name}: a fill is a colour`);
    }
  }
});
