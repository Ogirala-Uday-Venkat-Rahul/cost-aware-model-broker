"""Streamlit front end for the Cost-Aware Model Broker.

A router console: send a prompt, and each result card shows the routing decision
(which tier, which model, and the reason) above the answer. Cards stack newest
first, so firing several prompts shows how differently they route. The UI keeps a
client-side list only; the backend itself is stateless.
"""
import httpx
import streamlit as st

# Where to reach the FastAPI backend. On Streamlit Cloud this comes from the app's
# secrets; locally it falls back to a dev server on localhost.
try:
    BACKEND_URL = st.secrets["BACKEND_URL"]
except (KeyError, FileNotFoundError):
    BACKEND_URL = "http://127.0.0.1:8000/complete"

st.set_page_config(page_title="Cost-Aware Model Broker", page_icon="🧭", layout="centered")

# Colour and label shown on each result card, per routing tier.
TIER_STYLE = {
    "cheap": ("green", "CHEAP"),
    "strong": ("red", "STRONG"),
    "shortcut": ("blue", "SHORTCUT"),
}

if "results" not in st.session_state:
    st.session_state.results = []


def route_prompt(prompt: str):
    """Send one prompt to /complete and store the result (or error) for display."""
    prompt = prompt.strip()
    if not prompt:
        return

    try:
        response = httpx.post(BACKEND_URL, json={"prompt": prompt}, timeout=60.0)
    except httpx.RequestError:
        st.session_state.results.insert(0, {
            "prompt": prompt, "ok": False,
            "detail": "Could not reach the backend. Is it running?",
        })
        return

    if response.status_code == 200:
        body = response.json()
        st.session_state.results.insert(0, {
            "prompt": prompt, "ok": True,
            "tier": body["tier"], "model": body.get("model"),
            "reason": body["reason"], "answer": body["answer"],
        })
        return

    # Non-200: surface the backend's own message (safety 403, rate-limit 429, etc.).
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text
    retry = response.headers.get("Retry-After")
    if retry:
        detail = f"{detail} (retry after {retry}s)"
    st.session_state.results.insert(0, {
        "prompt": prompt, "ok": False,
        "status": response.status_code, "detail": detail,
    })


with st.sidebar:
    st.header("How it routes")
    st.markdown(
        "Each prompt is scored for difficulty and sent to the cheapest model that "
        "can handle it:\n\n"
        "- :green[**CHEAP**] — Llama 3.1 8B (Meta)\n"
        "- :red[**STRONG**] — Qwen3-32B (Alibaba)\n"
        "- :blue[**SHORTCUT**] — canned answer, no model call\n\n"
        "Ambiguous prompts get a one-word judgment from the cheap model before routing."
    )
    st.caption("Stateless: every prompt is an independent decision.")

st.title("🧭 Cost-Aware Model Broker")
st.caption("Send a prompt and watch which model tier it routes to, and why.")

with st.form("prompt_form", clear_on_submit=True):
    prompt = st.text_area("Prompt", placeholder="e.g. Explain why quicksort is fast", height=100)
    submitted = st.form_submit_button("Route")
if submitted:
    route_prompt(prompt)

# One-click examples that each hit a different route (cheap / strong / shortcut).
st.caption("Or try one:")
examples = [
    "What is the capital of France?",
    "Explain why quicksort is fast and how to optimize it",
    "ping",
]
for col, example in zip(st.columns(len(examples)), examples):
    if col.button(example, use_container_width=True):
        route_prompt(example)

# Results, newest first.
for r in st.session_state.results:
    with st.container(border=True):
        st.caption(f"prompt: {r['prompt']}")
        if r["ok"]:
            color, label = TIER_STYLE.get(r["tier"], ("gray", str(r["tier"]).upper()))
            model = r["model"] or "no model call"
            st.markdown(f":{color}[**{label}**]  ·  `{model}`")
            st.caption(f"why: {r['reason']}")
            st.write(r["answer"])
        else:
            head = f"Error {r['status']}" if r.get("status") else "Error"
            st.markdown(f":orange[**{head}**]")
            st.write(r["detail"])
