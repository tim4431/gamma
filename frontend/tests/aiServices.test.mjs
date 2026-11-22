// The connect dialog's services (src/settings/aiServices.js): the cards of
// its first step, which plan a form is on, and whether it may go without a
// key (the server's rule: tests/shared/ai_models.json's `keyless`).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { formService, keyOptional, serviceSections } from "../src/settings/aiServices.js";

const KEYLESS = JSON.parse(readFileSync(new URL("../../tests/shared/ai_models.json", import.meta.url), "utf8")).keyless;

const preset = (id, label, group, plan, extra = {}) => ({ id, label, group, plan, protocol: "openai", base_url: `https://${id}.example/v1`, ...extra });
const INFO = {
  protocols: [
    { id: "anthropic", label: "Anthropic", auth: "key", default_base_url: "https://api.anthropic.com" },
    { id: "openai", label: "OpenAI", auth: "key", default_base_url: "https://api.openai.com" },
    { id: "chatgpt", label: "ChatGPT", auth: "oauth" },
  ],
  services: [
    preset("kimi", "Kimi", "Kimi", "API key"),
    preset("kimi-cn", "Kimi (China)", "Kimi", "API key (China)"),
    preset("kimi-code", "Kimi Code", "Kimi", "Coding plan subscription", { billing: "subscription" }),
    preset("opencode-zen", "OpenCode Zen", "OpenCode", "Pay as you go"),
    preset("opencode-go", "OpenCode Go", "OpenCode", "Go subscription", { billing: "subscription", usage: "opencode-go" }),
    preset("ollama", "Ollama", "Ollama", "", { billing: "local", key_optional: true }),
  ],
};
const shape = (sections) => sections.map((s) => [s.id, s.cards.map((c) => `${c.label}:${c.plans.map((p) => p.id).join("+")}`)]);

test("services group by how they connect, a vendor's plans of one kind on one card", () => {
  assert.deepEqual(shape(serviceSections(INFO)), [
    ["signin", ["ChatGPT:chatgpt", "Kimi Code:kimi-code", "OpenCode Go:opencode-go"]],
    ["key", ["Anthropic:anthropic", "OpenAI API:openai", "Kimi:kimi+kimi-cn", "OpenCode Zen:opencode-zen"]],
    ["local", ["Ollama:ollama", "Custom endpoint:custom"]],
  ]);
  const billing = Object.fromEntries(serviceSections(INFO).flatMap((s) => s.cards).map((c) => [c.label, c.billing]));
  assert.deepEqual([billing.ChatGPT, billing["Kimi Code"], billing.Kimi, billing.Ollama, billing["Custom endpoint"]],
    ["subscription", "subscription", "api", "local", ""]);
});

test("editing a saved connection offers only services of its own kind", () => {
  const oauth = (id) => INFO.protocols.find((p) => p.id === id)?.auth === "oauth";
  const keyEntry = serviceSections(INFO, { only: (id) => !oauth(id) });
  assert.ok(!keyEntry.flatMap((s) => s.cards).some((c) => c.id === "chatgpt"));
  assert.ok(keyEntry.find((s) => s.id === "signin").cards.some((c) => c.label === "Kimi Code"), "a coding plan is a key entry");
  assert.deepEqual(shape(serviceSections(INFO, { only: oauth })), [["signin", ["ChatGPT:chatgpt"]]]);
});

test("a form is on the preset its address is, the protocol at its own endpoint, or a custom endpoint", () => {
  const form = (extra) => ({ protocol: "openai", base_url: "", ...extra });
  assert.equal(formService(form({ base_url: "https://ollama.example/v1/" }), INFO), "ollama");
  assert.equal(formService(form({ base_url: "http://10.0.0.5:8000/v1" }), INFO), "custom", "an address no preset has");
  assert.equal(formService(form({}), INFO), "openai", "the protocol at its own endpoint");
});

test("a form may go without a key where the server would save it without one", () => {
  for (const c of KEYLESS.cases) {
    assert.equal(keyOptional({ protocol: c.protocol, base_url: c.base_url }, KEYLESS.choices), c.key_optional, c.note);
  }
});
