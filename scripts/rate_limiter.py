"""
rate_limiter.py

A thread-safe rate limiter shared across all Groq calls, tracking BOTH
requests-per-minute AND tokens-per-minute - Groq enforces both limits
independently, and TPM is usually the tighter, more binding one, especially
as a conversation's message history grows longer with each turn.

Separate named buckets per model, since different Groq models have
different TPM budgets (llama-3.1-8b-instant vs llama-3.3-70b-versatile).
"""

import time
import threading
from collections import deque

# Conservative, safely under Groq's published free-tier minimums for each model
BUDGETS = {
    "llama-3.1-8b-instant": {"rpm": 28, "tpm": 5500},
    "llama-3.3-70b-versatile": {"rpm": 28, "tpm": 5500},
}
WINDOW_SECONDS = 60

_lock = threading.Lock()
_state = {name: {"calls": deque(), "tokens": deque()} for name in BUDGETS}


def estimate_tokens(messages: list) -> int:
    """Rough estimate: ~4 characters per token, plus a buffer for the response."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return (total_chars // 4) + 300  # +300 buffer for the model's reply


def acquire(model_name: str, messages: list):
    """Blocks until it's safe to make another call to this model, respecting
    both the request-count and token-volume limits, shared across all threads."""
    budget = BUDGETS.get(model_name, {"rpm": 25, "tpm": 5000})
    est_tokens = estimate_tokens(messages)

    while True:
        with _lock:
            now = time.time()
            state = _state.setdefault(model_name, {"calls": deque(), "tokens": deque()})

            for dq in (state["calls"], state["tokens"]):
                while dq and now - dq[0][0] > WINDOW_SECONDS:
                    dq.popleft()

            calls_used = len(state["calls"])
            tokens_used = sum(t for _, t in state["tokens"])

            if calls_used < budget["rpm"] and tokens_used + est_tokens < budget["tpm"]:
                state["calls"].append((now, 1))
                state["tokens"].append((now, est_tokens))
                return

            oldest_relevant = min(
                (state["calls"][0][0] if state["calls"] else now),
                (state["tokens"][0][0] if state["tokens"] else now),
            )
            wait_time = WINDOW_SECONDS - (now - oldest_relevant) + 0.5

        time.sleep(max(wait_time, 1))