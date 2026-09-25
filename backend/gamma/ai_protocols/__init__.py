"""The AI wire protocols, one adapter each (base.Protocol): everything that
differs between providers lives on its adapter, never as a protocol branch
in a route. Adding a provider that speaks a new wire is one module here and
one line in ``WIRES``; a new service on an existing wire is a ``SERVICES``
preset.

- ``anthropic`` — Anthropic Messages API (Anthropic, Kimi, GLM, …)
- ``openai`` — OpenAI Chat Completions (OpenAI, DeepSeek, OpenRouter, local
  servers …); switches to ``openai-responses`` for OpenAI's own tool calls
- ``chatgpt`` — ChatGPT subscription sign-in (the Codex Responses backend)
"""

from .anthropic import Anthropic
from .base import Protocol
from .chatgpt import ChatGPT
from .openai import OpenAIChat, is_openai_platform
from .responses import OPENAI_RESPONSES

# Every wire a call may go over, by id; the ones an entry may name come
# first, in the order the settings form offers them.
WIRES = {p.id: p for p in (Anthropic(), OpenAIChat(), ChatGPT(), OPENAI_RESPONSES)}

# The protocols a provider entry may name.
PROTOCOLS = {pid: p for pid, p in WIRES.items() if p.entry}

# Named services the settings form offers next to the raw protocols: a
# protocol plus that service's endpoint. An entry made from one is just
# protocol + base URL; the preset only names it (form, provider label).
SERVICES = [
    {"id": "deepseek", "label": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com"},
]


def get(protocol_id) -> Protocol | None:
    """The adapter of a wire protocol id, None for an unknown one."""
    return WIRES.get(protocol_id)


def of(conf: dict) -> Protocol:
    """The adapter of a resolved provider entry (ai_runtime's ``conf``)."""
    return WIRES[conf["protocol"]]


__all__ = ["PROTOCOLS", "SERVICES", "WIRES", "Protocol", "get", "is_openai_platform", "of"]
