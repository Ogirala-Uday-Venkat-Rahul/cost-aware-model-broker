from math import ceil

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx

from app.router import classify
from app.provider import call_model, classify_with_llm
from app.safety import is_safe
from app.intents import match_intent
from app.ratelimit import check_rate_limit

app = FastAPI(title="Model-Routing Service")


def _client_ip(request: Request) -> str:
    """The caller's IP, accounting for the proxy the app sits behind on a host.

    Behind a proxy (Render etc.) request.client.host is the *proxy's* IP, so every
    user would look identical. The real client IP is the first entry of the
    X-Forwarded-For chain ("client, proxy1, proxy2"). It's spoofable, so this is
    only trustworthy because our own proxy sets it; with no proxy we fall back to
    the direct connection IP.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


class RouteRequest(BaseModel):
    prompt: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/route")
def route(req: RouteRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt must not be empty")
    return classify(prompt)


@app.post("/complete")
def complete(req: RouteRequest, request: Request):
    # Guardrail 2: per-client rate limit. Runs first so request *frequency* is
    # capped regardless of content -- an abuser can't spam the endpoint for free
    # just by sending requests that would fail validation later.
    client_ip = _client_ip(request)
    limit = check_rate_limit(client_ip)
    if not limit["allowed"]:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded, slow down",
            headers={"Retry-After": str(ceil(limit["retry_after"]))},
        )

    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    # Layer 2: safety gate. Reject prompt-injection attempts before spending any
    # model call. 403 = "understood, but not allowed to run", not a 400/422 input
    # error and not a 5xx server fault.
    safety = is_safe(prompt)
    if not safety["allowed"]:
        raise HTTPException(status_code=403, detail=f"prompt rejected by safety gate ({safety['reason']})")

    # Layer 3: intent shortcut. Fixed operational intents (ping, help) get a canned
    # answer with no model call at all, the cheapest possible path.
    canned = match_intent(prompt)
    if canned is not None:
        return {"tier": "shortcut", "reason": "matched a fixed intent", "model": None, "answer": canned}

    decision = classify(prompt)
    tier = decision["tier"]
    reason = decision["reason"]

    try:
        # Layer 5: heuristics couldn't decide -> spend one cheap judge call to
        # resolve the tie before committing to a model. Both this judge call and
        # the answer call below are provider calls, so they share the 502 handler.
        if tier == "ambiguous":
            tier = classify_with_llm(prompt)
            reason = f"ambiguous heuristics ({decision['reason']}) -> LLM judged '{tier}'"
        result = call_model(prompt, tier)
    except httpx.HTTPStatusError as exc:
        # the upstream model provider failed (bad key, bad slug, rate limit) ->
        # surface it as 502 Bad Gateway, not a generic 500
        raise HTTPException(status_code=502, detail=f"provider error: {exc}")

    return {
        "tier": tier,
        "reason": reason,
        "model": result["model"],
        "answer": result["answer"],
    }
