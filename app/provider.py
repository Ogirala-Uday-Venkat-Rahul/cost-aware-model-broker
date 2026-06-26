"""Provider layer: turn a routing decision into a real completion via OpenRouter.

Every model call in the service goes through this one function, so changing
which model a tier uses is a one-line edit in MODELS — nothing else has to know.
"""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()  # read OPENROUTER_API_KEY out of .env into the environment

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# tier -> OpenRouter model slug.
# Pick the exact strings from https://openrouter.ai/models with the "Free"
# filter on: one small/fast model for "cheap", one stronger reasoning model
# for "strong". Copy the slug exactly (it looks like "vendor/model-name:free").
MODELS = {
    "cheap": "meta-llama/llama-3.2-3b-instruct:free",   # small, fast (3B)
    "strong": "qwen/qwen3-next-80b-a3b-instruct:free",  # larger reasoning (80B MoE)
}


def call_model(prompt: str, tier: str) -> dict:
    """Send the prompt to the model mapped to `tier`; return the answer + model used."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in .env")

    model = MODELS[tier]
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }

    response = httpx.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()  # raises on any non-2xx (e.g. bad key, bad slug)

    answer = response.json()["choices"][0]["message"]["content"]
    return {"model": model, "answer": answer}
