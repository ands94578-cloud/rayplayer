"""Provider adapters for the panel.

Stdlib only -- no third-party dependencies, so the orchestrator runs anywhere
python3 does. Every adapter takes the same (system, messages) shape and returns
a Completion, which is what lets the panel mix labs in one conversation.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    pass


@dataclass
class Msg:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class Completion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0


def merge_turns(messages: list[Msg]) -> list[Msg]:
    """Collapse consecutive same-role messages and make sure we open on 'user'.

    In a panel, everyone else's lines arrive as 'user' turns, so consecutive
    same-role messages are the norm rather than the exception. Most chat APIs
    reject them.
    """
    out: list[Msg] = []
    for m in messages:
        if out and out[-1].role == m.role:
            out[-1] = Msg(m.role, out[-1].content + "\n\n" + m.content)
        else:
            out.append(Msg(m.role, m.content))
    if out and out[0].role != "user":
        out.insert(0, Msg("user", "(recording starts)"))
    return out


def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json", **headers}
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = ProviderError(f"HTTP {e.code} from {url}: {detail}")
            if e.code not in (408, 409, 429, 500, 502, 503, 504):
                raise last from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = ProviderError(f"network error calling {url}: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


class Provider:
    """Base adapter. `spec` is the speaker's provider block from the panel file."""

    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.model = spec["model"]
        self.timeout = int(spec.get("timeout", 120))
        self.retries = int(spec.get("retries", 3))

    def api_key(self) -> str:
        env = self.spec.get("api_key_env")
        key = os.environ.get(env, "") if env else ""
        if not key:
            raise ProviderError(
                f"{env} is not set -- needed for model {self.model}. "
                f"Set it, or run with --offline to use the mock panel."
            )
        return key

    def complete(self, system: str, messages: list[Msg], temperature: float, max_tokens: int) -> Completion:
        raise NotImplementedError


class AnthropicProvider(Provider):
    def complete(self, system, messages, temperature, max_tokens):
        base = self.spec.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
        started = time.time()
        r = _post(
            f"{base.rstrip('/')}/v1/messages",
            {"x-api-key": self.api_key(), "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "system": system,
                "messages": [{"role": m.role, "content": m.content} for m in merge_turns(messages)],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            self.timeout,
            self.retries,
        )
        text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        usage = r.get("usage", {})
        return Completion(text, usage.get("input_tokens"), usage.get("output_tokens"), int((time.time() - started) * 1000))


class OpenAIChatProvider(Provider):
    """Anything that speaks /chat/completions: OpenAI, xAI, DeepSeek, Groq,
    OpenRouter, vLLM, Ollama. Point base_url at the right host."""

    def complete(self, system, messages, temperature, max_tokens):
        base = self.spec.get("base_url", "https://api.openai.com/v1")
        payload = [{"role": "system", "content": system}]
        payload += [{"role": m.role, "content": m.content} for m in merge_turns(messages)]
        started = time.time()
        r = _post(
            f"{base.rstrip('/')}/chat/completions",
            {"authorization": f"Bearer {self.api_key()}"},
            {
                "model": self.model,
                "messages": payload,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            self.timeout,
            self.retries,
        )
        choices = r.get("choices") or []
        if not choices:
            raise ProviderError(f"no choices in response from {self.model}: {json.dumps(r)[:300]}")
        text = choices[0].get("message", {}).get("content") or ""
        usage = r.get("usage", {})
        return Completion(text, usage.get("prompt_tokens"), usage.get("completion_tokens"), int((time.time() - started) * 1000))


class GeminiProvider(Provider):
    def complete(self, system, messages, temperature, max_tokens):
        base = self.spec.get("base_url", "https://generativelanguage.googleapis.com")
        contents = [
            {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
            for m in merge_turns(messages)
        ]
        started = time.time()
        r = _post(
            f"{base.rstrip('/')}/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key()},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            },
            self.timeout,
            self.retries,
        )
        candidates = r.get("candidates") or []
        if not candidates:
            raise ProviderError(f"no candidates from {self.model}: {json.dumps(r)[:300]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        usage = r.get("usageMetadata", {})
        return Completion(
            text,
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
            int((time.time() - started) * 1000),
        )


class MockProvider(Provider):
    """Offline stand-in so the orchestration can be developed and tested
    without burning four API budgets. Output is canned, deterministic, and
    obviously fake -- never mistake a mock transcript for a real one."""

    LINES = [
        "I'd push back on the framing there. The interesting question isn't whether it works, it's what we stop noticing once it does.",
        "Agreed on the mechanism, but I think you're underrating how much of this is just distribution.",
        "Let me be concrete: the failure mode nobody in this conversation has named yet is the boring one, cost.",
        "That's a fair point and it changes my answer. I came in thinking this was settled; it isn't.",
        "No, I don't buy that. The evidence you're pointing at is consistent with a much duller explanation.",
        "Can we separate two claims that keep getting merged here? One is empirical, one is basically aesthetic.",
    ]

    def api_key(self):  # never needed
        return "mock"

    def complete(self, system, messages, temperature, max_tokens):
        seed = abs(hash((self.model, system[:60], len(messages))))
        line = self.LINES[seed % len(self.LINES)]
        return Completion(f"[mock:{self.model}] {line}", 0, 0, 1)


REGISTRY = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIChatProvider,
    "openai-compatible": OpenAIChatProvider,
    "gemini": GeminiProvider,
    "mock": MockProvider,
}


def build(spec: dict[str, Any], offline: bool = False) -> Provider:
    kind = "mock" if offline else spec.get("kind", "openai-compatible")
    if kind not in REGISTRY:
        raise ProviderError(f"unknown provider kind {kind!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[kind](spec)
