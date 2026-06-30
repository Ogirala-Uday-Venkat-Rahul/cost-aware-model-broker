# Cost-Aware Model Broker

A request broker that sits in front of multiple LLMs and routes each prompt to the **cheapest model capable of handling it**. Simple prompts go to a fast, low-cost model; hard prompts are escalated to a stronger one. Built with FastAPI, with every model call kept behind one thin layer the service controls.

> **Honest framing:** this saves cost by *choosing a cheaper model per task*. It is not cheaper per token and it is not magic. The engineering is in deciding *which* tier a prompt needs, reliably and transparently.

---

## Architecture

A request to `/complete` flows through an ordered pipeline. Each stage answers exactly one question, and most stages can reject or short-circuit the request before it ever reaches a model.

```
                 POST /complete   { "prompt": "..." }
                          |
                          v
   Guardrail  per-IP rate limit       token bucket in Redis
                          |
                          v
   1. Validate            reject empty / whitespace  -> 400
                          |
                          v
   2. Safety gate         block prompt-injection      -> 403
                          |
                          v
   3. Intent shortcut     canned answers, no model call
                          |
                          v
   Guardrail  daily cap   global request ceiling      -> 429
                          |
                          v
   4. Difficulty router   cheap vs strong (hybrid heuristics)
                          |  (ambiguous)
                          v
   5. LLM escalation      cheap model judges difficulty
                          |
                          v
   Provider call (Groq)   max_tokens cap + 429 backoff + model fallback
                          |
                          v
                 { tier, reason, model, answer }
```

**Key design point:** *difficulty* ("how hard is this prompt?") and *safety* ("is this prompt allowed?") are separate questions. For example, `"ignore previous instructions"` is trivial by difficulty but must be blocked. They are kept in separate layers rather than folded into one classifier, so each stays simple and independently testable.

**Model tiers:** cheap is Llama 3.1 8B Instruct (Meta), strong is Qwen3-32B (Alibaba), both served by Groq. The tiers are cross-vendor by design, since a real broker should pick the best model per tier regardless of who made it. A third model, Llama 3.3 70B (Meta), is held in reserve as a reliability fallback. All three sit behind the provider layer and are swappable in one place, the `MODELS` dict in `provider.py`.

---

## What works today

- **Hybrid difficulty routing.** Deterministic signals (reasoning keywords, a code block, prompt length) classify the obvious cases for free, with no LLM call. A definitive signal sends the prompt to `strong` at any length; otherwise length decides three ways against two thresholds (`<=10` words is `cheap`, `>40` is `strong`, and the middle band is `ambiguous`).
- **LLM-classifier escalation.** This is the second half of the hybrid. An `ambiguous` prompt spends *one* call on the cheap model acting as a difficulty judge ("reply one word: cheap or strong"), then routes to its verdict. The judge runs at `temperature=0` so the same prompt always routes the same way, and an unparseable verdict fails safe to `strong`. This catches hard prompts phrased in plain words that keyword heuristics miss, without paying for an LLM call on every request.
- **Safety gate.** A precision-tuned heuristic blocks prompt-injection attempts (phrases that try to reach the system's instructions or override the model's identity) before any model call, returning `403`. It is one cheap layer in front of the model's own safety training, not an exhaustive filter.
- **Intent shortcut.** Fixed operational prompts (`ping`, `help`, `version`) return canned text with no classification and no model call. Matching is whole-prompt and exact rather than fuzzy, so it biases toward precision: a missed shortcut just falls through to a cheap call, but a wrong one would return the wrong answer.
- **Guardrails.** Three independent cost controls: a `max_tokens` ceiling bounds the length of any single response; a per-IP token-bucket rate limiter (in Redis) bounds how often one client can call; and a global daily cap (also in Redis) bounds total requests across all callers per UTC day. Over-limit requests get a `429` with a `Retry-After`.
- **Model fallback.** If the chosen model fails for a model-specific reason (a `5xx`, a timeout, or an unreachable endpoint), the provider layer retries once on the reserve model so the user still gets an answer. It deliberately does *not* fall back on a `429`, since the backup shares the same Groq budget and would be throttled too.
- **Transparent decisions.** Every response reports the chosen `tier`, the `word_count`, and a human-readable `reason` listing which signals fired (and, when escalated, that the judge decided). The decision is auditable, not a black box.
- **Swappable provider.** Every model call goes through one `call_model()` function, and the tier-to-model binding lives in a single `MODELS` dict, so swapping a model is a one-line edit.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Validation | Pydantic v2 |
| Provider | Groq (OpenAI-compatible API) |
| Rate-limit / daily-cap state | Redis (Upstash) |
| HTTP client | httpx |
| Config | python-dotenv |

---

## Project Structure

```
cost-aware-model-broker/
├── app/
│   ├── main.py        # FastAPI app: /health, /route, /complete + the pipeline wiring
│   ├── router.py      # classify(): difficulty heuristics (pure, no I/O)
│   ├── provider.py    # Groq calls, MODELS dict, retry/backoff, LLM judge, fallback
│   ├── safety.py      # is_safe(): prompt-injection gate (layer 2)
│   ├── intents.py     # match_intent(): canned answers for fixed prompts (layer 3)
│   ├── ratelimit.py   # per-IP token bucket in Redis (guardrail)
│   └── dailycap.py    # global daily request cap in Redis (guardrail)
├── streamlit_app.py   # Streamlit front end: a router console over /complete
├── requirements.txt
├── .env               # GROQ_API_KEY + Upstash Redis creds, never committed
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

**3. Add your credentials**

Create a `.env` file in the project root (never committed, see `.gitignore`):
```bash
GROQ_API_KEY=your_groq_key
UPSTASH_REDIS_REST_URL=your_upstash_url
UPSTASH_REDIS_REST_TOKEN=your_upstash_token
```
A free Groq key comes from [console.groq.com](https://console.groq.com); a free Redis database comes from [upstash.com](https://upstash.com). Redis backs the rate limiter and the daily cap.

**4. Run the API**
```bash
uvicorn app.main:app --reload
```

Interactive API docs at `http://localhost:8000/docs`.

**5. Run the UI** (optional, in a second terminal)
```bash
streamlit run streamlit_app.py
```
The router console opens at `http://localhost:8501` and talks to the backend on `localhost:8000` by default. When deployed, set a `BACKEND_URL` secret pointing at the hosted API's `/complete`.

**6. Try a request directly**
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
Classifies a prompt and returns the tier it should route to, without calling a model. Useful for inspecting the routing decision on its own.

**Request body:**
```json
{ "prompt": "What is the capital of France?" }
```

**Response:**
```json
{ "tier": "cheap", "word_count": 6, "reason": "no signals, short (6 <= 10 words)" }
```

### `POST /complete`
Runs the full pipeline: guardrails, safety gate, intent shortcut, classification, optional escalation, then calls the chosen model via Groq and returns the answer.

**Request body:**
```json
{ "prompt": "Explain why quicksort is fast" }
```

**Response:**
```json
{ "tier": "strong", "reason": "reasoning keyword(s): why, explain", "model": "qwen/qwen3-32b", "answer": "..." }
```

When the heuristics can't decide, the response shows the escalation trail, including the cheap-model judge's verdict:
```json
{ "tier": "strong", "reason": "ambiguous heuristics (no signals, mid-length (27 words) -> needs LLM judge) -> LLM judged 'strong'", "model": "qwen/qwen3-32b", "answer": "..." }
```

**Error responses:**
| Code | Meaning |
|---|---|
| `400` | Prompt is empty or whitespace-only |
| `403` | Blocked by the safety gate |
| `422` | Missing or malformed request body |
| `429` | Per-IP rate limit or global daily cap exceeded (see `Retry-After`) |
| `502` | Upstream model provider failed, and the fallback (if eligible) also failed |

### `GET /health`
Liveness check, returns `{ "status": "ok" }`.

---

## Key Design Decisions

**Why hybrid routing (heuristics + LLM escalation) instead of one or the other?**
Pure heuristics are free and instant but miss nuance; a pure LLM classifier catches nuance but adds an LLM call, and its cost, to *every* request, which undercuts the whole point. The hybrid keeps the common path free and only pays for a cheap LLM judgment on the prompts the heuristics can't confidently place. The deterministic heuristics live in one pure `classify()` function and the LLM judge lives in the provider layer, so each half can be tuned or replaced without touching the rest of the service.

**Why separate difficulty from safety?**
Routing by difficulty answers "which model?"; safety answers "should this run at all?". A short prompt can be trivial yet malicious, so collapsing the two into one classifier produces wrong decisions. Keeping them as distinct pipeline stages keeps each one simple and independently testable.

**Why three independent guardrails instead of one?**
They bound different things. `max_tokens` caps the length of a single response; the rate limiter caps how often one client calls; the daily cap bounds total spend across everyone. A client can stay under the per-IP rate limit and still, together with many others, drain the day's budget, which is exactly the gap the daily cap closes.

**Why fall back to another model, but not on a 429?**
A model can fail for reasons specific to it (its server errors, a timeout, an unreachable endpoint), and there a different model can succeed. A `429` is different: the backup shares the same Groq free budget, so it's rate-limited at the same time and falling back buys nothing. So the fallback triggers only on model-specific failures and fails fast on a `429`. A genuine `429`-fallback would need a differently-budgeted provider; the fallback is one entry in the `MODELS` dict, so adding one later is a small change.

**Why Groq as the provider?**
Groq serves many models (from Meta, Alibaba, OpenAI and more) behind one OpenAI-compatible API and a single key, so switching a tier is a one-line change with no per-provider SDKs to maintain, and every model call stays behind the one thin layer the service controls. It's also fast and has a usable free tier, which keeps the demo reliable.

**Why handle reasoning models specially?**
The strong tier (Qwen3) is a reasoning model: left alone it emits a `<think>` chain that burns the token budget and can be cut off before the answer. The provider layer sets `reasoning_effort="none"` for that model to get a direct answer. The cheap model rejects that parameter, which is why per-model request params live *with* the model in the `MODELS` config rather than being sent globally.

---

## Evaluation

How do you know the routing is any good? `evals/` runs a small labeled set of prompts through the *real* routing decision (intent shortcut, heuristics, and the LLM judge) and reports how often it agrees with hand-labeled tiers, plus how much it avoids the expensive model. On the current 22-prompt set:

| Metric | Result |
|---|---|
| Routing split | 41% cheap · 45% strong · 14% shortcut |
| Agreement with hand-labeled tiers | 16/22 (73%) |
| Requests that avoided the strong model | 12/22 (55%) |
| Modeled spend vs all-strong (assuming strong = 10× cheap/token) | ~50% of baseline |

The 73% is deliberately honest — the dataset includes the cases keyword heuristics are *known* to miss, so the score reflects real behavior rather than a curated win. The six misroutes split cleanly:

- **Under-routing (3):** plainly-phrased hard prompts with no trigger word (e.g. "how does a transformer work?") get sent cheap. This is the keyword false-negative — the limitation that motivates the LLM judge.
- **Over-routing (3):** trivial prompts that happen to contain a trigger word (e.g. "why is the sky blue?") get sent strong, plus one ambiguous prompt the judge escalated to be safe. These cost a little more but don't return *worse* answers — the errors are biased toward the safe (more expensive) direction by design.

This is also the honest argument for the routing *policy* being swappable: the keyword + length heuristic is transparent and free, but a production system would replace it (a small trained classifier or an embedding-based difficulty score) behind the same `classify()` seam, and this eval is how you'd measure the upgrade.

Run it yourself:
```bash
python -m evals.evaluate   # needs GROQ_API_KEY for the judge calls
```

---

## Roadmap

- [x] Provider call via Groq (`/complete`) with retry/backoff on transient `429`
- [x] LLM-classifier escalation for ambiguous prompts (layer 5)
- [x] Safety gate: prompt-injection filtering (layer 2)
- [x] Intent shortcut: canned responses for fixed intents, no LLM (layer 3)
- [x] Guardrails: `max_tokens` ceiling, per-IP rate limiter, global daily cap
- [x] Model fallback on model-specific failures
- [x] Streamlit UI (router console)
- [x] Routing eval (agreement + cost-avoided on a labeled set)
- [ ] Live hosted demo
