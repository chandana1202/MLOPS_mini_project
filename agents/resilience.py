import os
import random
import time

from redis import Redis

from agents.llm import LLMClient, LLMError


class CircuitOpen(LLMError):
    pass


class Breaker:
    def __init__(self):
        self.r = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
        self.threshold = int(os.getenv("BREAKER_THRESHOLD", "3"))
        self.reset_s = int(os.getenv("BREAKER_RESET_S", "30"))

    def allow(self):
        return not self.r.exists("breaker:open")

    def success(self):
        self.r.delete("breaker:fails")

    def failure(self):
        # after the open window expires, the failure count is still >= threshold, so the first
        # failed trial call re-opens immediately (half-open); the first success closes it.
        if self.r.incr("breaker:fails") >= self.threshold:
            self.r.set("breaker:open", "1", ex=self.reset_s)


class ResilientLLM(LLMClient):
    """Timeout + bounded retry with exponential backoff + circuit breaker around any provider."""

    def __init__(self, inner):
        self.inner, self.b = inner, Breaker()
        self.retries = int(os.getenv("LLM_RETRIES", "3"))
        self.timeout = float(os.getenv("LLM_TIMEOUT_S", "30"))

    def complete(self, system, user, *, role="generic", context=None, timeout=None):
        if not self.b.allow():
            raise CircuitOpen("circuit open: provider marked unavailable")
        last = None
        for i in range(self.retries):
            try:
                if self.b.r.get("demo:llm_down"):          # demo-only outage switch
                    raise LLMError("simulated provider outage")
                resp = self.inner.complete(system, user, role=role, context=context,
                                           timeout=timeout or self.timeout)
                self.b.success()
                return resp
            except LLMError as e:
                last = e
                self.b.failure()
                self.b.r.incr("metrics:llm_retries")
                if not self.b.allow():
                    raise CircuitOpen("circuit opened after repeated failures") from e
                time.sleep(min(8, 2 ** i) + random.random() * 0.3)
        raise last