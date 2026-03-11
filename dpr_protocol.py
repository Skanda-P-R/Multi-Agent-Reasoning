import os
import re
import random
from collections import deque
from pathlib import Path

import requests
from dotenv import load_dotenv

API_URL = "https://api.groq.com/openai/v1/chat/completions"

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AGENTS = [
{"name": "Agent 1", "model": "llama-3.3-70b-versatile"},
{"name": "Agent 2", "model": "openai/gpt-oss-120b"},
{"name": "Agent 3", "model": "moonshotai/kimi-k2-instruct"},
{"name": "Agent 4", "model": "openai/gpt-oss-20b"},
]

MAX_TURNS = 60
STARTING_QUOTA = 8
SUMMARY_LIMIT_WORDS = 350
REDIRECT_DURATION_TURNS = 3
STARVATION_THRESHOLD = 5
LOOP_WINDOW = 4
MAX_REPEAT_STREAK = 2


def call_model(model, messages, max_tokens=500):

    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY environment variable.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.6
    }

    r = requests.post(API_URL, headers=headers, json=payload)
    r.raise_for_status()

    msg = r.json()["choices"][0]["message"]
    return msg.get("content", "")


class DPRSession:

    def __init__(self, question):

        self.question = question
        self.turn = 0

        self.responses = []
        self.summary = ""

        self.paused = False

        self.pending_human_instruction = None
        self.pending_redirect = None

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
        self.last_speaker = None
        self.repeat_streak = 0

        self.ignored_responses = []
        self.facilitator_log = []

    def _agent_index(self, agent_name):
        return next(i for i, a in enumerate(AGENTS) if a["name"] == agent_name)

    def _token_distance(self, agent_name):
        idx = self._agent_index(agent_name)
        return (idx - self.current_index) % len(AGENTS)

    def _push_facilitator_event(self, kind, message):
        self.facilitator_log.append({
            "turn": self.turn,
            "kind": kind,
            "message": message
        })

    def _normalize(self, text):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", text.lower())).strip()

    def _update_summary(self, answer):
        accepted = [r["text"] for r in self.responses if r.get("accepted")]
        tail = accepted[-3:]
        joined = "\n\n".join(tail)
        words = joined.split()
        self.summary = " ".join(words[:SUMMARY_LIMIT_WORDS])


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


        redirect_block = ""
        if self.pending_redirect and self.pending_redirect["remaining"] > 0:
            redirect_block = f"""
Facilitator redirection (high priority):
{self.pending_redirect['message']}

You MUST keep your response aligned to this redirection.
"""

        return f"""
You are {agent_name} in a distributed reasoning protocol.

Original problem:
{self.question}

Current design summary:
{self.summary}

{human_block}
{redirect_block}

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

    def enqueue_interrupts(self, speaker):
        for a in AGENTS:
            agent = a["name"]
            if agent == speaker or self.quotas[agent] <= 0:
                continue

            starved = (self.turn - self.last_spoke[agent]) >= STARVATION_THRESHOLD
            if starved or random.random() < 0.2:
                if agent not in self.hand_queue:
                    self.hand_queue.append(agent)


    # --------------------------------------------
    # PRIORITY ORDER
    # --------------------------------------------

    def select_next_agent(self):

        live_agents = [a["name"] for a in AGENTS if self.quotas[a["name"]] > 0]
        if not live_agents:
            return None

        starved_agents = [
            a for a in live_agents
            if (self.turn - self.last_spoke[a]) >= STARVATION_THRESHOLD
        ]
        if starved_agents:
            chosen = sorted(
                starved_agents,
                key=lambda a: (self.turn - self.last_spoke[a], -self._token_distance(a)),
                reverse=True
            )[0]
            self._push_facilitator_event(
                "anti_starvation",
                f"Prioritized {chosen} due to starvation protection."
            )
            self.current_index = (self._agent_index(chosen) + 1) % len(AGENTS)
            return chosen

        if self.hand_queue:
            candidates = [a for a in list(self.hand_queue) if self.quotas[a] > 0]
            if candidates:
                agent = sorted(candidates, key=lambda a: self._token_distance(a))[0]
                self.hand_queue = deque([a for a in self.hand_queue if a != agent])
                self.current_index = (self._agent_index(agent) + 1) % len(AGENTS)
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

        if self.pending_redirect and self.pending_redirect["remaining"] > 0:
            self.pending_redirect["remaining"] -= 1

        normalized = self._normalize(answer)
        recent = [self._normalize(r["text"]) for r in self.responses if r.get("accepted")][-LOOP_WINDOW:]
        ignored_reason = None
        if normalized and normalized in recent:
            ignored_reason = "loop_detected"
            self._push_facilitator_event(
                "loop_detection",
                f"Ignored {agent_name} response due to repeated reasoning loop."
            )

        alternatives = [a["name"] for a in AGENTS if a["name"] != agent_name and self.quotas[a["name"]] > 0]
        if self.last_speaker == agent_name and self.repeat_streak >= MAX_REPEAT_STREAK and alternatives:
            ignored_reason = ignored_reason or "fairness_repeat_limit"
            self._push_facilitator_event(
                "fairness",
                f"Ignored {agent_name} response to break repeated-turn streak."
            )

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
            "text": answer,
            "accepted": ignored_reason is None
        }

        self.responses.append(entry)

        if ignored_reason:
            ignored_entry = {
                "turn": self.turn,
                "agent": agent_name,
                "reason": ignored_reason,
                "text": answer
            }
            self.ignored_responses.append(ignored_entry)
        else:
            self._update_summary(answer)

        self.quotas[agent_name] -= 1
        self.last_spoke[agent_name] = self.turn

        if self.last_speaker == agent_name:
            self.repeat_streak += 1
        else:
            self.last_speaker = agent_name
            self.repeat_streak = 1

        self.maybe_raise_hand(agent_name)
        self.enqueue_interrupts(agent_name)

        self.turn += 1

        return {
            "status": "ok",
            "agent": agent_name,
            "text": answer,
            "round": self.turn,
            "ignored": bool(ignored_reason),
            "ignored_reason": ignored_reason,
            "quota_left": self.quotas[agent_name],
            "queued_interrupts": list(self.hand_queue)
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

        entry = {
            "agent": "Human",
            "text": msg
        }
        self.responses.append(entry)
        return entry

    def redirect(self, msg, turns=REDIRECT_DURATION_TURNS):
        self.pending_redirect = {
            "message": msg,
            "remaining": max(1, int(turns))
        }
        self._push_facilitator_event("redirect", f"Redirect set: {msg}")
        entry = {
            "agent": "Human",
            "text": f"REDIRECT ({self.pending_redirect['remaining']} turns): {msg}"
        }
        self.responses.append(entry)
        return entry