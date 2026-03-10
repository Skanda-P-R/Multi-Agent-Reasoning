import requests
import random
from collections import deque

API_URL = "https://api.groq.com/openai/v1/chat/completions"
API_KEY = "YOUR_API_KEY"

HEADERS = {
"Content-Type": "application/json",
"Authorization": f"Bearer {API_KEY}"
}

AGENTS = [
{"name": "Agent 1", "model": "llama-3.3-70b-versatile"},
{"name": "Agent 2", "model": "moonshotai/kimi-k2-instruct"},
{"name": "Agent 3", "model": "qwen/qwen3-32b"},
{"name": "Agent 4", "model": "openai/gpt-oss-20b"},
]

SUMMARY_MODEL = "openai/gpt-oss-120b"

MAX_TURNS = 60
STARTING_QUOTA = 8
SUMMARY_LIMIT_WORDS = 350


def call_model(model, messages, max_tokens=500):

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.6
    }

    r = requests.post(API_URL, headers=HEADERS, json=payload)
    r.raise_for_status()

    msg = r.json()["choices"][0]["message"]
    return msg.get("content", "")


def update_summary(previous_summary, new_text):

    prompt = f"""
You maintain a running reasoning summary.

Previous summary:
{previous_summary}

New reasoning contribution:
{new_text}

Update the summary so it captures ALL reasoning so far. Keep it Paragraph-wise, not point-wise.

Limit the result to about {SUMMARY_LIMIT_WORDS} words.
Do not remove important design decisions.
"""

    messages = [
        {"role": "system", "content": "You summarize collaborative reasoning states."},
        {"role": "user", "content": prompt}
    ]

    return call_model(SUMMARY_MODEL, messages, 500)



class DPRSession:

    def __init__(self, question):

        self.question = question
        self.turn = 0

        self.responses = []
        self.summary = ""

        self.paused = False

        self.pending_human_instruction = None

        self.quotas = {
            a["name"]: STARTING_QUOTA
            for a in AGENTS
        }

        self.last_spoke = {
            a["name"]: -1
            for a in AGENTS
        }

        self.hand_queue = deque()
        self.current_index = 0


    # --------------------------------------------
    # CONTEXT
    # --------------------------------------------

    def build_context(self, agent_name):

        human_block = ""

        if self.pending_human_instruction:

            human_block = f"""
Human instruction:
{self.pending_human_instruction}

You MUST incorporate this instruction into your reasoning.
"""

            self.pending_human_instruction = None


        return f"""
You are {agent_name} in a distributed reasoning protocol.

Original problem:
{self.question}

Current design summary:
{self.summary}

{human_block}

Instructions:
- Continue the design logically.
- Do NOT repeat earlier reasoning.
- Add new improvements or extensions.
- If the design is complete write: FINAL DESIGN COMPLETE
"""


    # --------------------------------------------
    # HAND RAISE
    # --------------------------------------------

    def maybe_raise_hand(self, agent):

        if random.random() < 0.25:

            if agent not in self.hand_queue:
                self.hand_queue.append(agent)


    # --------------------------------------------
    # PRIORITY ORDER
    # --------------------------------------------

    def select_next_agent(self):

        if self.hand_queue:

            agent = self.hand_queue.popleft()

            if self.quotas[agent] > 0:
                return agent


        for i in range(len(AGENTS)):

            idx = (self.current_index + i) % len(AGENTS)
            agent = AGENTS[idx]["name"]

            if self.quotas[agent] > 0:

                self.current_index = (idx + 1) % len(AGENTS)
                return agent

        return None


    # --------------------------------------------
    # STEP
    # --------------------------------------------

    def step(self):

        if self.paused:
            return {"status": "paused"}

        if self.turn >= MAX_TURNS:

            return {
                "status": "done",
                "agent": "System",
                "text": "Session ended: max turns reached.",
                "round": self.turn
            }


        agent_name = self.select_next_agent()

        if not agent_name:

            return {
                "status": "done",
                "agent": "System",
                "text": "All agents exhausted quotas.",
                "round": self.turn
            }


        model = next(a["model"] for a in AGENTS if a["name"] == agent_name)

        context = self.build_context(agent_name)

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": "Continue the reasoning."}
        ]

        answer = call_model(model, messages)

        # termination detection
        if "FINAL DESIGN COMPLETE" in answer.upper():

            return {
                "status": "done",
                "agent": agent_name,
                "text": answer,
                "round": self.turn
            }


        entry = {
            "agent": agent_name,
            "text": answer
        }

        self.responses.append(entry)

        # update global summary
        self.summary = update_summary(self.summary, answer)

        self.quotas[agent_name] -= 1
        self.last_spoke[agent_name] = self.turn

        self.maybe_raise_hand(agent_name)

        self.turn += 1

        return {
            "status": "ok",
            "agent": agent_name,
            "text": answer,
            "round": self.turn
        }


    # --------------------------------------------
    # HUMAN COMMANDS
    # --------------------------------------------

    def pause(self):
        self.paused = True


    def resume(self):
        self.paused = False


    def inject(self, msg):

        self.pending_human_instruction = msg

        self.responses.append({
            "agent": "Human",
            "text": msg
        })