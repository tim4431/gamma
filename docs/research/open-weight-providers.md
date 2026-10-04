# Chinese and open-weight models through Gamma's AI wires

Surveyed October 2026 against the providers' own API docs (DeepSeek, Kimi /
Moonshot, Qwen on Alibaba Model Studio, Zhipu GLM / Z.ai, MiniMax) and the
code at the time. No live calls were made, so each gap below rests on the
code plus the provider's documented contract. The mechanics are in
[ai.md](../dev/ai.md) (providers, wires, chat, the agent loop), which this
note does not repeat.

## How they reach Gamma

There is no adapter per vendor. A Chinese or open-weight model is an entry
on one of the two key wires, `openai` (Chat Completions) or `anthropic`
(Messages), with a custom base URL. DeepSeek is the only named preset
(`SERVICES` in `ai_protocols/__init__.py`). Models the listing does not
return can be typed in by hand in the connect form. Context windows and
effort levels come from models.dev when the vendor's listing omits them.
Nothing blocks a private or loopback base URL, so a local Ollama, vLLM or
LM Studio server works as a custom endpoint.

What works today, on either wire: plain chat, streamed translation, token
counts (`stream_options.include_usage`), the native-PDF fallback to text
(any 4xx on file parts), `max_tokens` rather than `max_completion_tokens`,
and no OpenAI- or Anthropic-only fields (cache keys, service tiers, cache
breakpoints) sent to another host. The CJK token estimate (one token per
character) errs on the high side, which is the safe side.

## Endpoints

Both wires append a fixed path to the base URL: `/v1/chat/completions` and
`/v1/models` (`openai.py` `request`, `base.py` `models_request`), and
`/v1/messages` (`anthropic.py`). Settings strip only a trailing `/`
(`ai_settings.apply_provider_fields`). So the base URL entered must be the
vendor's URL without its `/v1`, and a vendor whose path is not `/v1`
cannot be reached on the `openai` wire at all.

| Service | Documented OpenAI base | Enter on `openai` | Anthropic base (`anthropic` wire) |
|---|---|---|---|
| DeepSeek | `https://api.deepseek.com` | as is (preset) | `https://api.deepseek.com/anthropic` |
| Kimi (Moonshot) | `https://api.moonshot.cn/v1`, `.ai/v1` | without `/v1` | `https://api.moonshot.cn/anthropic` |
| Qwen (Model Studio) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | without `/v1` | `https://dashscope.aliyuncs.com/apps/anthropic` |
| GLM (Zhipu / Z.ai) | `https://open.bigmodel.cn/api/paas/v4` | unreachable (`v4`) | `https://open.bigmodel.cn/api/anthropic`, `https://api.z.ai/api/anthropic` |
| MiniMax | `https://api.minimaxi.com/v1`, `api.minimax.io/v1` | without `/v1` | `https://api.minimax.io/anthropic` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | without `/v1` | — |
| Volcengine Ark (Doubao) | `https://ark.cn-beijing.volces.com/api/v3` | unreachable (`v3`) | — |
| Baidu Qianfan | `https://qianfan.baidubce.com/v2` | unreachable (`v2`) | — |
| Ollama / vLLM / LM Studio | `http://host:port/v1` | without `/v1` | — |

Entering the documented URL doubles the version (`…/v1/v1/chat/completions`).
The result is a 404, which shows as `bad_endpoint` ("check the base URL")
and gives no hint that only `/v1` was wrong. The user guide's "any
OpenAI-compatible gateway" promises more than this delivers.

## Gaps, ranked

1. **Reasoning is dropped between tool rounds.** The OpenAI wire reads
   `delta.content` and `delta.tool_calls` only. `reasoning_content` is
   never read, and the assistant turn the agent loop replays
   (`ai_agent.py`, the `{"role": "assistant", "content", "tool_calls"}`
   turn) has nowhere to keep it. DeepSeek V4 thinks by default. When the
   request carries `tools`, DeepSeek requires every earlier assistant
   turn's `reasoning_content` back and answers 400 without it. Kimi's
   docs ask for the same replay but call a missing `reasoning_content`
   lost reasoning context, not an error (see the recheck below).
   Assistant tools are on by default
   and reading tools are Allow, so with the shipped DeepSeek preset the
   round after the first tool call fails. Chats that never call a tool
   are unaffected. The Anthropic wire has the same blind spot: it
   ignores `thinking_delta` and `signature_delta` and replays no thinking
   blocks, which matters for the vendors' Anthropic endpoints that think
   by default. Whether those endpoints refuse the replay was not checked.
   Fix: the wires yield a `("reasoning", text)` event, the loop keeps it
   on the assistant turn, and `OpenAIChat.request` writes it back as
   `reasoning_content` on turns that carry `tool_calls` (the Anthropic
   wire: the thinking blocks with their signatures).
2. **A version path other than `/v1` is unreachable, and a pasted `/v1`
   doubles.** See the table. Fix: when the base URL already ends in a
   version segment (`/v\d+`), append only `/chat/completions` and
   `/models`. Then add `SERVICES` presets for Kimi, Qwen, GLM, MiniMax and
   SiliconFlow with their key URLs, so most users never type an endpoint.
3. **Thinking that arrives inside the text shows as text.** MiniMax's
   OpenAI endpoint wraps thinking in `<think>…</think>` inside `content`
   (unless `reasoning_split` is sent). So does a local server running a
   reasoning model without a reasoning parser (Qwen3, DeepSeek-R1
   distills). The chat shows the thinking as the reply, and metadata
   extraction's first-`{`-to-last-`}` match (`routers/metadata.py`) can
   pick up braces from it. Fix: fold a leading `<think>` block out of the
   OpenAI stream, or steer MiniMax to its Anthropic endpoint.
4. **Qwen3 open-weight models on Model Studio refuse non-streamed calls.**
   They think by default, and a non-streamed request is a 400 unless it
   sends `enable_thinking: false`. Gamma's non-streamed callers are the
   Test probe (which also runs right after Connect), metadata extraction,
   `/metadata/cite` and the non-streamed translation. So the connection
   reads as broken while chat works. `qwen3-max`, `qwen3.5-plus` and
   similar commercial models are not affected. Fix: on a compatible
   server, have `call_ai` stream and join the reply (what `streams_only`
   already does for the Codex backend).
5. **Pictures go to text-only models.** `view_pdf_page`, `view_ink`,
   selection crops and pasted images are offered whatever the model takes.
   DeepSeek, Kimi K2, MiniMax-M2 and GLM's text models then reject the
   `image_url` parts. Unlike native PDFs, images get no text fallback.
   models.dev carries `modalities.input`, so `ai_catalog` could read it
   beside the window and efforts, and the chat could drop the picture
   tools and warn on attached images.
6. **No vendor thinking switch, and a tight output cap.** Effort goes out
   as `reasoning_effort`. DeepSeek and GLM switch thinking with `thinking:
   {type}` and Qwen with `enable_thinking`, so "none" turns nothing off and
   the user cannot avoid gap 1 by disabling thinking. The chat's
   `max_tokens` is 8192, and DeepSeek counts reasoning toward it at its
   default high effort, so long answers will hit the "Reply cut off"
   pill more often than on OpenAI or Anthropic.

## What to do first

Gap 1 breaks the one Chinese provider Gamma ships, in its default
configuration, so it comes first. It needs a test in `tests/test_ai_wire.py`
that streams `reasoning_content` with a tool call and checks the next
request carries it back. Gap 2 is a small change in the two path builders
plus presets, and it unlocks GLM, Doubao and Qianfan. Gaps 3 and 4 are
one-host quirks worth a few lines each. Gap 5 is general (it hits text-only
OpenAI-compatible models of any origin) and needs a new model fact.

Sources: [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
[Kimi thinking models](https://platform.kimi.ai/docs/guide/use-kimi-k2-thinking-model),
[Model Studio deep thinking](https://www.alibabacloud.com/help/en/model-studio/deep-thinking),
[Z.ai quick start](https://docs.z.ai/guides/overview/quick-start),
[MiniMax OpenAI API](https://platform.minimax.io/docs/api-reference/text-openai-api).

## Recheck, 3 October 2026

Re-read against the code after the data-model refactor (commit
`066707b9`) and against the vendors' docs the same day. All six gaps
stand: nothing reads `reasoning_content`, `thinking_delta` or a `<think>`
block, the path builders still append `/v1/...`, and `ai_catalog` still
reads only windows and efforts from models.dev. What changed is on the
vendors' side.

**DeepSeek.** The current models are `deepseek-v4-pro` (text only) and
`deepseek-flash` / `deepseek-v4-flash` (takes images), both 1M context,
384K output. `reasoning_effort` takes `none`, `low`, `high`, `max`; left
unset, `max_tokens` defaults to 64K in thinking mode, so Gamma's 8192 is
far below what the vendor expects. The tools-plus-`reasoning_content` 400
is confirmed in the thinking-mode guide. The Anthropic endpoint
(`/anthropic`) takes images and tools but not `document` parts (Gamma's
native-PDF attempt then falls back to text, which is one wasted upload per
chat) and not `cache_control` (Gamma already sends none off anthropic.com).
It maps Claude model names onto its own. `GET /models` exists on the
OpenAI endpoint.

**Kimi.** The docs moved to platform.kimi.ai; the hosts are
`api.moonshot.ai` (global) and `api.moonshot.cn` (China, a separate
account). `kimi-k3` (1M context, images) always thinks and takes
`reasoning_effort` `low` / `high` / `max`; `kimi-k2.6` takes `thinking:
{type, keep}`; `kimi-k2.7-code` errors on `thinking: disabled`. Gap 1 is
softer here than the note said: omitting `reasoning_content` "may lose
reasoning context", no 400. `max_tokens` is marked deprecated in favour of
`max_completion_tokens` but still accepted; the thinking guide asks for at
least 16000. Kimi takes OpenAI's `prompt_cache_key` and
`prompt_cache_options`, and reports `cached_tokens` and
`cache_write_tokens` — Gamma sends its cache key only to api.openai.com,
so this is a cheap win. `GET /v1/models` exists.

**Qwen (Model Studio).** Endpoints are moving to workspace-specific hosts
(`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`,
`.ap-southeast-1.` for Singapore); the plain dashscope hosts still work but
are called deprecated. Thinking is on by default for Qwen3.5 and later;
`enable_thinking` toggles it, `thinking_budget` caps it, and `qwen3.8-*`
also take `reasoning_effort` (`low` / `medium` / `xhigh` per models.dev).
`preserve_thinking` defaults to on for `qwen3.8-max` / `-flash` and then
wants the full `reasoning_content` back, so gap 1 reaches Qwen too. Tools
stream fine (`tool_stream` is only for complex argument schemas). The
Anthropic endpoint (`/apps/anthropic`) takes `cache_control`, `thinking`
with a budget, images, `output_config.effort`, and also serves
`deepseek-v4-*`, `kimi-k3`, `glm-5.3` and `MiniMax-M2.5` on one key — but
has no `/v1/models` (the FAQ says 404), so on Gamma's `anthropic` wire the
connect form cannot list models or ping the key; models must be typed in.
Images: `qwen3.8-max`, `qwen3.7-plus`, `qwen3-vl-plus`; `qwen3.7-max` is
text only. No model listing is documented for compatible-mode either.

**GLM.** Z.ai's OpenAI base is `https://api.z.ai/api/paas/v4` (coding
plan: `/api/coding/paas/v4`; a Responses API at `/api/v1`), China's
`https://open.bigmodel.cn/api/paas/v4` — both still unreachable on the
`openai` wire (gap 2). The Anthropic endpoints `https://api.z.ai/api/anthropic`
and `https://open.bigmodel.cn/api/anthropic` are documented and are the
way in today. `glm-5.3` (1M context, 128K output, text only) can no longer
turn thinking off: `thinking.type` must be `enabled` and `reasoning_effort`
(`low` / `high` / `max`) is the only dial; `glm-5.3-flash` / `-flashx` take
images, video and PDF. `clear_thinking` decides whether earlier
`reasoning_content` is kept; no 400 is documented. Z.ai also has
`tool_stream`, built-in `web_search` and `retrieval` tools, and reports
`prompt_tokens_details.cached_tokens` (Gamma reads it). `GET /models`
exists on `paas/v4`.

**models.dev.** Its entries now carry `modalities.input` for all four
vendors, including `pdf` for `glm-5.3-flash` and `qwen3.8-max`, so gap 5's
"new model fact" is available without a vendor table. Two host-hint
misses: `ai_catalog.catalog_hints` derives names from the base URL's host,
and `dashscope.aliyuncs.com` / `open.bigmodel.cn` / `api.moonshot.cn` match
none of models.dev's `alibaba`, `zhipuai`, `moonshotai` keys, so those
entries fall to the majority vote across providers (which happens to give
1M for the current flagships). `api.deepseek.com`, `api.moonshot.ai` and
`api.z.ai` match.

**Effort levels.** The chat's `effortFor` maps Gamma's `none … max` onto
whatever a model lists, so DeepSeek's and Kimi K3's `low / high / max` and
Qwen3.8's `low / medium / xhigh` work without code; `none` goes out as
`reasoning_effort: none` on the OpenAI wire, which DeepSeek accepts and the
others don't list.

Recheck sources: [DeepSeek chat completion](https://api-docs.deepseek.com/api/create-chat-completion),
[DeepSeek Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api),
[DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing),
[Kimi chat API](https://platform.kimi.ai/docs/api/chat),
[Kimi API overview](https://platform.kimi.ai/docs/api/overview),
[Model Studio OpenAI-compatible chat](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions),
[Model Studio Anthropic-compatible Messages](https://www.alibabacloud.com/help/en/model-studio/anthropic-api-messages),
[Z.ai chat completion](https://docs.z.ai/api-reference/llm/chat-completion),
[Z.ai GLM-5.3](https://docs.z.ai/guides/llm/glm-5.3),
[Zhipu Claude-compatible API](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction),
[models.dev](https://models.dev/api.json).

## What landed, 3 October 2026

The services above are presets under the connect dialog's Other tile
(`ai_protocols/services.py`; the mechanics are in [ai.md](../dev/ai.md)
"Other services"). Gaps 1, 2 and 5 are closed on the `openai` wire, gap 6 in part:

1. Thinking is kept and echoed back, within a reply and across messages.
2. A base URL that ends in a version keeps it; GLM, the coding plans and a
   pasted `/v1` work. Presets cover Kimi, Qwen, GLM and OpenRouter, each
   with its China endpoint and coding subscription where the vendor sells
   one. DeepSeek sells no subscription.
5. Pictures are left out for models that read text only.
6. The reply cap is 32,768 for these presets, bounded by the model's own
   output limit. The vendor thinking switches are still not sent; effort
   goes out as `reasoning_effort`, which all four accept on their current
   models.

Kimi's preset also sends `prompt_cache_key`.

Still open:

- Gap 3: `<think>` blocks inside `content` (local servers without a
  reasoning parser) still show as text.
- Gap 4: Qwen3 open-weight models on Model Studio still refuse the
  non-streamed callers (Test, metadata, citations).
- The Anthropic wire still drops thinking blocks, and the vendors' Anthropic
  endpoints have no presets.
- The chat does not show the thinking text, only keeps it.

Unverified without vendor keys:

- That DeepSeek accepts an empty `reasoning_content` on assistant turns
  that kept none, and refuses nothing on the first request after a chat
  whose earlier replies came from another provider (they carry none at all).
- That Kimi Code admits Gamma's client. Its terms forbid a forged
  User-Agent, and Gamma sends its real one (Python's default), so a gate
  on known coding tools would refuse it with a 403.
- What the coding-plan listings return, and OpenRouter's handling of
  `stream_options` and of `reasoning` echoed on assistant turns.
