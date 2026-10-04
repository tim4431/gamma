"""Named services: the presets the connect dialog offers under its Other
tile. A preset is data, never code: a protocol, that service's endpoint
and its key hints. An entry made from one stores only protocol + base URL
(``service_of`` recognises the pair again), so a preset can be added,
renamed or dropped without touching saved entries.

Fields — ``id``, ``label`` (an entry's name when it has none of its own),
``protocol``, ``base_url``, ``key_placeholder`` and ``key_url`` (the key
field's hint and its "Get a key at" link) are required. The optional ones
change nothing when absent:

- ``group`` / ``plan``: the dialog's Service menu lists the groups, and a
  group of several presets gets a Plan menu of their ``plan`` labels. The
  labels are a small fixed vocabulary the browser translates (``PLAN_LABELS``
  in frontend/src/settings/providerEditor.js names them for the catalog).
- ``note``: a sentence the dialog shows under the Plan menu.
- ``catalog``: the models.dev provider key of the service, for model facts
  when its host names none (``Protocol.catalog_hints``).
- ``cache_key``: the endpoint takes OpenAI's ``prompt_cache_key``.
- ``max_tokens``: the chat's reply cap. Thinking counts toward it on these
  services, so it is above the default; a model whose own output limit is
  known lower gets that (``ai_catalog.output_limit``).
"""

# The chat's reply cap where a service sets none, and the one a service
# with a ``max_tokens`` falls back to for a model nobody knows the limit of.
DEFAULT_MAX_TOKENS = 8192
_THINKING_CAP = 32768

_KEY = "API key"
_KEY_CN = "API key (China)"
_PLAN = "Coding plan subscription"
_PLAN_CN = "Coding plan subscription (China)"
_CODING_TOOLS_ONLY = ("Alibaba's terms allow the Coding Plan only inside coding tools such as Qwen Code, "
                      "and other use may get the plan suspended.")


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
             "https://www.kimi.com/code/console", catalog="kimi-code-plan-global", max_tokens=_THINKING_CAP),
    _service("qwen", "Qwen", "Qwen", _KEY, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "https://modelstudio.console.alibabacloud.com/", catalog="alibaba", max_tokens=_THINKING_CAP),
    _service("qwen-cn", "Qwen (China)", "Qwen", _KEY_CN, "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "https://bailian.console.aliyun.com/", catalog="alibaba-cn", max_tokens=_THINKING_CAP),
    _service("qwen-coding", "Qwen Coding Plan", "Qwen", _PLAN, "https://coding-intl.dashscope.aliyuncs.com/v1",
             "https://modelstudio.console.alibabacloud.com/", "sk-sp-…",
             catalog="alibaba", max_tokens=_THINKING_CAP, note=_CODING_TOOLS_ONLY),
    _service("qwen-coding-cn", "Qwen Coding Plan (China)", "Qwen", _PLAN_CN, "https://coding.dashscope.aliyuncs.com/v1",
             "https://bailian.console.aliyun.com/", "sk-sp-…",
             catalog="alibaba-cn", max_tokens=_THINKING_CAP, note=_CODING_TOOLS_ONLY),
    _service("glm", "GLM", "GLM", _KEY, "https://api.z.ai/api/paas/v4",
             "https://z.ai/manage-apikey/apikey-list", "", catalog="zai", max_tokens=_THINKING_CAP),
    _service("glm-cn", "GLM (China)", "GLM", _KEY_CN, "https://open.bigmodel.cn/api/paas/v4",
             "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys", "", catalog="zhipuai", max_tokens=_THINKING_CAP),
    _service("glm-coding", "GLM Coding Plan", "GLM", _PLAN, "https://api.z.ai/api/coding/paas/v4",
             "https://z.ai/manage-apikey/apikey-list", "", catalog="zai", max_tokens=_THINKING_CAP),
    _service("glm-coding-cn", "GLM Coding Plan (China)", "GLM", _PLAN_CN, "https://open.bigmodel.cn/api/coding/paas/v4",
             "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys", "", catalog="zhipuai", max_tokens=_THINKING_CAP),
    _service("openrouter", "OpenRouter", "OpenRouter", _KEY, "https://openrouter.ai/api/v1",
             "https://openrouter.ai/settings/keys", "sk-or-…"),
]


def service_of(conf: dict) -> dict | None:
    """The preset an entry (or a runtime conf) was made from: the one with
    its protocol and base URL, None for any other endpoint."""
    protocol = conf.get("protocol")
    base = (conf.get("base_url") or "").strip().rstrip("/")
    return next((s for s in SERVICES if s["protocol"] == protocol and s["base_url"] == base), None)
