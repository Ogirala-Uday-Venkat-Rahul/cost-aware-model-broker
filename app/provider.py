"""Provider layer: turn a routing decision into a real completion via Groq.

Every model call in the service goes through this one function, so changing which
model a tier uses is a one-line edit in MODELS and nothing else has to know. Groq
exposes an OpenAI-compatible API, so the request and response shape is the standard
{model, messages} in, choices[0].message.content out.
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

# Output-length guardrail. max_tokens caps how much the model may generate on one
# call, so the cost of a single response has a hard ceiling no matter how the user
# phrases the request ("write a book about Rome" can't run up thousands of tokens).
# The default applies to every answer call automatically, since a forgotten limit
# must never mean an unbounded one. The judge call overrides it with a tiny ceiling
# because it only ever replies one word ("cheap" or "strong"), which keeps escalation
# cheap.
DEFAULT_MAX_TOKENS = 1024  # answer ceiling: generous enough for real answers, still bounded
JUDGE_MAX_TOKENS = 8       # the classifier needs one word; never let it write an essay

# Maps each tier to its Groq model slug, the single place a tier is bound to a
# concrete model. The tiers are cross-vendor by design: cheap is Meta, strong is
# Alibaba, because a real broker picks the best model per tier regardless of who
# made it. Slugs come from Groq's live model list (GET .../v1/models). Each tier
# maps to its slug plus any model-specific request params.
#
# Qwen3 is a reasoning model: left alone it emits a <think> chain that burns the
# token budget and can get cut off mid-thought, so we set reasoning_effort="none"
# to get a direct answer. The cheap Llama model rejects that param with a 400, which
# is why per-model params live here, next to the model they apply to.
MODELS = {
    "cheap": {"model": "llama-3.1-8b-instant"},                         # Meta, 8B, small and fast
    "strong": {"model": "qwen/qwen3-32b", "reasoning_effort": "none"},  # Alibaba, 32B, stronger
    # Not a routing tier. This is the reliability backup (feature 4): a general-purpose
    # Meta 70B used only when the chosen model fails for a model-specific reason.
    # Listing it here lets call_model drive it with the same retry/token/temp logic.
    "fallback": {"model": "llama-3.3-70b-versatile"},
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


def call_model(
    prompt: str,
    tier: str,
    temperature: float | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict:
    """Send the prompt to the model mapped to `tier`; return the answer + model used.

    Retries transient 429s (provider rate-limited) up to MAX_RETRIES, honoring the
    provider's Retry-After hint. Any other error (bad key, bad slug) fails fast.

    `temperature` is optional: left as None, the model uses its own default (good
    for creative answers). The classifier passes temperature=0 so its judgment is
    deterministic and reproducible.

    `max_tokens` caps how much the model may generate. It defaults to DEFAULT_MAX_TOKENS
    so every call is bounded even if the caller forgets. The judge call passes a small
    value because it only needs one word back.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set in .env")

    config = MODELS[tier]          # {"model": ..., + any model-specific params}
    model = config["model"]
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,  # hard ceiling on generated tokens for this call
        **config,                  # spreads in "model" plus e.g. reasoning_effort
    }
    if temperature is not None:    # note: `is not None`, so temperature=0 still applies
        payload["temperature"] = temperature

    for attempt in range(MAX_RETRIES + 1):  # 1 initial try + MAX_RETRIES retries
        response = httpx.post(GROQ_URL, headers=headers, json=payload, timeout=60)

        # Retry only on 429 (rate-limited), and only while we have attempts left.
        # Every other status, including a hard error like 401 or 404, drops through
        # to raise_for_status() below and fails immediately, since retrying won't fix it.
        if response.status_code == 429 and attempt < MAX_RETRIES:
            time.sleep(_retry_after_seconds(response))
            continue

        response.raise_for_status()  # raises on any non-2xx (incl. a final 429)
        answer = response.json()["choices"][0]["message"]["content"]
        return {"model": model, "answer": answer}


def _is_model_specific_failure(exc: Exception) -> bool:
    """True if `exc` points at this specific model or endpoint, so a different model
    might work.

    A fallback is worth trying when the model's server erred (5xx) or we couldn't
    reach it at all (timeout, connection refused or reset, DNS); httpx.TransportError
    covers both of those transport cases. It's not worth trying (return False, fail
    fast) on a 429, because our backup shares Groq's free budget and is throttled too,
    so falling back would be theater; nor on any other 4xx like 401 or 400, since a
    bad key or bad request fails on any model.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def call_with_fallback(prompt: str, tier: str, **kwargs) -> dict:
    """Call the chosen tier's model, and on a model-specific failure retry once on
    the dedicated backup model so the user still gets an answer.

    Reliability is kept separate from routing: the fallback is its own model rather
    than a swap between cheap and strong, so a recovery never silently changes which
    quality tier the user asked for. Only model-specific failures trigger it (see
    _is_model_specific_failure); everything else re-raises and becomes a 502 upstream.
    """
    try:
        return call_model(prompt, tier, **kwargs)
    except httpx.HTTPError as exc:
        if not _is_model_specific_failure(exc):
            raise  # a 429, 401 or 400 won't be helped by a different model, so fail fast
        # the chosen model is down or unreachable, so make one attempt on the backup
        return call_model(prompt, "fallback", **kwargs)


# Layer 5: LLM-classifier escalation for the ambiguous middle.
# When the deterministic heuristics can't decide (router returns "ambiguous"), we
# spend one small call on the cheap model to break the tie. The cheap model acts
# purely as a judge here: it never answers the user's question, it only labels the
# difficulty. We wrap the user's prompt in a strict instruction so the reply is a
# single word we can parse.
CLASSIFIER_INSTRUCTION = (
    "You are a routing classifier for an AI service. Decide whether the user's "
    "request can be handled by a small 'cheap' model or needs a more capable "
    "'strong' model. Consider reasoning depth, multi-step work, and nuance. "
    "Reply with exactly one word, lowercase: cheap or strong.\n\n"
    "User request:\n{prompt}"
)


def classify_with_llm(prompt: str) -> str:
    """Resolve an 'ambiguous' prompt to a real tier via a cheap-model judge call.

    Returns "cheap" or "strong". If the judge replies with anything we can't read as
    a clear tier, we fail safe to "strong": a broken classifier should never silently
    downgrade a hard prompt to the weak model.
    """
    judge_prompt = CLASSIFIER_INSTRUCTION.format(prompt=prompt)
    # temperature=0 makes the judge return its single best label rather than a random
    # sample, so the same prompt always routes the same way. max_tokens=JUDGE_MAX_TOKENS
    # caps it tiny since it only has to say one word, which keeps the escalation call
    # cheap and stops it ever writing a paragraph.
    result = call_model(judge_prompt, "cheap", temperature=0, max_tokens=JUDGE_MAX_TOKENS)
    reply = result["answer"].strip().lower()

    # Check "strong" first so a hedged reply mentioning both words ("not cheap, use
    # strong") biases to strong, the same fail-safe direction as an unparseable reply.
    if "strong" in reply:
        return "strong"
    if "cheap" in reply:
        return "cheap"
    return "strong"  # unparseable judge output, so fail safe on quality
