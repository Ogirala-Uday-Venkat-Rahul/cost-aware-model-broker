from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

from app.router import classify
from app.provider import call_model

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

    decision = classify(prompt)
    try:
        result = call_model(prompt, decision["tier"])
    except httpx.HTTPStatusError as exc:
        # the upstream model provider failed (bad key, bad slug, rate limit) ->
        # surface it as 502 Bad Gateway, not a generic 500
        raise HTTPException(status_code=502, detail=f"provider error: {exc}")

    return {
        "tier": decision["tier"],
        "reason": decision["reason"],
        "model": result["model"],
        "answer": result["answer"],
    }
