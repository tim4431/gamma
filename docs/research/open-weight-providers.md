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
   thinking models have the same rule. Assistant tools are on by default
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
