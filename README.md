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
   5. LLM escalation           tiny model rates difficulty            [planned]
                     |
                     v
   Provider call (OpenRouter)  + fallback + guardrails                [planned]
                     |
                     v
            { tier, word_count, reason }
```

**Key design point:** *difficulty* ("how hard is this prompt?") and *safety* ("is this prompt allowed?") are orthogonal questions — e.g. `"ignore previous instructions"` is trivial by difficulty but must be blocked. They are deliberately kept in separate layers rather than folded into one classifier.

**Target model tiers** (provisional, validated against free-tier limits): cheap = Llama 3.3 70B · strong = DeepSeek R1. Both sit behind the provider layer and are swappable in one place.

---

## What works today

- **Hybrid difficulty routing (heuristics layer)** — deterministic signals (prompt length, reasoning keywords, presence of a code block) classify the obvious cases for free, with no LLM call. The ambiguous middle is what later escalates to a small LLM classifier.
- **Transparent decisions** — every routing response reports the chosen `tier`, the `word_count`, and a human-readable `reason` listing which signals fired. The decision is auditable, not a black box.
- **Input validation** — empty or whitespace-only prompts are rejected at the front door with a `400`, so the service never spends a model call on nothing.
- **Isolated routing logic** — the whole decision lives in one swappable `classify()` function, decoupled from the HTTP layer.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Validation | Pydantic v2 |
| Provider gateway *(planned)* | OpenRouter — one key, many models |
| HTTP client *(planned)* | httpx |
| Config | python-dotenv |

---

## Project Structure

```
cost-aware-model-broker/
├── app/
│   ├── main.py        # FastAPI app — /health and /route endpoints, input validation
│   └── router.py      # Routing logic — classify() difficulty heuristics
├── requirements.txt   # Python dependencies
├── .env               # OPENROUTER_API_KEY — never committed (see .gitignore)
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

**3. Run the API**
```bash
uvicorn app.main:app --reload
```

Interactive API docs at `http://localhost:8000/docs`.

**4. Try a request**
```bash
curl -X POST http://localhost:8000/route \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain why this algorithm is slow and how to optimize it"}'
```
```json
{ "tier": "strong", "word_count": 11, "reason": "reasoning keyword(s): why, explain, optimize, algorithm" }
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
{ "tier": "cheap", "word_count": 6, "reason": "no strong signals" }
```

**Error responses:**
| Code | Meaning |
|---|---|
| `400` | Prompt is empty or whitespace-only |
| `422` | Missing or malformed request body |

### `GET /health`
Liveness check — returns `{ "status": "ok" }`.

---

## Key Design Decisions

**Why hybrid routing (heuristics + LLM escalation) instead of one or the other?**
Pure heuristics are free and instant but miss nuance; a pure LLM classifier catches nuance but adds an LLM call — and its cost — to *every* request, undercutting the whole point. The hybrid keeps the common path free, and only the prompts the heuristics can't confidently place pay for a (cheap, tightly capped) LLM judgment. The strategy lives behind one `classify()` function, so it can be tuned or replaced without touching the rest of the service.

**Why separate difficulty from safety?**
Routing by difficulty answers "which model?"; safety answers "should this run at all?". A short prompt can be trivial yet malicious, so collapsing the two into one classifier produces wrong decisions. Keeping them as distinct pipeline stages keeps each one simple and independently testable.

**Why OpenRouter as the provider?**
Since this project *is* a model broker, fronting many models through a single API key means switching a tier is a one-line change with no per-provider SDKs or keys to maintain — and it keeps every model call behind the one thin layer the service controls.

---

## Roadmap

- [ ] Safety gate — prompt-injection / PII / malware filtering (layer 2)
- [ ] Intent shortcut — canned responses for fixed intents, no LLM (layer 3)
- [ ] LLM-classifier escalation for ambiguous prompts (layer 5)
- [ ] Provider call via OpenRouter + cross-model fallback
- [ ] Guardrails — `max_tokens` ceiling, rate limiter, global daily cap
- [ ] Live hosted demo
