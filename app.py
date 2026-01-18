from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
API_KEY = "YOUR_GROQ_API_KEY"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

def call_groq(model, messages):
    payload = {
        "model": model,
        "messages": messages
    }
    r = requests.post(API_URL, headers=HEADERS, json=payload)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return msg.get("content", "")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/run", methods=["POST"])
def run_round():
    data = request.json

    question = data["question"]
    context = data.get("context")

    agent_a_model = "llama-3.1-8b-instant"
    agent_b_model = "openai/gpt-oss-20b"
    judge_model = "openai/gpt-oss-120b"

    messages_base = [{"role": "system", "content": "You are a reasoning AI agent."}]
    if context:
        messages_base.append({"role": "system", "content": context})

    a_answer = call_groq(
        agent_a_model,
        messages_base + [{"role": "user", "content": question}]
    )

    b_answer = call_groq(
        agent_b_model,
        messages_base + [{"role": "user", "content": question}]
    )

    judge_prompt = f"""
Question:
{question}

Agent A Answer:
{a_answer}

Agent B Answer:
{b_answer}

Give:
1. Correct answer
2. Who is correct (A, B, Both, Neither)
3. Clear reasoning
"""

    judge_verdict = call_groq(
        judge_model,
        [
            {"role": "system", "content": "You are a strict judge AI."},
            {"role": "user", "content": judge_prompt}
        ]
    )

    def agent_agrees(model):
        result = call_groq(
            model,
            [
                {"role": "system", "content": "Respond ONLY with AGREE or DISAGREE."},
                {"role": "user", "content": judge_verdict}
            ]
        )
        return result.strip().upper() == "AGREE"

    agree_a = agent_agrees(agent_a_model)
    agree_b = agent_agrees(agent_b_model)

    finished = agree_a and agree_b

    return jsonify({
        "agent_a": a_answer,
        "agent_b": b_answer,
        "judge": judge_verdict,
        "agree_a": agree_a,
        "agree_b": agree_b,
        "finished": finished,
        "new_context": f"Judge verdict to reconsider:\n{judge_verdict}"
    })

if __name__ == "__main__":
    app.run(debug=True)
