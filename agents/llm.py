"""LLM interface. Provider is chosen by config only (LLM_PROVIDER). Member B wraps `complete`
with timeout/retry/circuit-breaker; keep this module free of that logic."""
import json
import os
import time
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_s: float


class LLMError(Exception):
    pass


class LLMClient:
    def complete(self, system: str, user: str, *, role: str = "generic",
                 context: dict | None = None, timeout: float = 30.0) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatClient(LLMClient):
    """Any provider exposing an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self):
        self.base = os.environ["LLM_BASE_URL"].rstrip("/")
        self.key = os.environ["LLM_API_KEY"]
        self.model = os.environ["LLM_MODEL"]

    def complete(self, system, user, *, role="generic", context=None, timeout=30.0):
        body = {"model": self.model, "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        req = urllib.request.Request(
            self.base + "/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            text = data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise LLMError(f"HTTP {e.code}: {body}") from e
        except Exception as e:
            raise LLMError(str(e)) from e
        
        u = data.get("usage", {})
        return LLMResponse(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                           self.model, time.monotonic() - t0)


def get_llm() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "stub")
    if provider == "stub":
        from agents.stub_llm import StubLLM
        return StubLLM()
    if provider == "openai_compat":
        return OpenAICompatClient()
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}")
