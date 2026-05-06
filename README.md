# Multi-Agent Reasoning (Phase-0)

Phase-0 is the first implementation of this project: a Flask web app where:

- Agent A answers a question
- Agent B answers the same question
- A Judge model evaluates both answers
- Both agents then vote `AGREE` or `DISAGREE` with the judge verdict
- The system runs multiple rounds until both agents agree

The UI shows each round in real time with typing animation, agreement indicators, and round navigation.

## Features in Phase-0

- Two-agent parallel reasoning (`Agent A`, `Agent B`)
- Judge-based arbitration after each round
- Iterative loop with carry-forward context from judge verdict
- Automatic stop when both agents agree (`finished = true`)
- Round history with previous/next navigation
- Markdown rendering of responses
- Visual agreement states (green/red)

## Tech Stack

- Python + Flask
- Groq Chat Completions API
- Vanilla JavaScript frontend

## Project Structure

```
app.py
templates/
  index.html
static/
  app.js
  style.css
```

## Setup

1. Clone and enter the project:

```bash
git clone <your-repo-url>
cd Multi-Agent-Reasoning
```

2. Create and activate virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install flask requests
```

## Configure API Key

Phase-0 currently keeps the key directly in `app.py`.

Open [app.py](/d:/8th Sem Major Project/Multi-Agent-Reasoning/app.py) and set:

```python
API_KEY = "YOUR_GROQ_API_KEY"
```

Do not commit a real key.

## Run

```bash
python app.py
```

Then open:

`http://127.0.0.1:5000`

## API Flow (Phase-0)

- `GET /` -> serves UI
- `POST /run` -> runs one full round:
  - Agent A response
  - Agent B response
  - Judge verdict
  - Agent agreement checks
  - Returns `finished` and `new_context`

Frontend repeats `/run` automatically until `finished` is true.

## Default Models in Phase-0

- Agent A: `llama-3.1-8b-instant`
- Agent B: `openai/gpt-oss-20b`
- Judge: `openai/gpt-oss-120b`
