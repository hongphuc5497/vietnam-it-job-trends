"""OpenAI-compatible LLM adapter ("Hermes" model backend).

Exposes `complete(system, user, max_tokens)` (async) and `complete_sync(...)`
built on the `openai` package, targeting an OpenAI-compatible chat completions
endpoint.

Model / base_url / api_key resolution order:

1. Environment variables ``LLM_MODEL`` / ``LLM_BASE_URL`` / ``LLM_API_KEY``.
2. The Hermes config (``~/.hermes/profiles/primary/config.yaml``): the provider
   block whose ``base_url`` points at the Hermes endpoint
   (``https://token-plan-sgp.xiaomimimo.com/v1``) — its ``api_key`` and default
   model entry are used.
3. If that block has no ``api_key``, the provider's own env var (e.g.
   ``XIAOMI_API_KEY`` for `provider: xiaomi`) is looked up in the process
   environment and in Hermes' ``.env`` files (``~/.hermes/.env`` and
   ``~/.hermes/profiles/primary/.env``).

The API key is never printed or logged, not even partially.
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

DEFAULT_MODEL = "mimo-v2.6-pro"
DEFAULT_BASE_URL = "https://token-plan-sgp.xiaomimimo.com/v1"

_HERMES_CONFIG = Path.home() / ".hermes" / "profiles" / "primary" / "config.yaml"
_HERMES_ENV_FILES = (
    Path.home() / ".hermes" / ".env",
    Path.home() / ".hermes" / "profiles" / "primary" / ".env",
)


def _parse_hermes_block() -> dict:
    """Find the provider block in the Hermes config containing DEFAULT_BASE_URL.

    Returns the block's ``key: value`` entries (e.g. api_key, default, provider).
    Missing file or block yields {}.
    """
    try:
        lines = _HERMES_CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    idx = next(
        (i for i, l in enumerate(lines) if DEFAULT_BASE_URL in l and "base_url" in l),
        None,
    )
    if idx is None:
        return {}

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    base = indent(lines[idx])
    block: dict = {}

    def collect(line: str) -> None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            return
        key, _, value = stripped.partition(":")
        block[key.strip()] = value.strip().strip("'\"")

    # Walk outwards from the base_url line while entries stay at the same indent.
    for i in range(idx, -1, -1):
        line = lines[i]
        if line.strip() and indent(line) < base:
            break
        if indent(line) == base:
            collect(line)
    for i in range(idx + 1, len(lines)):
        line = lines[i]
        if line.strip() and indent(line) < base:
            break
        if indent(line) == base:
            collect(line)
    return block


def _parse_env_file(path: Path) -> dict:
    """Parse a simple KEY=VALUE .env file (comments and blanks skipped)."""
    values: dict = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _resolve_api_key(block: dict) -> str:
    key = os.environ.get("LLM_API_KEY", "").strip()
    if key:
        return key
    key = (block.get("api_key") or "").strip()
    if key:
        return key
    # Fall back to the provider's own env var (e.g. XIAOMI_API_KEY).
    provider = (block.get("provider") or "").strip().upper().replace("-", "_")
    env_names = [f"{provider}_API_KEY"] if provider else []
    for name in env_names:
        key = os.environ.get(name, "").strip()
        if key:
            return key
    for env_file in _HERMES_ENV_FILES:
        values = _parse_env_file(env_file)
        for name in env_names:
            key = (values.get(name) or "").strip()
            if key:
                return key
    raise RuntimeError(
        "No API key found: set LLM_API_KEY, or the Hermes provider api_key "
        "(config.yaml / provider env var such as XIAOMI_API_KEY)."
    )


@lru_cache(maxsize=1)
def _settings() -> tuple:
    """Resolved (model, base_url, api_key). Cached; the key is never logged."""
    block = _parse_hermes_block()
    model = (
        os.environ.get("LLM_MODEL", "").strip()
        or (block.get("default") or block.get("model") or "").strip()
        or DEFAULT_MODEL
    )
    base_url = (
        os.environ.get("LLM_BASE_URL", "").strip()
        or (block.get("base_url") or "").strip()
        or DEFAULT_BASE_URL
    )
    return model, base_url, _resolve_api_key(block)


def get_model() -> str:
    return _settings()[0]


def get_base_url() -> str:
    return _settings()[1]


@lru_cache(maxsize=1)
def _async_client() -> AsyncOpenAI:
    _, base_url, api_key = _settings()
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


@lru_cache(maxsize=1)
def _sync_client() -> OpenAI:
    _, base_url, api_key = _settings()
    return OpenAI(api_key=api_key, base_url=base_url)


async def complete(system: str, user: str, max_tokens: int = 4000) -> str:
    """Chat completion: system + user prompt -> assistant text."""
    model, _, _ = _settings()
    response = await _async_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def complete_sync(system: str, user: str, max_tokens: int = 4000) -> str:
    """Synchronous convenience wrapper around :func:`complete`."""
    model, _, _ = _settings()
    response = _sync_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


if __name__ == "__main__":
    print(asyncio.run(complete("You are a test.", "Reply with exactly: OK")))
