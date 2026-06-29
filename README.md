# Cost-Aware Model Broker

A request broker that sits in front of multiple LLMs and routes each prompt to the **cheapest model capable of handling it**. Simple prompts go to a fast, low-cost model; hard prompts are escalated to a stronger one. Built with FastAPI, with every model call kept behind one thin layer the service controls.

> **Honest framing:** this saves cost by *choosing a cheaper model per task* — it is not cheaper per token and not magic. The engineering is in deciding *which* tier a prompt needs, reliably and transparently.

---

## Architecture

A request flows through an ordered pipeline; each stage answers exactly one question.

```
            POST /route   { "prompt": "..." }
                     |
                     v
   1. Normalize + validate     reject empty / whitespace -> 400      [implemented]
                     |
                     v
   2. Safety gate              block injection / PII / malware        [planned]
                     |
                     v
   3. Intent shortcut          canned answers, no LLM call            [planned]
                     |
                     v
   4. Difficulty classifier    cheap vs strong  (hybrid heuristics)   [implemented]
                     |  (ambiguous)
                     v
   5. LLM escalation           cheap model judges difficulty          [implemented]
                     |
                     v
   Provider call (Groq)        + retry/backoff on 429                 [implemented]
                     |
                     v
            { tier, reason, model, answer }
```

**Key design point:** *difficulty* ("how hard is this prompt?") and *safety* ("is this prompt allowed?") are orthogonal questions — e.g. `"ignore previous instructions"` is trivial by difficulty but must be blocked. They are deliberately kept in separate layers rather than folded into one classifier.

**Model tiers:** cheap = Llama 3.1 8B Instruct (Meta) · strong = Qwen3-32B (Alibaba), both served by Groq. Cross-vendor on purpose — a real broker picks the best model per tier regardless of who made it. Both sit behind the provider layer and are swappable in one place (the `MODELS` dict in `provider.py`).

---

## What works today

- **Hybrid difficulty routing** — deterministic signals (reasoning keywords, code block, prompt length) classify the obvious cases for free, with no LLM call. Definitive signals → `strong` at any length; otherwise length decides three ways against two knobs (`≤10` words → `cheap`, `>40` → `strong`, the middle band → `ambiguous`).
- **LLM-classifier escalation (the hybrid's second half)** — `ambiguous` prompts spend *one* call on the cheap model acting as a difficulty *judge* ("reply one word: cheap or strong"), then route to its verdict. Unparseable judgments fail safe to `strong`. This catches hard prompts phrased in plain words that keyword heuristics miss, without paying for an LLM call on every request.
- **Transparent decisions** — every routing response reports the chosen `tier`, the `word_count`, and a human-readable `reason` listing which signals fired (and, when escalated, that the judge decided). The decision is auditable, not a black box.
- **Input validation** — empty or whitespace-only prompts are rejected at the front door with a `400`, so the service never spends a model call on nothing.
- **Isolated routing logic** — the whole decision lives in one swappable `classify()` function, decoupled from the HTTP layer.
- **Real completions with a swappable provider** — `POST /complete` classifies *and* calls the chosen model via Groq, returning the answer plus which model ran. Every model call goes through one `call_model()` function; the tier→model binding lives in a single `MODELS` dict, so swapping a model is a one-line edit.
- **Resilient provider calls** — transient `429`s are retried with backoff that honors the provider's `Retry-After`; permanent errors (bad key/slug) fail fast, and an exhausted retry surfaces as a clean `502`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Validation | Pydantic v2 |
| Provider | Groq (OpenAI-compatible API) |
| HTTP client | httpx |
| Config | python-dotenv |

---

## Project Structure

```
cost-aware-model-broker/
├── app/
│   ├── main.py        # FastAPI app — /health, /route, /complete endpoints + validation
│   ├── router.py      # Routing logic — classify() difficulty heuristics
│   └── provider.py    # Provider layer — call_model() via Groq, MODELS dict, 429 backoff
├── requirements.txt   # Python dependencies
├── .env               # GROQ_API_KEY — never committed (see .gitignore)
└── .gitignore
```

---

## Local Setup

**1. Clone**
```bash
git clone https://github.com/Ogirala-Uday-Venkat-Rahul/cost-aware-model-broker.git
cd cost-aware-model-broker
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Add your Groq API key**

Create a `.env` file in the project root (never committed — see `.gitignore`):
```bash
GROQ_API_KEY=your_key_here
```

**4. Run the API**
```bash
uvicorn app.main:app --reload
```

Interactive API docs at `http://localhost:8000/docs`.

**5. Try a request**
```bash
curl -X POST http://localhost:8000/complete \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain why this algorithm is slow and how to optimize it"}'
```
```json
{ "tier": "strong", "reason": "reasoning keyword(s): why, explain, optimize, algorithm", "model": "qwen/qwen3-32b", "answer": "..." }
```

---

## API Reference

### `POST /route`
Classifies a prompt and returns the model tier it should be routed to.

**Request body:**
```json
{ "prompt": "What is the capital of France?" }
```

**Response:**
```json
{ "tier": "cheap", "word_count": 6, "reason": "no signals, short (6 <= 10 words)" }
```

**Error responses:**
| Code | Meaning |
|---|---|
| `400` | Prompt is empty or whitespace-only |
| `422` | Missing or malformed request body |

### `POST /complete`
Classifies a prompt, calls the chosen model via Groq, and returns the answer.

**Request body:**
```json
{ "prompt": "Explain why quicksort is fast" }
```

**Response:**
```json
{ "tier": "strong", "reason": "reasoning keyword(s): why, explain", "model": "qwen/qwen3-32b", "answer": "..." }
```

When the heuristics can't decide, the response shows the escalation trail — the cheap-model judge's verdict:
```json
{ "tier": "strong", "reason": "ambiguous heuristics (no signals, mid-length (27 words) -> needs LLM judge) -> LLM judged 'strong'", "model": "qwen/qwen3-32b", "answer": "..." }
```

**Error responses:**
| Code | Meaning |
|---|---|
| `400` | Prompt is empty or whitespace-only |
| `422` | Missing or malformed request body |
| `502` | Upstream model provider failed (e.g. rate-limited after retries) |

### `GET /health`
Liveness check — returns `{ "status": "ok" }`.

---

## Key Design Decisions

**Why hybrid routing (heuristics + LLM escalation) instead of one or the other?**
Pure heuristics are free and instant but miss nuance; a pure LLM classifier catches nuance but adds an LLM call — and its cost — to *every* request, undercutting the whole point. The hybrid keeps the common path free, and only the prompts the heuristics can't confidently place pay for a cheap LLM judgment. The deterministic heuristics live in one pure `classify()` function; the LLM judge lives in the provider layer (`classify_with_llm`), so each half can be tuned or replaced without touching the rest of the service.

**Why separate difficulty from safety?**
Routing by difficulty answers "which model?"; safety answers "should this run at all?". A short prompt can be trivial yet malicious, so collapsing the two into one classifier produces wrong decisions. Keeping them as distinct pipeline stages keeps each one simple and independently testable.

**Why Groq as the provider?**
Groq serves many models (from several vendors — Meta, Alibaba, OpenAI and more) behind one OpenAI-compatible API and a single key, so switching a tier is a one-line change with no per-provider SDKs to maintain — and every model call stays behind the one thin layer the service controls. It's also fast and has a usable free tier, which keeps the demo reliable. The cheap and strong tiers deliberately use models from *different* vendors (Meta vs Alibaba): a real broker should pick the best model per tier regardless of who made it.

**Why handle reasoning models specially?**
The strong tier (Qwen3) is a reasoning model — left alone it emits a `<think>` chain that burns the token budget and can be cut off before the answer. The provider layer sets `reasoning_effort="none"` for that model to get a direct answer. The cheap model rejects that parameter, which is exactly why per-model request params live *with* the model in the `MODELS` config rather than being sent globally.

---

## Roadmap

- [x] Provider call via Groq (`/complete`) + retry/backoff on transient `429`
- [ ] Safety gate — prompt-injection / PII / malware filtering (layer 2)
- [ ] Intent shortcut — canned responses for fixed intents, no LLM (layer 3)
- [x] LLM-classifier escalation for ambiguous prompts (layer 5)
- [ ] Cross-model fallback to a differently-budgeted model on failure
- [ ] Guardrails — `max_tokens` ceiling, rate limiter, global daily cap
- [ ] Live hosted demo
