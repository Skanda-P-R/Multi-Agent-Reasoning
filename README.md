# Multi-Agent Reasoning Web App (Flask + Groq)

This project is an interactive multi-agent reasoning system built with Flask and Groq LLMs.
It simulates two AI agents independently answering a question, a judge evaluating their responses, and an iterative reasoning loop until consensus is reached — all visualized in a modern, real-time web UI.

The UI features:

* Parallel agent responses (sentence-by-sentence typing)
* Judge evaluation with staged delays
* Visual agreement/disagreement indicators
* Multi-round navigation with history
* Markdown rendering
* Polished, modern dashboard-style layout


## Installation & Setup
### 1. Clone the repository
  ```
  git clone https://github.com/your-username/multi-agent-reasoning.git
  cd multi-agent-reasoning
  ```

### 2. Create a virtual environment (recommended)

  `python -m venv venv`
  
  Activate it:
  
  Windows
  
  `venv\Scripts\activate`
  
  
  macOS / Linux
  
  `source venv/bin/activate`

### 3. Install dependencies

`pip install -r requirements.txt`


Contents of requirements.txt:
```
flask
requests
```

### Getting a Groq API Key

This project uses the Groq API to run the AI agents and judge.

Step-by-step:

1. Go to  `https://console.groq.com/keys`
2. Sign up or log in with your account
3. Click Create API Key
4. Copy the generated key
`(It will look like: gsk_...)`
5. Configure the API Key
   * Open app.py and find this line:
     `API_KEY = "PUT_YOUR_API_KEY_HERE"`
   * Replace it with your actual Groq API key:
     `API_KEY = "gsk_your_actual_key_here"`

⚠️ Important:
Do NOT commit your real API key to a public repository.
If publishing publicly, use environment variables instead.

### Running the App

Start the Flask server:

`python app.py`


You should see output like:

`Running on http://127.0.0.1:5000`


Open your browser and go to:

`http://127.0.0.1:5000`


## Models Used

Agent A: llama-3.1-8b-instant

Agent B: openai/gpt-oss-20b

Judge: openai/gpt-oss-120b

(You can easily swap these in app.py.)
