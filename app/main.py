from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.router import classify

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
