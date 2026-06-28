"""Provider layer: turn a routing decision into a real completion via Groq.

Every model call in the service goes through this one function, so changing
which model a tier uses is a one-line edit in MODELS — nothing else has to know.
Groq exposes an OpenAI-compatible API, so the request/response shape is the
standard {model, messages} -> choices[0].message.content.
"""
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()  # read GROQ_API_KEY out of .env into the environment

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Backoff policy for transient 429s. The provider is rate-limited only briefly,
# so we retry a few times instead of failing the caller on the first hiccup.
MAX_RETRIES = 3        # total attempts = 1 original + up to 3 retries
DEFAULT_BACKOFF = 2    # seconds to wait if the provider gives no Retry-After hint
MAX_BACKOFF = 10       # never sleep longer than this on a single wait, so a request can't hang

# tier -> Groq model slug (the single place a tier is bound to a concrete model).
# Cross-vendor on purpose: cheap is Meta, strong is Alibaba — a real broker picks
# the best model per tier regardless of who made it. Slugs come from Groq's live
# model list (GET https://api.groq.com/openai/v1/models).
# Each tier maps to its model slug plus any model-specific request params.
# Qwen3 is a reasoning model: left alone it emits a <think> chain that burns the
# token budget and can get cut off mid-thought, so we set reasoning_effort="none"
# to get a direct answer. The cheap Llama model doesn't accept that param (400),
# which is exactly why per-model params live here, next to the model they apply to.
MODELS = {
    "cheap": {"model": "llama-3.1-8b-instant"},                      # Meta, 8B — small & fast
    "strong": {"model": "qwen/qwen3-32b", "reasoning_effort": "none"},  # Alibaba, 32B — stronger
}


def _retry_after_seconds(response: httpx.Response) -> float:
    """How long the provider asked us to wait before retrying a 429.

    We prefer the standard HTTP Retry-After header (Groq sets this), then a value
    nested in a JSON error body (some gateways put it there), then a sane default.
    We cap it so one unlucky request can't block for half a minute.
    """
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return min(float(header), MAX_BACKOFF)
        except ValueError:
            pass  # header wasn't a number; fall through to the body / default

    try:
        body_value = response.json()["error"]["metadata"]["retry_after_seconds"]
        return min(float(body_value), MAX_BACKOFF)
    except (ValueError, KeyError, TypeError):
        return DEFAULT_BACKOFF


def call_model(prompt: str, tier: str) -> dict:
    """Send the prompt to the model mapped to `tier`; return the answer + model used.

    Retries transient 429s (provider rate-limited) up to MAX_RETRIES, honoring the
    provider's Retry-After hint. Any other error (bad key, bad slug) fails fast.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set in .env")

    config = MODELS[tier]          # {"model": ..., + any model-specific params}
    model = config["model"]
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        **config,                  # spreads in "model" plus e.g. reasoning_effort
    }

    for attempt in range(MAX_RETRIES + 1):  # 1 initial try + MAX_RETRIES retries
        response = httpx.post(GROQ_URL, headers=headers, json=payload, timeout=60)

        # Retry only on 429 (rate-limited), and only while we have attempts left.
        # Every other status — including a hard error like 401/404 — drops through
        # to raise_for_status() below and fails immediately; retrying won't fix it.
        if response.status_code == 429 and attempt < MAX_RETRIES:
            time.sleep(_retry_after_seconds(response))
            continue

        response.raise_for_status()  # raises on any non-2xx (incl. a final 429)
        answer = response.json()["choices"][0]["message"]["content"]
        return {"model": model, "answer": answer}
