# Models per task, providers and cost

How other multi-provider clients let a user mix providers, choose a model
for each kind of work, notice when that model goes away, and read what
the calls cost. This note was surveyed on 2026-11-21 for the redesign of
Settings → AI. The design picked is described at the end.

## Gamma before the redesign

- **One provider for everything.** Each account kept a list of
  connections, each an API key or a ChatGPT sign-in with its picked
  models. The client offered only one connection's models at a time: the
  `ai-provider` pick, or the first connection when nothing was picked.
  So the chat, metadata, citations, titles, translation and handwriting
  all ran on the same provider. The server already routed any
  `<entryId>:<model>` to its entry. Only the client's `providerModels`
  narrowed the list.
- **Picks were kept in the browser.** The metadata, translation and chat
  picks lived in localStorage, so a server-side job could not see them.
  The extension's clip fetched metadata with no model, which meant the
  first model of the first connection.
- **Gone picks were replaced silently.** A metadata or translation pick
  whose connection was gone fell back to the chat model, and Settings
  showed it as "Same as chat".
- **Usage was counted in tokens only.** It did not record whether a call
  was metered, covered by a subscription or paid for by the server's
  shared key.

## What others do

### OpenClaw

[docs.openclaw.ai](https://docs.openclaw.ai/concepts/models)

- **Model names.** A model is written `provider/model`.
  `agents.defaults.model` has a `primary` and an ordered list of
  `fallbacks`. Fallbacks first rotate the auth profiles within a
  provider, then move to the next model.
- **Models per purpose.** There is a separate model for each purpose:
  - `imageModel`, used when the primary model cannot read pictures;
  - `pdfModel`, which falls back to `imageModel`, then to the primary;
  - `utilityModel`, for titles and summaries.

  An unset purpose model uses the next one along its chain.
- **A model picked with `/model` never falls back.** If it cannot be
  reached, the reply fails instead.
- **Credentials and model names are separate.** Auth profiles hold API
  keys or OAuth, and several can serve one provider.
- **A model with no working credential stays listed, marked
  unavailable.** `openclaw models status` lists auth cooldowns, sign-ins
  about to expire and missing credentials, each with what to do about
  it. `--probe` sorts live failures into ok, auth, rate_limit, billing,
  timeout, format and no_model.
- **Cost.**
  - It is input, output, cache read and cache write tokens, each priced
    per million.
  - Prices are taken from what the provider billed, else the local
    config, else the hosted catalog.
  - A tiered price is chosen by the size of the whole prompt.
  - An unknown price is left out, never shown as $0.
  - Subscription sessions show tokens and the provider's quota windows
    ("X% left", with reset times), not dollars
    ([token use](https://docs.openclaw.ai/reference/token-use),
    [usage tracking](https://docs.openclaw.ai/concepts/usage-tracking)).

### OpenCode

[opencode.ai/docs](https://opencode.ai/docs/models/)

- **Model names and choosing one.** A model is written
  `provider/model`, optionally with a variant such as a reasoning
  effort. `/models` lists only enabled models whose provider is
  available. A pick there applies to the session and leaves the config
  alone.
- **`small_model`** does titles and other light work and falls back to
  the main model. This is from search snippets of the config docs, so
  partly verified.
- **Console (formerly Zen)** is a pay-as-you-go gateway with one key.
  - It lives under `https://opencode.ai/inference/`, with a path per
    wire: Responses for GPT and Grok, Messages for Claude, Gemini's own
    for Gemini, and Chat Completions for the open models.
  - Console translates between wires, so any text model is reachable
    over any of them.
  - The listing is `GET /inference/v1/models`.
  - Prices are per million tokens, with cache read and cache write
    columns ([docs](https://opencode.ai/docs/zen/)).
- **Go** is a subscription: $10 a month, Plus at $40.
  - It lives at `https://opencode.ai/inference/go`, and the listing is
    `/go/v1/models`.
  - `GET /go/v1/usage` answers rolling, weekly and monthly percent used,
    with reset times.
  - A 403 means the key does not cover Go.
  - Third-party clients are asked to send their own User-Agent and a
    stable `x-opencode-session` header per conversation
    ([docs](https://opencode.ai/docs/go/)).
- **models.dev catalog.** OpenCode's team keeps the models.dev catalog.
  It lists both services, as `opencode` and `opencode-go`.

### Catalogs

**[models.dev](https://models.dev/api.json)**

- Its `cost` is in USD per million tokens:
  `{input, output, cache_read?, cache_write?, reasoning?, context_over_200k?, tiers?}`.
  A context tier reads `{tier: {type: "context", size: 272000}}`.
- There is no subscription flag. A subscription plan is a separate
  provider, such as `zai-coding-plan` or `kimi-code-plan-global`, whose
  models cost 0.
- About 450 models have no `cost` at all, which means unknown, not free.

**[OpenRouter's listing](https://openrouter.ai/api/v1/models)**

- Its `pricing` holds strings in USD per token: `prompt`, `completion`,
  `input_cache_read`, `input_cache_write`, and more.

### Models per task elsewhere

- **[Cherry Studio](https://cherryai.com/docs/en/pre-basic/settings/default-models/)**
  has four default models: the assistant, a Quick model (topic naming,
  prompt optimizing), Translate and Draw. One known bug: the topic-naming
  model was hidden and inherited the chat's reasoning effort.
- **LobeHub** has a "service model" tab for topic naming, translation
  and compression.
- **[Open WebUI](https://docs.openwebui.com/features/administration/task-models/)**
  has an external and a local task model, both defaulting to "Current
  Model". It warns against reasoning models for titles, whose thinking
  eats the budget. One bug sent a local chat's titles to the external
  task model.
- **[Zed](https://zed.dev/docs/ai/agent-settings)** has a default model
  plus models for the inline assistant, commit messages, thread
  summaries, compaction and subagents. An unset one, or one that is
  unavailable, uses the thread's model.

### Subscription against metered

- **Claude Code** shows a session cost for API use. A Pro or Max
  subscriber sees plan bars for the 5-hour and weekly windows instead,
  and its docs say the dollar figure does not apply to them
  ([costs](https://code.claude.com/docs/en/costs)).
- **Codex** shows a "5h limit" and a "Weekly limit", each with percent
  left and the reset time. Only third-party pages describe it.
- **OpenClaw and OpenCode Go** show tokens and quota for subscriptions,
  never dollars.

## What carries over

1. **Models come from connections; each task picks a model from any
   connection.** A short list of named tasks, each saying "uses the chat
   model" when unset. Cherry Studio, Zed and OpenClaw all have this
   shape.
2. **A gone pick stays visible, marked unavailable, with a way to fix
   it.** It is never silently replaced and shown as if it were still the
   choice (OpenClaw). Gamma's tasks still run, on the chat model, so a
   background job such as the extension's clip does not lose its
   metadata. They say so wherever the pick shows.
3. **One menu.**
   - It is searchable across connections and grouped by connection, with
     favorites first.
   - Facts come from the live listing and models.dev, never from tables
     in the code: tools, pictures, context and price.
4. **Cost only where money is spent per token.**
   - It is cache-aware and tiered by the size of the prompt.
   - An unknown price is shown as unknown.
   - Subscriptions show tokens and quota windows, and shared connections
     show tokens against the allowance.
5. **Left out.** Two things were not taken:
   - fallback chains on a failed call, since Gamma fails visibly and the
     user picks again;
   - per-task reasoning effort.

As built: [dev/ai.md](../dev/ai.md) "Models for each task", "The model menu",
"Other services" and "Token usage".
