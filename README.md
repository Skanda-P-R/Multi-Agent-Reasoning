# Distributed Protocol for Reasoning (DPR) - Phase 4

This branch is the **Phase-4 implementation** of DPR.

It builds on Phase-3 and adds:

- provider-aware model routing across Groq and OpenRouter
- OpenRouter-backed broadcast and selector flows to reduce Groq pressure
- expanded section-aware agent paneling
- saved chat history with restore and redirect-based continuation
- stricter LLM selector parsing with heuristic fallback
- cleaner chat transcripts that hide ignored/API-error turns
- stronger finalization gates before `FINAL DESIGN COMPLETE`
- updated broadcast test harness behavior for provider-split calls

---

## Architecture

### Backend

- `app.py` - Flask API routes, session lifecycle, history routes, and finalization routes
- `dpr_protocol.py` - `DPRSession` orchestrator, turn execution, human controls, completion gates
- `dpr_memory_mixin.py` - shared memory extraction, merge, context building, and completion-gate prompting
- `dpr_intent_mixin.py` - broadcast intent collection, section-fit validation, queue construction, speaker selection
- `dpr_selector.py` - question-based model/section panel suggestion using OpenRouter LLM routing plus fallback parsing
- `dpr_model_client.py` - provider-aware chat-completions caller for Groq, OpenRouter, and broadcast routing
- `dpr_constants.py` - model catalogs, provider URLs, section metadata, routing maps, runtime thresholds
- `dpr_history.py` - saved chat persistence and continuation support

### Frontend

- `templates/index.html`
- `static/app.js`
- `static/style.css`

The UI supports live protocol control, model-section selection, selector-source visibility, human interventions, history restore, and per-turn metadata display.

---

## Phase-4 Protocol Capabilities

- Dynamic agent panel at session start with `{ model, section }` entries
- Expanded section catalog:
  - `general`
  - `education`
  - `programming`
  - `research`
  - `product`
  - `design`
  - `business`
  - `operations`
  - `security`
  - `ethics`
- Model catalog and default panel exposure via `/models`
- OpenRouter-backed model panel recommendation via `/suggest_models`
- LLM-selected model-section pairs are respected exactly after validation and dedupe
  - same model ID may appear in multiple sections when the selector returns those pairs
  - exact duplicate model-section pairs are removed
  - if the LLM returns unusable output, heuristic fallback is used
- More resilient selector parsing:
  - JSON object
  - fenced JSON
  - prose-wrapped JSON
  - Python-style dicts
  - trailing-comma repair
  - raw-output preview in the UI for non-LLM fallback paths
- Queue-based next-speaker arbitration:
  - consume queued intents first
  - starvation fallback for long-waiting agents
  - broadcast if queue is empty
  - bootstrap fallback when required
- OpenRouter broadcast intent collection:
  - live Groq model IDs are mapped to OpenRouter broadcast equivalents
  - broadcast records include live model and broadcast model metadata
  - contribution/live turns still use Groq model IDs
- Section-aware broadcast behavior:
  - prompts include section-specific role headers
  - section-fit retry path for misaligned pointers
  - synthetic fallback pointers remain section-aligned
- Shared memory state:
  - `facts`
  - `options`
  - `decisions`
  - `open_questions`
  - `actions`
  - `changelog`
- Memory summarization uses Groq `openai/gpt-oss-safeguard-20b`
- Facilitator safeguards:
  - loop filtering
  - fairness repeat protection
  - redirect handling across turns
  - provider/API errors are logged internally instead of printed into the chat transcript
- Completion governance:
  - models are instructed not to emit `FINAL DESIGN COMPLETE` early
  - backend strips or defers premature completion markers
  - completion requires broad shared context, enough accepted turns, addressed pointers, agent coverage, no active pointer, and no pending broadcast pointers
- Human governance:
  - raise hand for direct human turn
  - submit human reasoning turn
  - submit human action turn (`inject` / `redirect`)
  - approve or continue after finalization candidate
  - continue restored history through redirect
- Saved chat history:
  - ended sessions can be saved from the UI before starting a new chat
  - saved chats are listed with title, saved time, agent count, and turn count
  - loading a saved chat restores transcript, agents, memory, facilitator logs, broadcast events, and runtime state
  - restored chats pause until the human supplies a redirect objective, preventing silent continuation from stale context
- Rich state metadata in responses:
  - `selection_reason`
  - queued interrupts
  - intent metadata (`intent_priority`, `intent_pointer`, `intent_confidence`, etc.)
  - memory snapshot
  - completion readiness details

---

## Saved Chat History

Phase-4 adds first-class local history persistence.

Saved session documents are written as JSON files under:

```text
session_history/
```

Each saved document includes:

- original question
- agent model-section panel
- accepted and ignored responses
- facilitator log
- shared memory
- broadcast events
- history events
- runtime state needed for continuation

History workflow:

1. Finish or stop a session.
2. Use **New Chat** to save the ended session into history.
3. Open the history drawer and select a saved chat.
4. Review the restored transcript and memory.
5. Continue only by entering a redirect objective.

The redirect requirement is intentional: a loaded chat may contain old assumptions, stale unresolved pointers, or a previous completion boundary, so Phase-4 asks the human to explicitly steer continuation before agents resume.

History endpoints:

- `GET /history` - list saved chat summaries
- `POST /history/save_current` - save the current ended session
- `GET /history/<history_id>` - fetch a saved document
- `POST /history/<history_id>/load` - restore a saved chat into the active session
- `POST /history/continue` - continue a restored chat with a redirect

---

## Provider Routing

Phase-4 separates where each kind of model call goes:

- Live agent answers: Groq
- Memory summaries: Groq
- Broadcast hand-raise intent calls: OpenRouter
- Model/section selector: `SELECTOR_MODEL` through `SELECTOR_PROVIDER`

Current constants:

```python
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPEN_ROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

MEMORY_MODEL = "openai/gpt-oss-safeguard-20b"
SELECTOR_MODEL = "openrouter/free"
SELECTOR_PROVIDER = "openrouter"
```

Broadcast mapping:

```text
llama-3.1-8b-instant    -> nvidia/nemotron-nano-9b-v2:free
llama-3.3-70b-versatile -> nvidia/nemotron-nano-12b-v2-vl:free
openai/gpt-oss-20b      -> openai/gpt-oss-20b:free
openai/gpt-oss-120b     -> openai/gpt-oss-120b:free
```

The selector sends the exact configured `SELECTOR_MODEL` string to OpenRouter. If that provider rejects the slug, update `SELECTOR_MODEL` in `dpr_constants.py`.

---

## API Endpoints

- `GET /` - UI
- `POST /start` - start session:
  - body: `{ "question": "...", "models": [...] }`
- `GET /models` - available models, default models, sections
- `POST /suggest_models` - suggest model-section panel for a question
- `POST /step` - execute one protocol step
- `POST /pause` - pause session
- `POST /resume` - resume session
- `POST /set_models` - update active panel while paused
- `POST /stop` - stop session
- `POST /inject` - inject human instruction
- `POST /raise_hand` - enqueue human hand raise
- `POST /human_turn` - submit human turn / human action
- `POST /redirect` - set redirect objective and duration
- `POST /finalize/approve` - approve completion candidate
- `POST /finalize/continue` - continue after completion candidate
- `GET /history` - list saved chats
- `POST /history/save_current` - save ended session
- `GET /history/<history_id>` - fetch saved chat document
- `POST /history/<history_id>/load` - load saved chat into session
- `POST /history/continue` - continue loaded chat with redirect

---

## Setup

### 1) Create and activate virtual environment

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

macOS/Linux:

```bash
source venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` includes:

- `flask`
- `requests`
- `python-dotenv`

### 3) Configure API keys

1. Copy `.env.example` to `.env`
2. Set:

```env
GROQ_API_KEY=gsk_your_actual_key_here
OPEN_ROUTER_API_KEY=sk-or-v1-your_actual_key_here
```

### 4) Run

```bash
python app.py
```

Open: `http://127.0.0.1:5000`

---

## Default Models (Phase-4)

Live Groq default panel:

- `llama-3.3-70b-versatile`
- `openai/gpt-oss-120b`
- `openai/gpt-oss-20b`

Available live Groq catalog additionally includes:

- `llama-3.1-8b-instant`

OpenRouter catalog used by Phase-4:

- `openai/gpt-oss-120b:free`
- `openai/gpt-oss-20b:free`
- `nvidia/nemotron-nano-12b-v2-vl:free`
- `nvidia/nemotron-nano-9b-v2:free`

---

## Broadcast Test Harness

Broadcast validation assets are under:

- `broadcast_tests/broadcast_handraise_test.py`
- `broadcast_tests/artifacts/`

Run from project root:

```bash
python broadcast_tests/broadcast_handraise_test.py --question "Design a campus waste-sorting system"
```

With contribution phase:

```bash
python broadcast_tests/broadcast_handraise_test.py --question "Design a campus waste-sorting system" --run-contribution
```

Phase-4 behavior:

- broadcast intent calls use OpenRouter equivalents
- contribution calls use live Groq model IDs
- `OPEN_ROUTER_API_KEY` is required for intent-only runs
- `GROQ_API_KEY` is also required when `--run-contribution` is used

For artifact matrix and usage notes, see [broadcast_tests README](broadcast_tests/README.md).

---

## Phase-4 Change Summary

- Split old single Groq API URL into Groq and OpenRouter endpoints.
- Introduced provider-aware `call_model(...)` routing.
- Kept UI/session model IDs as Groq live model IDs.
- Routed broadcast hand-raise prompts through OpenRouter mappings.
- Routed selector calls through `SELECTOR_PROVIDER`.
- Added expanded section headers for broader agent specialization.
- Hardened selector parsing while preserving the selector model's exact returned panel.
- Hid ignored turns and provider errors from the visible chat transcript.
- Added stricter completion readiness checks before finalization.
- Added local saved-chat history with restore and redirect-gated continuation.
- Updated broadcast test harness docs for OpenRouter intent calls.

---

## Notes

- This runs with Flask dev server (`debug=True`).
- Keep `.env` private and never commit real API keys.
