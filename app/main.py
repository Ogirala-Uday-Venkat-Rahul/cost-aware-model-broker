from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

from app.router import classify
from app.provider import call_model, classify_with_llm
from app.safety import is_safe

app = FastAPI(title="Model-Routing Service")


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
def complete(req: RouteRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    # Layer 2: safety gate. Reject prompt-injection attempts before spending any
    # model call. 403 = "understood, but not allowed to run", not a 400/422 input
    # error and not a 5xx server fault.
    safety = is_safe(prompt)
    if not safety["allowed"]:
        raise HTTPException(status_code=403, detail=f"prompt rejected by safety gate ({safety['reason']})")

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
