# Distributed Protocol for Reasoning (DPR) - Phase 3

This branch is the **Phase-3 implementation** of DPR.

It includes:

- modular protocol engine (core session + mixins)
- broadcast-driven hand-raise intent queue
- starvation fallback selection
- shared structured memory with context synthesis
- human-in-the-loop turns and finalization governance
- model panel configuration and model suggestion APIs

---

## Architecture

### Backend

- `app.py` - Flask API routes and session lifecycle
- `dpr_protocol.py` - `DPRSession` orchestrator (main loop + human controls)
- `dpr_memory_mixin.py` - memory extraction, merge, and context building
- `dpr_intent_mixin.py` - broadcast, intent parsing, queue selection logic
- `dpr_selector.py` - question-based model panel suggestion (LLM + heuristic fallback)
- `dpr_model_client.py` - Groq model API caller
- `dpr_constants.py` - model catalog and protocol constants

### Frontend

- `templates/index.html`
- `static/app.js`
- `static/style.css`

---

## Phase-3 Protocol Capabilities

- Dynamic agent panel at session start (`selected_models` with section tags)
- Model catalog and default panel exposure via `/models`
- Model panel recommendation via `/suggest_models`
- Queue-based next-speaker arbitration:
  - consume queued intents first
  - starvation selection for long-waiting agents
  - broadcast if queue is empty
  - fallback bootstrap when required
- Quota-aware broadcast behavior:
  - prompts include remaining quota
  - low-quota agents are gated to high-value hand raises
- Pointer-aware turn context:
  - selected intent pointer is passed into context
  - starvation-selected turns intentionally use empty pointer
- Shared memory state:
  - `facts`, `options`, `decisions`, `open_questions`, `actions`, `changelog`
- Facilitator controls:
  - loop filtering
  - fairness repeat protection
  - redirect handling across turns
- Human governance:
  - raise hand for direct human turn
  - submit human reasoning turn
  - submit human action turn (`inject` / `redirect`)
  - approve or continue after finalization candidate
- Rich state metadata in step responses:
  - `selection_reason`
  - `hand_raise_scores`
  - `queued_interrupts`
  - intent metadata (`intent_priority`, `intent_pointer`, etc.)

---

## API Endpoints

- `GET /` - UI
- `POST /start` - start session:
  - body: `{ "question": "...", "models": [...] }`
- `GET /models` - available models, default models, sections
- `POST /suggest_models` - suggest model panel for a question
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

## Default Models (Phase-3)

From `dpr_constants.py`:

- `llama-3.3-70b-versatile`
- `openai/gpt-oss-120b`
- `openai/gpt-oss-20b`

Available catalog additionally includes:

- `llama-3.1-8b-instant`
- `groq/compound-mini`
- `openai/gpt-oss-safeguard-20b`

---

## Notes

- This runs with Flask dev server (`debug=True`)
- Keep `.env` private and never commit real API keys
