# Distributed Protocol for Reasoning (DPR)

A production-style research prototype for orchestrating multi-agent reasoning with structured facilitator controls, human governance, and memory-aware turn selection.

This `main` branch is merged with the `Phase-3` branch.

---

## Overview

The system runs a guided reasoning session where multiple LLM agents collaborate on a shared problem under a protocol that balances:

- contribution quality
- fairness in participation
- anti-loop safeguards
- human oversight at critical decision points

Core capabilities include dynamic model panel selection, broadcast-based hand-raise intent collection, starvation-aware fallback selection, structured shared memory updates, and human finalization approval flow.

---

## System Architecture

### Backend Modules

- `app.py`  
  Flask API surface for session lifecycle, intervention commands, and finalization routes.

- `dpr_protocol.py`  
  `DPRSession` orchestrator for session state, step execution, human turn control, and completion handling.

- `dpr_intent_mixin.py`  
  Intent parsing, broadcast prompting, queue construction, starvation fallback, and next-speaker arbitration.

- `dpr_memory_mixin.py`  
  Shared memory extraction/cleanup, delta merging, context synthesis, and state payload generation.

- `dpr_selector.py`  
  Question-aware model/section recommendation using LLM routing with heuristic fallback.

- `dpr_model_client.py`  
  Groq chat-completions API client wrapper.

- `dpr_constants.py`  
  Runtime limits, model catalog, section metadata, memory tuning, and scoring thresholds.

### Frontend

- `templates/index.html`
- `static/app.js`
- `static/style.css`

The UI supports live session control, human intervention actions, and per-turn protocol metadata display.

---

## Protocol Features

### 1. Dynamic Agent Paneling

- Supports user-provided model panel selection at session start.
- Validates model compatibility against `AVAILABLE_MODELS`.
- Supports section assignment per agent (`general`, `programming`, `education`, `research`).

### 2. Broadcast-Driven Turn Arbitration

- When queue is empty, all eligible agents receive a broadcast intent prompt.
- Agents return structured JSON with:
  - hand raise decision
  - priority
  - pointer (next concrete contribution)
  - confidence
- Intents are filtered for section fit, pointer quality, novelty, and relevance.
- Low-quota agents are gated to higher-value raises only.
- Broadcast processing also applies:
  - one retry path for section misalignment
  - deduplication of near-identical pointer themes
  - fallback queue recovery from raw candidates when too few valid raises exist
  - synthetic section-aligned fallback pointers to maintain protocol momentum
- Final queue ordering is normalized by priority rank, confidence, and deterministic agent tie-breaking.

### 3. Queue + Starvation Fallback Logic

Speaker selection uses a layered strategy:

1. Consume existing `intent_queue`
2. Apply starvation fallback for long-waiting agents
3. Broadcast for fresh intents
4. Retry starvation fallback
5. Bootstrap/random fallback (eligible agent)

Starvation-selected turns intentionally use empty pointers so non-broadcast selections do not inherit synthetic pointer intent.

### 4. Shared Memory Model

The protocol maintains structured memory:

- `facts`
- `options`
- `decisions`
- `open_questions`
- `actions`
- `changelog`

Memory is updated each accepted turn using a summarizer model plus robust fallback heuristics when parsing fails.

### 5. Facilitator Safeguards

- Loop detection over recent accepted responses
- Fairness repeat-streak suppression
- Redirect objective injection with bounded duration
- Logged facilitator events for traceability

### 6. Human Governance Controls

- Raise human hand and enter protocol queue
- Submit direct human reasoning turn
- Submit human action turn (`inject` / `redirect`)
- Stop session
- Finalization gate:
  - approve completion candidate
  - continue after candidate (optionally with redirect)

### 7. Rich State and Observability

Step payloads expose protocol internals for UI/debugging, including:

- `selection_reason`
- `hand_raise_scores`
- queued interrupts
- intent metadata
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
- `POST /suggest_models` - model panel recommendation for a question

### Human Intervention

- `POST /inject` - inject facilitator instruction
- `POST /raise_hand` - enqueue human turn request
- `POST /human_turn` - submit human turn/action
- `POST /redirect` - apply redirect objective for N turns

### Finalization

- `POST /finalize/approve` - accept completion candidate
- `POST /finalize/continue` - continue reasoning after candidate

---

## Configuration

Environment variable:

- `GROQ_API_KEY` - required for model API calls

Setup:

1. Copy `.env.example` to `.env`
2. Set:

```env
GROQ_API_KEY=gsk_your_actual_key_here
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

Default panel (`DEFAULT_AGENT_MODELS`):

- `llama-3.3-70b-versatile`
- `openai/gpt-oss-120b`
- `openai/gpt-oss-20b`

Current available catalog (`AVAILABLE_MODELS`) also includes:

- `llama-3.1-8b-instant`
- `groq/compound-mini`
- `openai/gpt-oss-safeguard-20b`

---

## Broadcast Test Artifacts

Broadcast validation assets are included under:

- `broadcast_tests/broadcast_handraise_test.py` - broadcast/hand-raise test harness
- `broadcast_tests/artifacts/` - saved JSON outputs for analysis and reproducibility

Current JSON artifacts include:

- `broadcast_debug.json`
- `default_no_contribution.json`
- `default_with_contribution.json`
- `default_with_contribution_compact.json`
- `all_models_no_contribution.json`
- `all_models_with_contribution.json`
- `all_models_with_contribution_compact.json`

These files capture model intent outputs, parsing behavior, and contribution/no-contribution scenarios used to inspect broadcast decision quality.

For execution flow, artifact format, and usage notes, see [broadcast_tests README](Multi-Agent-Reasoning/broadcast_tests/README.md).

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

---

## Notes

- Runs on Flask development server (`debug=True`) by default.
- Keep `.env` private; never commit real API keys.
