# Distributed Protocol for Reasoning (DPR) - Phase 1

This branch contains the **Phase-1 implementation** of DPR: a 4-agent reasoning loop with facilitator controls and human intervention commands.

It supports:

- sequential multi-agent reasoning turns
- quota-aware participation
- hand-raise queue interrupts
- anti-loop and fairness guards
- human inject and redirect control

---

## What Phase-1 Includes

- Fixed 4-agent panel in code (`AGENTS`)
- Turn progression with round-robin base + interrupt handling
- Quota economy (`STARTING_QUOTA = 8`)
- Hand-raise behavior:
  - random raise opportunity
  - queue enqueue for potential interrupts
- Anti-starvation prioritization using `last_spoke` + `STARVATION_THRESHOLD`
- Loop detection using normalized recent accepted responses
- Fairness repeat-streak limiter
- Running summary memory (`self.summary`) built from recent accepted turns

---

## Architecture

### Backend

- `app.py` - Flask API and session lifecycle
- `dpr_protocol.py` - DPR protocol engine (`DPRSession`) and model call logic

### Frontend

- `templates/index.html` - DPR console UI
- `static/app.js` - step loop + pause/resume/inject/redirect actions
- `static/style.css` - styling

### Config

- `.env.example` - sample environment file
- `.gitignore` - excludes `.env` and cache files

---

## Core Protocol Behavior (Phase-1)

- `/start` creates a session from a question
- `/step` selects next agent and executes one reasoning turn
- Context includes:
  - original problem
  - rolling summary
  - optional human instruction
  - optional redirect objective
- Agent response can be ignored when:
  - detected as a repeated loop
  - same speaker exceeds repeat-streak fairness limit
- Session ends when:
  - model outputs `FINAL DESIGN COMPLETE`, or
  - `MAX_TURNS` reached, or
  - all quotas exhausted

---

## API Endpoints

- `GET /` - UI
- `POST /start` - start session with `{ "question": "..." }`
- `POST /step` - run one protocol step
- `POST /pause` - pause session
- `POST /resume` - resume session
- `POST /inject` - inject human instruction `{ "message": "..." }`
- `POST /redirect` - set redirection objective `{ "message": "...", "turns": 3 }`

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
pip install flask requests python-dotenv
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

## Default Agent Models (Phase-1)

Defined in `dpr_protocol.py`:

- Agent 1: `llama-3.3-70b-versatile`
- Agent 2: `openai/gpt-oss-120b`
- Agent 3: `moonshotai/kimi-k2-instruct`
- Agent 4: `openai/gpt-oss-20b`

---

## Notes

- Uses Flask dev server (`debug=True`)
- Keep `.env` private and never commit real API keys
