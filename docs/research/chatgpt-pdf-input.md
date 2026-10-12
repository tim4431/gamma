# PDF files over the ChatGPT sign-in

Whether the Codex backend that a ChatGPT sign-in talks to
(`chatgpt.com/backend-api/codex/responses`) takes a PDF as an `input_file`
part. Surveyed in October 2026 from source, OpenAI's docs and public
reports, then settled by a live request. How Gamma sends PDFs now is in
[ai.md](../dev/ai.md) (`native_pdf`).

## What Gamma assumed

Until October 2026 the `chatgpt` protocol set `native_pdf = False` ("the
Codex backend refuses input_file parts"). This came from a test made when
the sign-in was added in July 2026. The error it returned was not recorded.
The chat's Full PDF switch therefore started off for these models. Turned on
by hand, the backend still sent the file first and resent it as text after
a 4xx. Gamma's part always carries a `filename` (`document-N.pdf`) and
inline `file_data`, and never a `detail`.

The API-key route is a separate matter. Chat Completions (`file`) and the
Responses API (`input_file`) on OpenAI's platform take PDFs, and Gamma sends
them natively there.

## What was found

- **Codex CLI cannot send a file.** `ContentItem` in
  `codex-rs/protocol/src/models.rs` has only `input_text`, `input_image`,
  `input_audio` and `output_text`. The app-server rejects an `input_file`
  itself, before any request goes out: "unknown variant `input_file`". No
  release up to 0.162.1 (2026-10-09) added PDF input. Issue
  [openai/codex#1797](https://github.com/openai/codex/issues/1797) is still
  open. The "Codex doesn't read PDFs" that users report is about the client.
  It says nothing about the backend.
- **One successful request on the backend.** In the 2026-09-02 comment on
  #1797, a patched app-server running on a ChatGPT sign-in sent
  `{"type": "input_file", "filename": …, "file_data":
  "data:application/pdf;base64,…", "detail": "auto"}` to `gpt-5.6-luna`.
  The model read a 65 KB PDF correctly. The same author ships this in
  auto-zettelkasten (September 2026, models `gpt-5.6-luna` and
  `gpt-5.6-terra`).
- **The only failure is confounded.** llxprt-code#2608 (July 2026,
  `gpt-5.6-sol`) got a 400, "Missing required parameter:
  'input[…].content[0]'". Its `input_file` had no `filename`, and a
  missing filename gives that same error. The project switched PDFs off
  for the Codex route without testing a request that had one.
- **OpenAI's adjacent route says files work.** Sign in with ChatGPT's token
  sharing (ChatGPT-plan tokens on `api.openai.com/v1`, launched 2026-09-29)
  lists "Text, images, and files are supported when the selected model
  accepts them" in its preview limitations. The Files upload API is not
  supported there. That page is about a different endpoint, so it is
  evidence about the plan, not about the Codex backend.
- **Other clients play it safe.** OpenClaw and hermes-agent convert or drop
  PDFs on the Codex route. opencode and LiteLLM's `chatgpt/` provider pass
  `input_file` through unchanged, and nobody has reported a rejection from
  them.
- **Still unknown:** whether `file_id` or `file_url` works, whether
  acceptance depends on the model, and whether `detail` is needed.
  OpenAI's docs make `detail` optional.

## Live test, 2026-10-12

A one-page PDF reading "The codeword is PLUM-4271" was built with Gamma's
own `chatgpt` wire. The prompt asked for the codeword without stating it,
and the request went to `gpt-6-astra` on a ChatGPT sign-in:

| Request | Result |
|---|---|
| text only (control) | 200, "NONE" |
| Gamma's shape (`filename`, inline `file_data`) | 200, "PLUM-4271" |
| the same plus `"detail": "auto"` | 200, "PLUM-4271" |
| `filename` left out | 400, "Missing required parameter: 'input[0].content[0]'." |

The backend reads inline PDFs and does not need `detail`. A missing
`filename` produces the same error llxprt-code hit, which explains that
failure.

As a result `chatgpt` no longer overrides `native_pdf`, so the switch
defaults on as it does for API keys. The text retry after a 4xx stays for a
model that refuses. Other sign-in models, very large PDFs, and the `file_id`
and `file_url` forms were not tested.

## Prompt cache, 2026-10-12

The PDF switch then started sending the file with every message, kept on
the oldest turn ([ai_context.md](../dev/ai_context.md#with-the-pdf-switch-on)).
To check that the sign-in's cache serves it, an 8-page generated PDF
(17 KB, about 4,900 input tokens) was sent to `gpt-6-astra` in conversations
laid out that way, with one cache key per conversation.

- **Six messages in one conversation.** The first came in uncached. Messages
  2 to 6 read 95 to 97% of their input from the cache: 4,864 tokens, then
  4,992. In all, 80% of the conversation's input tokens were cached. The
  cached count grows in steps, so a short new message adds to it only every
  few turns.
- **An earlier three-message run** cached message 2 (96%) but not message 3
  (0%), with the same layout. OpenAI's docs say a request reuses an entry
  only when it reaches a machine that holds one, and the six-message run
  never missed. That makes the 0% a routing miss.
- **Explicit breakpoints are refused.** A `prompt_cache_breakpoint` on the
  PDF turn's text got a 400: "prompt_cache_breakpoint is not supported on
  this model". Only the provider's own breakpoint, at the end of the latest
  user message, is available there.

The page images OpenAI renders from a PDF did not show up in the count:
4,900 tokens is about the size of the PDF's text alone. Whether the backend
renders pages for this model, or bills them some other way, was not looked
into.
