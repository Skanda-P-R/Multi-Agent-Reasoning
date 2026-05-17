# Distributed Protocol for Reasoning (DPR)

A production-style research prototype for orchestrating multi-agent reasoning with structured facilitator controls, provider-aware model routing, saved history, human governance, and memory-aware turn selection.

This `main` branch now includes the `Phase-4` merge.

---

## Overview

The system runs a guided reasoning session where multiple LLM agents collaborate on a shared problem under a protocol that balances:

- contribution quality
- fairness in participation
- rate-limit pressure across model providers
- anti-loop and early-completion safeguards
- saved context continuity across sessions
- human oversight at critical decision points

Core capabilities include dynamic model/section panel selection, Groq-backed live agent answers, OpenRouter-backed broadcast intent collection, starvation-aware fallback selection, structured shared memory updates, local chat history, and human finalization approval flow.

---

## System Architecture

### Backend Modules

- `app.py` - Flask API surface for session lifecycle, intervention commands, history routes, and finalization routes.

- `dpr_protocol.py` - `DPRSession` orchestrator for session state, step execution, human turn control, restored-history continuation, and completion handling.

- `dpr_intent_mixin.py` - Intent parsing, OpenRouter broadcast prompting, queue construction, section-fit validation, starvation fallback, and next-speaker arbitration.

- `dpr_memory_mixin.py` - Shared memory extraction/cleanup, delta merging, context synthesis, state payload generation, and completion-readiness checks.

- `dpr_selector.py` - Question-aware model/section recommendation using LLM routing with strict parsing and heuristic fallback.

- `dpr_model_client.py` - Provider-aware chat-completions client for Groq, OpenRouter, and broadcast model mapping.

- `dpr_constants.py` - Runtime limits, provider URLs, model catalogs, section metadata, routing maps, memory tuning, and scoring thresholds.

- `dpr_history.py` - Local saved-chat persistence, history summaries, history loading, and continuation support.

### Frontend

- `templates/index.html`
- `static/app.js`
- `static/style.css`

The UI supports live session control, model/section selection, selector diagnostics, human intervention actions, history restore, and per-turn protocol metadata display.

---

## Protocol Features

### 1. Dynamic Agent Paneling and Selector Routing

- Supports user-provided model panel selection at session start.
- Accepts model-section entries such as `{ "model": "...", "section": "design" }`.
- Validates live session models against the Groq-facing catalog.
- Supports expanded section assignment per agent:
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
- Uses an LLM selector through `SELECTOR_PROVIDER` and `SELECTOR_MODEL`.
- Respects the selector model's returned panel after validation and dedupe.
- Allows the same live model to appear in multiple sections when the selector returns those model-section pairs.
- Falls back to heuristic selection when LLM selection is unavailable, malformed, or insufficient.
- Shows selector source, fallback reason, and raw selector preview in the UI when useful.

### 2. Provider-Aware Model Routing

Phase-4 separates model calls by purpose:

- Live agent answers use Groq model IDs.
- Shared memory summarization uses Groq `openai/gpt-oss-safeguard-20b`.
- Broadcast hand-raise intent calls use OpenRouter model equivalents.
- Model/section selection uses the configured selector provider.

Current provider constants:

```python
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPEN_ROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

MEMORY_MODEL = "openai/gpt-oss-safeguard-20b"
SELECTOR_MODEL = "openrouter/free"
SELECTOR_PROVIDER = "openrouter"
```

Broadcast mapping keeps the UI/session panel on Groq model IDs while moving intent-only traffic to OpenRouter:

```text
llama-3.1-8b-instant    -> nvidia/nemotron-nano-9b-v2:free
llama-3.3-70b-versatile -> nvidia/nemotron-nano-12b-v2-vl:free
openai/gpt-oss-20b      -> openai/gpt-oss-20b:free
openai/gpt-oss-120b     -> openai/gpt-oss-120b:free
```

### 3. Broadcast-Driven Turn Arbitration

- When the queue is empty, eligible agents receive a broadcast intent prompt.
- Broadcast prompts are section-aware and include the agent's assigned role focus.
- Agents return structured intent data:
  - hand raise decision
  - priority
  - pointer for the next concrete contribution
  - confidence
- Intents are filtered for section fit, pointer quality, novelty, and relevance.
- Broadcast results record both the live Groq model and the OpenRouter broadcast model used.
- Provider/API errors are logged internally and hidden from the visible chat transcript.

### 4. Queue + Starvation Fallback Logic

Speaker selection uses a layered strategy:

1. Consume existing `intent_queue`
2. Apply starvation fallback for long-waiting agents
3. Broadcast for fresh intents
4. Retry starvation fallback
5. Bootstrap fallback to an eligible agent

Starvation-selected turns intentionally use empty pointers so non-broadcast selections do not inherit synthetic pointer intent.

### 5. Shared Memory Model

The protocol maintains structured memory:

- `facts`
- `options`
- `decisions`
- `open_questions`
- `actions`
- `changelog`

Memory is updated each accepted turn using a summarizer model plus robust fallback heuristics when parsing fails.

### 6. Saved Chat History

Saved chat history is a first-class Phase-4 feature.

Ended sessions can be saved locally and restored later from the UI. Saved documents are written as JSON under:

```text
session_history/
```

Each saved document includes:

- original question
- selected agent model-section panel
- accepted and ignored responses
- facilitator log
- shared memory
- broadcast events
- history events
- runtime state needed for continuation

History workflow:

1. Finish or stop a session.
2. Use **New Chat** to save the ended session into history.
3. Open the history drawer and load a saved chat.
4. Review the restored transcript and memory.
5. Continue only by entering a redirect objective.

The redirect requirement prevents silent continuation from stale assumptions or an old completion boundary.

### 7. Completion and Facilitator Safeguards

- Loop detection over recent accepted responses
- Fairness repeat-streak suppression
- Redirect objective injection with bounded duration
- Section-aware fallback pointers to maintain protocol momentum
- Early `FINAL DESIGN COMPLETE` suppression
- Completion readiness checks for broad shared context, accepted-turn count, addressed pointers, agent coverage, and pending broadcast work
- Logged facilitator events for traceability

### 8. Human Governance Controls

- Raise human hand and enter protocol queue
- Submit direct human reasoning turn
- Submit human action turn (`inject` / `redirect`)
- Stop session
- Approve completion candidate
- Continue after completion candidate with optional redirect
- Continue restored history only through redirect-based steering

### 9. Rich State and Observability

Step payloads expose protocol internals for UI/debugging, including:

- `selection_reason`
- selector source and fallback reason
- broadcast live/broadcast model metadata
- queued interrupts
- intent metadata
- completion readiness details
- memory snapshot

---

## API Endpoints

### Session Lifecycle

- `GET /` - UI entrypoint
- `POST /start` - start session (`question`, optional `models`)
- `POST /step` - execute one protocol step
- `POST /pause` - pause session
- `POST /resume` - resume session
- `POST /set_models` - update active panel while paused
- `POST /stop` - terminate session

### Model Selection

- `GET /models` - available/default models and supported sections
- `POST /suggest_models` - model/section panel recommendation for a question

### Human Intervention

- `POST /inject` - inject facilitator instruction
- `POST /raise_hand` - enqueue human turn request
- `POST /human_turn` - submit human turn/action
- `POST /redirect` - apply redirect objective for N turns

### Finalization

- `POST /finalize/approve` - accept completion candidate
- `POST /finalize/continue` - continue reasoning after candidate

### History

- `GET /history` - list saved chat summaries
- `POST /history/save_current` - save the current ended session
- `GET /history/<history_id>` - fetch a saved document
- `POST /history/<history_id>/load` - restore a saved chat into the active session
- `POST /history/continue` - continue a restored chat with a redirect

---

## Configuration

Environment variables:

- `GROQ_API_KEY` - required for Groq live turns and memory calls
- `OPEN_ROUTER_API_KEY` - required for OpenRouter selector and broadcast calls

Setup:

1. Copy `.env.example` to `.env`
2. Set:

```env
GROQ_API_KEY=gsk_your_actual_key_here
OPEN_ROUTER_API_KEY=sk-or-v1-your_actual_key_here
```

---

## Installation and Run

1. Create virtual environment:

```bash
python -m venv venv
```

2. Activate environment:

Windows:

```bash
venv\Scripts\activate
```

macOS/Linux:

```bash
source venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Start server:

```bash
python app.py
```

5. Open:

`http://127.0.0.1:5000`

---

## Default Model Configuration

Default live Groq panel (`DEFAULT_AGENT_MODELS`):

- `llama-3.3-70b-versatile`
- `openai/gpt-oss-120b`
- `openai/gpt-oss-20b`

Current live Groq catalog (`AVAILABLE_MODELS`) also includes:

- `llama-3.1-8b-instant`

Current OpenRouter broadcast catalog includes:

- `openai/gpt-oss-120b:free`
- `openai/gpt-oss-20b:free`
- `nvidia/nemotron-nano-12b-v2-vl:free`
- `nvidia/nemotron-nano-9b-v2:free`

---

## Broadcast Test Artifacts

Broadcast validation assets are included under:

- `broadcast_tests/broadcast_handraise_test.py` - broadcast/hand-raise test harness
- `broadcast_tests/artifacts/` - saved JSON outputs for analysis and reproducibility

Example run:

```bash
python broadcast_tests/broadcast_handraise_test.py --question "Design a campus waste-sorting system"
```

Run with contribution turns:

```bash
python broadcast_tests/broadcast_handraise_test.py --question "Design a campus waste-sorting system" --run-contribution
```

Phase-4 behavior in the harness:

- broadcast intent calls use OpenRouter equivalents
- contribution calls use live Groq model IDs
- `OPEN_ROUTER_API_KEY` is required for intent-only runs
- `GROQ_API_KEY` is also required when `--run-contribution` is used

For execution flow, artifact format, and usage notes, see [broadcast_tests README](broadcast_tests/README.md).

---

## Branch Feature Snapshot

- `Phase-0`
  - Two-agent plus judge iterative loop with consensus-style continuation.
  - Basic round UI with verdict-driven context carry-forward.

- `Phase-1`
  - Four-agent protocol with quotas, hand queue, loop/fairness checks.
  - Human `inject` and `redirect` controls added to runtime flow.

- `Phase-2`
  - Dynamic model panel APIs and model suggestion endpoint introduced.
  - Shared structured memory and richer governance/finalization flow expanded.

- `Phase-3`
  - Protocol modularized into orchestrator + intent/memory mixins.
  - Broadcast-intent arbitration, starvation fallback, and detailed observability stabilized.

- `Phase-4`
  - Provider-aware Groq/OpenRouter routing added to reduce Groq rate-limit pressure.
  - OpenRouter-backed broadcast and selector flows introduced.
  - Expanded section-aware paneling from four sections to ten sections.
  - Selector parsing hardened while preserving the LLM-selected panel.
  - Provider/API errors hidden from chat output while staying available in logs/metadata.
  - Premature `FINAL DESIGN COMPLETE` behavior gated by stronger completion readiness checks.
  - Local saved-chat history added with restore and redirect-gated continuation.

---

## Notes

- Runs on Flask development server (`debug=True`) by default.
- Keep `.env` private; never commit real API keys.
