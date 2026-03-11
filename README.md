# Distributed Protocol for Reasoning (DPR)

This project is a web-based implementation of a **Distributed Protocol for Reasoning (DPR)** with multi-agent turn-taking and human governance controls.

It runs a shared reasoning session across 4 agents and provides a live console for:

- start / pause / resume
- human inject instructions
- human redirect objective (for N turns)
- visible protocol metadata (ignored responses, quota, hand-queue)

---

## Current Architecture

### Backend

- `app.py` — Flask API + session lifecycle
- `dpr_protocol.py` — DPR protocol engine (`DPRSession`)

### Frontend

- `templates/index.html` — console layout
- `static/app.js` — UI actions + polling loop
- `static/style.css` — styling

### Config / Safety

- `.env.example` — Sample API key config
- `.gitignore` — excludes `.env` and Python cache files

---

## Protocol Features Implemented

### Phase 1 Core

- Talking-stick style sequencing (round-robin base)
- Basic fairness / sequencing controls
- Human console commands: **PAUSE**, **RESUME**, **INJECT**
- Logged ignored responses (with reason)

### Phase 2 Governance

- Hand-raise interrupt queue
- Quota-based contribution economy (`STARTING_QUOTA`)
- Priority ordering using proximity-to-token for queued interrupts
- Anti-starvation prioritization using `last_spoke` threshold

### Facilitator Rules

- Loop detection (normalized recent-response repeat check)
- Fairness repeat-streak protection
- Redirection support via human **REDIRECT** command
- Facilitator event logging (`facilitator_log`)

---

## API Endpoints

- `POST /start` → start session with `{ "question": "..." }`
- `POST /step` → run one protocol step
- `POST /pause` → pause session
- `POST /resume` → resume session
- `POST /inject` → inject instruction `{ "message": "..." }`
- `POST /redirect` → redirect objective `{ "message": "...", "turns": 3 }`

---

## Setup

### 1) Create & activate virtual environment (recommended)

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

### 3) Configure Groq API key with `.env`

In `Multi-Agent-Reasoning/`:

1. Copy `.env.example` to `.env`
2. Edit `.env` and set:

```env
GROQ_API_KEY=gsk_your_actual_key_here
```

### 4) Run

From `Multi-Agent-Reasoning/`:

```bash
python app.py
```

Open: `http://127.0.0.1:5000`

---

## Models (default)

Configured in `dpr_protocol.py`:

- Agent 1: `llama-3.3-70b-versatile`
- Agent 2: `openai/gpt-oss-120b`
- Agent 3: `moonshotai/kimi-k2-instruct`
- Agent 4: `openai/gpt-oss-20b`

You can modify the `AGENTS` list to change models.

---

## Notes

- This uses Flask dev server (`debug=True`) and is not production-ready.
- Keep `.env` private and never commit real API keys.
