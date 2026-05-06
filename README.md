# Distributed Protocol for Reasoning (DPR) - Phase 2

This branch contains the **Phase-2 implementation** of DPR: a multi-agent reasoning system with:

- dynamic model panel selection
- section-aware agents (general / programming / education / research)
- score-based hand-raise queue
- shared structured memory
- human-in-the-loop turns and finalization control

---

## What Phase-2 Adds

Compared to earlier phases, this version includes:

- Model catalog + defaults exposed via API
- Question-based model suggestion endpoint
- Per-agent quota economy (`STARTING_QUOTA = 15`)
- Score-based hand-raise selection (relevance, novelty, confidence, fairness)
- Shared memory state (`facts`, `options`, `decisions`, `open_questions`, `actions`, `changelog`)
- Human hand raise + explicit human turn submission
- Finalization gate:
  - completion candidate can be raised
  - human can approve or continue with redirect
- Rich protocol metadata returned each step (`selection_reason`, `hand_raise_scores`, queued interrupts, memory)

---

## Architecture

### Backend

- `app.py` - Flask API and session routes
- `dpr_protocol.py` - Phase-2 DPR engine (`DPRSession`)
- `dpr_selector.py` - LLM + heuristic panel/model suggestion
- `dpr_model_client.py` - Groq API caller
- `dpr_constants.py` - models, thresholds, protocol constants

### Frontend

- `templates/index.html` - console UI
- `static/app.js` - interaction loop and controls
- `static/style.css` - styling

---

## Core Protocol Behavior (Phase-2)

- Agents are initialized from selected models (`/start` payload can pass model+section choices)
- Each step selects next speaker using queue + scoring logic
- Responses are accepted/ignored with facilitator checks (loop/fairness)
- Shared memory is updated continuously and fed back into context
- Human can intervene using:
  - `inject`
  - `redirect`
  - `raise_hand`
  - `human_turn` (direct reasoning turn or action)
- If a completion candidate is raised, session enters `awaiting_human_finalization`

---

## API Endpoints

- `GET /` - UI
- `POST /start` - start session:
  - body: `{ "question": "...", "models": [...] }`
- `GET /models` - available models, defaults, sections
- `POST /suggest_models` - suggestion for model panel based on question
- `POST /step` - run one protocol step
- `POST /pause` - pause session
- `POST /resume` - resume session
- `POST /set_models` - update active model panel (session must be paused)
- `POST /stop` - stop session
- `POST /inject` - add human instruction
- `POST /raise_hand` - enqueue human turn request
- `POST /human_turn` - submit human turn / human action
- `POST /redirect` - set facilitator redirect objective for N turns
- `POST /finalize/approve` - approve completion candidate
- `POST /finalize/continue` - reject completion and continue (optional redirect)

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

### 3) Configure API key

1. Copy `.env.example` to `.env`
2. Set:

```env
GROQ_API_KEY=gsk_your_actual_key_here
```

### 4) Run

```bash
python app.py
```

Open: `http://127.0.0.1:5000`

---

## Default Models

From `dpr_constants.py`:

- Defaults:
  - `llama-3.3-70b-versatile`
  - `openai/gpt-oss-120b`
  - `openai/gpt-oss-20b`
- Available catalog also includes:
  - `llama-3.1-8b-instant`
  - `groq/compound-mini`
  - `openai/gpt-oss-safeguard-20b`
  - `qwen/qwen3-32b`

---

## Notes

- Runs on Flask dev server (`debug=True`)
- Keep `.env` private and never commit real API keys
