"""Provider identification and provider_flags.yaml access. Shared by
parse.py (no -- parse stays pure), extract.py, reasoning_convention.py,
cache_convention.py and pricing.py. No dollars here.

Two different things live in provider_flags.yaml and must not be confused:

  * What the provider's RAW API does (documented, source_url + verified).
    E.g. Anthropic's raw `input_tokens` excludes cache reads; OpenAI's raw
    `prompt_tokens` includes them; OpenAI bills cache writes only from
    GPT-5.6.
  * What the trajs actually contain -- litellm's NORMALIZED usage, whose
    mapping of the raw fields has changed across litellm versions. That is
    detected empirically per entry (cache_convention.py), never assumed
    from the provider flag. The flag is the reference to compare against.
"""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from .config import TOKEN_WASTE_DIR

PROVIDER_FLAGS_PATH = TOKEN_WASTE_DIR / "provider_flags.yaml"

# litellm-style routing prefixes: "openai/", "@openai/", "anthropic/", ...
_ROUTING_PREFIX = re.compile(r"^@?[a-z0-9_.-]+/")

_PROVIDER_PREFIXES = (
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini", "google"),
    ("minimax", "minimax"),
    ("glm", "zhipu"),
    ("kimi", "moonshot"),
    ("deepseek", "deepseek"),
    ("devstral", "mistral"),
    ("labs-devstral", "mistral"),
    ("mistral", "mistral"),
    ("qwen", "alibaba"),
    ("llama", "meta"),
)


def strip_routing_prefix(model_name: str | None) -> str | None:
    """'openai/gpt-5.2-2025-12-11' -> 'gpt-5.2-2025-12-11'; '@google/x' -> 'x'.
    The prefix is litellm's routing hint, not part of the model's identity,
    and appears on the requested side but not the answering side -- so a
    raw string compare reports a mismatch on every call of every entry."""
    if model_name is None:
        return None
    return _ROUTING_PREFIX.sub("", model_name)


def infer_provider(model_name: str | None) -> str | None:
    if not model_name:
        return None
    name = strip_routing_prefix(model_name.lower())
    for prefix, provider in _PROVIDER_PREFIXES:
        if name.startswith(prefix):
            return provider
    return None


@lru_cache(maxsize=1)
def load_provider_flags() -> dict:
    return yaml.safe_load(PROVIDER_FLAGS_PATH.read_text()) or {}


def cache_write_billed(model_name: str | None) -> bool | None:
    """True / False when provider_flags.yaml has a VERIFIED answer for this
    model, None otherwise (unverified provider, unknown provider, or the
    flag row is marked verified: false). None must flow through as
    provenance 'unknown' -- never as a 0."""
    provider = infer_provider(model_name)
    if provider is None:
        return None
    row = (load_provider_flags().get(provider) or {}).get("cache_write_billed")
    if not row or not row.get("verified"):
        return None
    name = strip_routing_prefix(model_name.lower())
    for pat in row.get("billed_from_models") or []:
        if pat in name:
            return True
    return bool(row.get("default"))
