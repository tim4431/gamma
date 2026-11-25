# Gemini through a Google sign-in

The question: can Gamma reach Gemini through the user's Google account,
the way it reaches ChatGPT through a ChatGPT sign-in (Codex CLI's flow),
and on what terms? Surveyed on 2026-11-23 from Gemini CLI's own source
(`packages/core/src/code_assist/`: `oauth2.ts`, `server.ts`, `setup.ts`,
`types.ts`, `converter.ts`, and their tests), its documentation, and the
public reports of account suspensions. The design built from it is
[dev/ai.md](../dev/ai.md#the-gemini-sign-in).

## What Google's terms say

Gemini CLI's documentation says it directly. Using the services behind
Gemini CLI, such as the Code Assist backend, from third-party software
with Gemini CLI's OAuth sign-in violates the applicable terms, and may get
the account suspended or terminated
([terms and privacy](https://geminicli.com/docs/resources/tos-privacy/)).
The FAQ says such use may bring an immediate suspension, and names a
Vertex AI or Google AI Studio API key as the supported way for third-party
tools ([FAQ](https://geminicli.com/docs/resources/faq/)).

Google enforces it. Users of other tools that borrowed the sign-in report
suspensions that also locked them out of Gemini CLI and Code Assist,
because the abuse checks are shared
([discussion #20632](https://github.com/google-gemini/gemini-cli/discussions/20632)).
One user was suspended after linking an account to an editor extension
([issue #25744](https://github.com/google-gemini/gemini-cli/issues/25744)).
Google described an automated unban for recently flagged accounts and said
a second flagged violation is permanent. Appeals go through a form, and
some report a suspension that stayed after one. The reports don't say
which patterns trigger it.

This is the same position as Anthropic's: Gamma left out Claude's
subscription sign-in for its terms. OpenAI is the exception: Codex CLI's
sign-in is open to other clients. The developer asked for the Gemini
sign-in anyway, so it is built with the warning on its card in the
connect dialog, kept off the chat's setup card, and it never claims to be
Gemini CLI (its user agent is `Gamma`).

## Retired for individual accounts (found 2026-11-24)

The first real sign-in, with the developer's own account, reached Google
and was refused at setup. `loadCodeAssist` answered an ineligible tier
whose reason reads "This client is no longer supported for Gemini Code
Assist for individuals. To continue using Gemini, please migrate to the
Antigravity suite of products: https://antigravity.google". Gemini CLI
itself gets the same answer, so the survey above had missed the change:

- On 2026-06-18 Gemini CLI and the Code Assist IDE extensions stopped
  serving individual accounts: the free tier, Google AI Pro and Google AI
  Ultra. Accounts with an active AI Pro plan get the same refusal
  ([gemini-cli #28229](https://github.com/google-gemini/gemini-cli/issues/28229),
  [Google AI forum](https://discuss.ai.google.dev/t/gemini-code-assist-entitlement-never-provisioned-for-active-google-ai-pro-account-not-eligible-for-gemini-code-assist-for-individuals-on-all-three-clients/185120),
  [a guide to the error](https://gptprompts.ai/ai-errors-and-fixes/gemini-code-assist-client-no-longer-supported),
  [a write-up of the shutdown](https://www.aibuilderclub.com/blog/google-kills-gemini-cli-june-18-2026)).
  The sign-in is retired, not refused for a ban or a quota.
- Organizations on Gemini Code Assist Standard or Enterprise keep access.
  Those licenses live on a Google Cloud project the user names (Gemini
  CLI's `GOOGLE_CLOUD_PROJECT`).
- Google's replacement for individuals is Antigravity. Using Antigravity's
  sign-in from another app is what the ban reports above were about, so
  posing as that client is no way on.

So the sign-in stays for an organization's license. It asks for the
project, and Google's refusal of an individual account is explained in
plain words with the two ways left: an organization's project, or a
Gemini API key.

## The sign-in

- **The client.** Gemini CLI's is a Google "Desktop app" OAuth client,
  `681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com`,
  whose secret ships in the CLI's source, as Google's guidance for
  installed apps allows. Scopes: `cloud-platform`, `userinfo.email`,
  `userinfo.profile`.
- **Two endings.** `authWithWeb` listens on a free port and redirects to
  `http://127.0.0.1:<port>/oauth2callback`; a desktop client may use any
  loopback port. `authWithUserCode`, for a browser elsewhere, redirects to
  `https://codeassist.google.com/authcode`, a Google page showing the code
  to paste, with PKCE. A desktop client gets no device codes from Google.
- **Tokens.** `access_type=offline` asks for a refresh token. Gamma also
  sends `prompt=consent` so one comes back on every sign-in, not only the
  first. Google doesn't rotate refresh tokens.

## The Code Assist backend

- **Endpoint.** `https://cloudcode-pa.googleapis.com/v1internal:<method>`,
  POST with JSON; a stream is `:streamGenerateContent?alt=sse`. Gemini
  CLI's reader joins an event's `data:` lines up to the blank line.
- **Setup** (`setup.ts`). `loadCodeAssist` `{metadata: {ideType,
  platform, pluginType: "GEMINI"}}` answers `currentTier`,
  `allowedTiers`, `ineligibleTiers`, `paidTier` and
  `cloudaicompanionProject`. An account with a current tier and a project
  is set up. Otherwise the default allowed tier is onboarded with
  `onboardUser` `{tierId, metadata}`, a long-running operation polled
  through `GET v1internal/<operation name>` every 5 s, whose
  `response.cloudaicompanionProject.id` is the project. The free tier's
  project is Google's own, so a request naming one fails. A tier with
  `userDefinedCloudaicompanionProject` needs the user's own Google Cloud
  project (`GOOGLE_CLOUD_PROJECT`), which Gamma doesn't ask for. An
  ineligible tier may carry `VALIDATION_REQUIRED` and a `validationUrl`,
  where the account must be verified first.
- **A call.** `{model, project, user_prompt_id, request:
  {contents, systemInstruction, tools, toolConfig, generationConfig,
  session_id, …}}`; the answer is `{response: {candidates, usageMetadata,
  modelVersion}, traceId}`, Gemini's own `GenerateContentResponse` wrapped.
- **Models.** No listing call. `retrieveUserQuota` `{project}` answers
  `{buckets: [{modelId, tokenType, remainingFraction, resetTime}]}`, one
  bucket per model the account may use, so the buckets are the model list
  and the quota at once.

## Gemini's request rules that matter here

- Thinking counts toward `maxOutputTokens`. A small cap with a high
  budget ends in an empty `MAX_TOKENS` reply. `thinkingBudget` works on
  Gemini 2.5 and, for compatibility, on Gemini 3; `thinkingLevel` is
  Gemini 3's only and a 2.5 model refuses it.
- Gemini 3 returns a `thoughtSignature` on a step's first function call
  and refuses the next round of a tool loop without it. Google's docs give
  `skip_thought_signature_validator` for history whose signatures are
  lost.
- An empty text part is refused.
- `parametersJsonSchema` takes a tool's JSON Schema as written, where
  `parameters` takes only an OpenAPI subset.

## Still open

- The one real sign-in confirmed the client, the scopes and the code
  exchange, and met the retirement at setup. No organization's license has
  been tried: the project path (load and onboard with the project, the
  stream, the quota) is checked against fakes only.
- Whether the bucket `modelId`s are always valid `model` values (a
  variant suffix, say) is unchecked.
