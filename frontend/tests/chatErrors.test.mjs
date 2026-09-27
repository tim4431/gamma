import assert from "node:assert/strict";
import { test } from "node:test";
import { FAILURE_KINDS, chatFailure, failureCopy, failureFields, fixLabel } from "../src/chat/chatErrors.js";

test("every failure kind has a headline, a sentence and a labelled fix", () => {
  for (const kind of FAILURE_KINDS) {
    const copy = failureCopy(kind, { provider: "OpenAI" });
    assert.ok(copy.headline, kind);
    assert.ok(copy.text, kind);
    if (copy.fix) assert.ok(fixLabel(copy.fix), `${kind}: ${copy.fix}`);
  }
});

test("a rejected credential names the connection and asks for the right fix", () => {
  assert.equal(failureCopy("auth", { provider: "OpenAI" }).headline, "OpenAI rejected the API key");
  assert.equal(failureCopy("auth", { provider: "OpenAI" }).fix, "key");
  assert.equal(failureCopy("auth", { provider: "ChatGPT", auth: "oauth" }).fix, "signin");
  assert.equal(failureCopy("auth").headline, "The AI service rejected the API key");
  assert.equal(failureCopy("network").switchModel, undefined, "another model can't fix a lost connection");
});

test("the server's classification rides on the thrown error and is saved on the reply", () => {
  const err = chatFailure("AI call failed: upstream 529: Overloaded",
    { kind: "overloaded", status: 529, provider_name: "Anthropic", provider_id: "p1", provider_auth: "key" });
  assert.deepEqual(failureFields(err), {
    errorKind: "overloaded", errorDetail: "AI call failed: upstream 529: Overloaded",
    errorStatus: 529, errorProvider: "Anthropic", errorProviderId: "p1",
  });
  // An unknown or missing kind (an older server) is "other"; a detail that
  // isn't a string (a validation error) still reads.
  assert.equal(chatFailure("x", { kind: "nope" }).kind, "other");
  assert.equal(chatFailure([{ msg: "bad" }]).message, '[{"msg":"bad"}]');
  // fetch's own TypeError is the browser losing Gamma, not the provider.
  assert.equal(failureFields(new TypeError("Failed to fetch")).errorKind, "network");
});
