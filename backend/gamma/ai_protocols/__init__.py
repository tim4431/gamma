"""The AI wire protocols, one adapter each (base.Protocol): everything that
differs between providers lives on its adapter, never as a protocol branch
in a route. Adding a provider that speaks a new wire is one module here and
one line in ``WIRES``; a new service on an existing wire is a ``SERVICES``
preset (``services.py``).

- ``anthropic`` — Anthropic Messages API (Anthropic, and services that
  speak it behind their own base URL)
- ``openai`` — OpenAI Chat Completions (OpenAI, DeepSeek, Kimi, Qwen, GLM,
  OpenRouter, local servers …); switches to ``openai-responses`` for
  OpenAI's own tool calls
- ``chatgpt`` — ChatGPT subscription sign-in (the Codex Responses backend)
"""

from .anthropic import Anthropic
from .base import SPEED_ORDER, Protocol
from .chatgpt import ChatGPT
from .openai import OpenAIChat
from .responses import OPENAI_RESPONSES
from .quota import READERS
from .services import DEFAULT_MAX_TOKENS, SERVICES, service_of

# Every wire a call may go over, by id; the ones an entry may name come
# first, in the order the settings form offers them.
WIRES = {p.id: p for p in (Anthropic(), OpenAIChat(), ChatGPT(), OPENAI_RESPONSES)}

# The protocols a provider entry may name.
PROTOCOLS = {pid: p for pid, p in WIRES.items() if p.entry}


def get(protocol_id) -> Protocol | None:
    """The adapter of a wire protocol id, None for an unknown one."""
    return WIRES.get(protocol_id)


def of(conf: dict) -> Protocol:
    """The adapter of a resolved provider entry (ai_runtime's ``conf``)."""
    return WIRES[conf["protocol"]]


def quota_reader(conf: dict):
    """The quota reader of a connection (``conf``: a runtime conf, or an
    entry's protocol and base URL): its protocol's own (the ChatGPT
    sign-in), else the one its service preset names (``usage``: OpenCode
    Go); None for a connection whose plan has no quota to read."""
    proto = WIRES.get(conf.get("protocol"))
    if proto is not None and proto.has_account_usage:
        return proto
    return READERS.get((service_of(conf) or {}).get("usage"))


__all__ = ["DEFAULT_MAX_TOKENS", "PROTOCOLS", "SERVICES", "SPEED_ORDER", "WIRES", "Protocol", "get",
           "of", "quota_reader", "service_of"]
