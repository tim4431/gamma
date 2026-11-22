"""Named services: the presets the connect dialog offers. A preset is data,
never code: a protocol, that service's endpoint and its key hints. An entry
made from one stores only protocol + base URL (``service_of`` recognises
the pair again), so a preset can be added, renamed or dropped without
touching saved entries.

Fields — ``id``, ``label`` (an entry's name when it has none of its own),
``protocol``, ``base_url``, ``key_placeholder`` and ``key_url`` (the key
field's hint and its "Get a key at" link) are required. The optional ones
change nothing when absent:

- ``group`` / ``plan``: the dialog groups the presets by vendor and names
  each one's plan. The labels are a small fixed vocabulary the browser
  translates (``SERVICE_WORDING`` in frontend/src/settings/providerEditor.js
  names them for the catalog), and so is ``note``.
- ``note``: a sentence the dialog shows under the plan.
- ``catalog``: the models.dev provider key of the service, for model facts
  and prices (``Protocol.catalog_hints``).
- ``cache_key``: the endpoint takes OpenAI's ``prompt_cache_key``.
- ``max_tokens``: the chat's reply cap. Thinking counts toward it on these
  services, so it is above the default, bounded by the model's own output
  limit (``ai_catalog.reply_cap``).
- ``billing``: how a call through it is paid for (``ai_settings.billing_of``):
  ``"subscription"`` (a coding plan's key) or ``"local"`` (a server on this
  computer or network); absent, per token (``"api"``).
- ``usage``: the quota reader of a subscription (``ai_protocols.quota``),
  whose windows Settings draws as bars.
- ``key_optional``: an entry may be saved without a key (a local server).
- ``versioned``: the base URL already ends in its version prefix, so
  ``base.api_url`` appends no ``/v1`` (Gemini's ``/v1beta/openai``).
- ``session_header``: the header the service asks a client to send a stable
  id per conversation in (the chat's prompt-cache key), with a user agent
  of its own (OpenCode).
"""

# The chat's reply cap where a service sets none, and the one a service
# with a ``max_tokens`` falls back to for a model nobody knows the limit of.
DEFAULT_MAX_TOKENS = 8192
_THINKING_CAP = 32768

_KEY = "API key"
_KEY_CN = "API key (China)"
_PLAN = "Coding plan subscription"
_PLAN_CN = "Coding plan subscription (China)"
_PAYG = "Pay as you go"
_SUBSCRIPTION = "Subscription"
_LOCAL = "On this computer or network"
_CODING_TOOLS_ONLY = ("Alibaba's terms allow the Coding Plan only inside coding tools such as Qwen Code, "
                      "and other use may get the plan suspended.")
_OPENCODE_SESSION = "x-opencode-session"


def _service(id, label, group, plan, base_url, key_url, key_placeholder="sk-…", **extra):
    return {"id": id, "label": label, "group": group, "plan": plan, "protocol": "openai",
            "base_url": base_url, "key_placeholder": key_placeholder, "key_url": key_url, **extra}


SERVICES = [
    _service("deepseek", "DeepSeek", "DeepSeek", _KEY, "https://api.deepseek.com",
             "https://platform.deepseek.com/api_keys", catalog="deepseek", max_tokens=_THINKING_CAP),
    _service("kimi", "Kimi", "Kimi", _KEY, "https://api.moonshot.ai",
             "https://platform.kimi.ai/console/api-keys",
             catalog="moonshotai", cache_key=True, max_tokens=_THINKING_CAP),
    _service("kimi-cn", "Kimi (China)", "Kimi", _KEY_CN, "https://api.moonshot.cn",
             "https://platform.moonshot.cn/console/api-keys",
             catalog="moonshotai", cache_key=True, max_tokens=_THINKING_CAP),
    _service("kimi-code", "Kimi Code", "Kimi", _PLAN, "https://api.kimi.com/coding/v1",
             "https://www.kimi.com/code/console", catalog="kimi-code-plan-global", max_tokens=_THINKING_CAP,
             billing="subscription"),
    _service("qwen", "Qwen", "Qwen", _KEY, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "https://modelstudio.console.alibabacloud.com/", catalog="alibaba", max_tokens=_THINKING_CAP),
    _service("qwen-cn", "Qwen (China)", "Qwen", _KEY_CN, "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "https://bailian.console.aliyun.com/", catalog="alibaba-cn", max_tokens=_THINKING_CAP),
    _service("qwen-coding", "Qwen Coding Plan", "Qwen", _PLAN, "https://coding-intl.dashscope.aliyuncs.com/v1",
             "https://modelstudio.console.alibabacloud.com/", "sk-sp-…",
             catalog="alibaba", max_tokens=_THINKING_CAP, note=_CODING_TOOLS_ONLY, billing="subscription"),
    _service("qwen-coding-cn", "Qwen Coding Plan (China)", "Qwen", _PLAN_CN, "https://coding.dashscope.aliyuncs.com/v1",
             "https://bailian.console.aliyun.com/", "sk-sp-…",
             catalog="alibaba-cn", max_tokens=_THINKING_CAP, note=_CODING_TOOLS_ONLY, billing="subscription"),
    _service("glm", "GLM", "GLM", _KEY, "https://api.z.ai/api/paas/v4",
             "https://z.ai/manage-apikey/apikey-list", "", catalog="zai", max_tokens=_THINKING_CAP),
    _service("glm-cn", "GLM (China)", "GLM", _KEY_CN, "https://open.bigmodel.cn/api/paas/v4",
             "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys", "", catalog="zhipuai", max_tokens=_THINKING_CAP),
    _service("glm-coding", "GLM Coding Plan", "GLM", _PLAN, "https://api.z.ai/api/coding/paas/v4",
             "https://z.ai/manage-apikey/apikey-list", "", catalog="zai", max_tokens=_THINKING_CAP,
             billing="subscription"),
    _service("glm-coding-cn", "GLM Coding Plan (China)", "GLM", _PLAN_CN, "https://open.bigmodel.cn/api/coding/paas/v4",
             "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys", "", catalog="zhipuai", max_tokens=_THINKING_CAP,
             billing="subscription"),
    _service("openrouter", "OpenRouter", "OpenRouter", _KEY, "https://openrouter.ai/api/v1",
             "https://openrouter.ai/settings/keys", "sk-or-…"),
    # OpenCode's Console: Zen bills credits per token, Go is a monthly
    # subscription with 5-hour, weekly and monthly limits. Both translate
    # between wire formats, so Chat Completions reaches every text model.
    _service("opencode", "OpenCode Zen", "OpenCode", _PAYG, "https://opencode.ai/inference/openai/v1",
             "https://opencode.ai/auth", catalog="opencode", max_tokens=_THINKING_CAP,
             session_header=_OPENCODE_SESSION),
    _service("opencode-go", "OpenCode Go", "OpenCode", _SUBSCRIPTION, "https://opencode.ai/inference/go/openai/v1",
             "https://opencode.ai/auth", catalog="opencode-go", max_tokens=_THINKING_CAP,
             billing="subscription", usage="opencode-go", session_header=_OPENCODE_SESSION),
    _service("gemini", "Gemini", "Gemini", _KEY, "https://generativelanguage.googleapis.com/v1beta/openai",
             "https://aistudio.google.com/apikey", "AIza…", catalog="google", versioned=True),
    _service("xai", "xAI", "xAI", _KEY, "https://api.x.ai/v1", "https://console.x.ai/", "xai-…", catalog="xai"),
    _service("mistral", "Mistral", "Mistral", _KEY, "https://api.mistral.ai/v1",
             "https://console.mistral.ai/api-keys", "", catalog="mistral"),
    _service("groq", "Groq", "Groq", _KEY, "https://api.groq.com/openai/v1",
             "https://console.groq.com/keys", "gsk_…", catalog="groq"),
    # Servers on this computer or network: an address preset, keyless. The
    # network guards still apply.
    _service("ollama", "Ollama", "Ollama", _LOCAL, "http://localhost:11434/v1", "", "",
             billing="local", key_optional=True),
    _service("lmstudio", "LM Studio", "LM Studio", _LOCAL, "http://localhost:1234/v1", "", "",
             catalog="lmstudio", billing="local", key_optional=True),
]


def service_of(conf: dict) -> dict | None:
    """The preset an entry (or a runtime conf) was made from: the one with
    its protocol and base URL, None for any other endpoint."""
    protocol = conf.get("protocol")
    base = (conf.get("base_url") or "").strip().rstrip("/")
    return next((s for s in SERVICES if s["protocol"] == protocol and s["base_url"] == base), None)


def versioned_base(base_url: str) -> bool:
    """Whether ``base_url`` is a preset's that already ends in its version
    prefix (``versioned``), so no ``/v1`` goes after it."""
    base = (base_url or "").strip().rstrip("/")
    return any(s.get("versioned") and s["base_url"] == base for s in SERVICES)
