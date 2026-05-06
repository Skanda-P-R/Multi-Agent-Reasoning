import time
from collections import deque

import requests
from dpr_constants import (
    AVAILABLE_MODELS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    HUMAN_NAME,
    LOOP_WINDOW,
    MAX_REPEAT_STREAK,
    MAX_TURNS,
    REDIRECT_DURATION_TURNS,
    SECTION_HEADERS,
    STARTING_QUOTA,
    TURN_CALL_DELAY_SECONDS,
)
from dpr_intent_mixin import DPRIntentMixin
from dpr_memory_mixin import DPRMemoryMixin
from dpr_model_client import call_model
from dpr_selector import suggest_models_for_question


class DPRSession(DPRMemoryMixin, DPRIntentMixin):
    # --------------------------------------------
    # MODEL SELECTION / SESSION INITIALIZATION
    # --------------------------------------------
    @staticmethod
    def _normalize_selected_models(selected_models):
        selected_models = selected_models or list(DEFAULT_AGENT_MODELS)
        if len(selected_models) < 2:
            raise ValueError("Select at least 2 models to start a session.")

        normalized = []
        for item in selected_models:
            if isinstance(item, str):
                normalized.append({"model": item, "section": DEFAULT_SECTION})
            elif isinstance(item, dict):
                model = str(item.get("model", "")).strip()
                section = str(item.get("section", DEFAULT_SECTION)).strip().lower() or DEFAULT_SECTION
                if section not in SECTION_HEADERS:
                    section = DEFAULT_SECTION
                normalized.append({"model": model, "section": section})
            else:
                raise ValueError("Invalid model selection payload.")

        invalid = [m["model"] for m in normalized if m["model"] not in AVAILABLE_MODELS]
        if invalid:
            raise ValueError(f"Unsupported model(s): {', '.join(invalid)}")

        return normalized

    def __init__(self, question, selected_models=None):
        normalized = self._normalize_selected_models(selected_models)

        self.agents = [
            {"name": f"Agent {idx + 1}", "model": item["model"], "section": item["section"]}
            for idx, item in enumerate(normalized)
        ]

        self.question = question
        self.turn = 0

        self.responses = []
        self.stopped_by_human = False

        self.paused = False

        self.pending_human_instruction = None
        self.pending_redirect = None

        self.quotas = {a["name"]: STARTING_QUOTA for a in self.agents}

        self.last_spoke = {a["name"]: -1 for a in self.agents}

        self.hand_queue = deque()
        self.intent_queue = deque()
        self.human_hand_raised = False
        self.awaiting_human_turn = False
        self.awaiting_human_finalization = False
        self.finalization_candidate = None
        self.current_index = 0
        self.last_speaker = None
        self.repeat_streak = 0

        self.ignored_responses = []
        self.facilitator_log = []

        self.shared_memory = {
            "facts": [],
            "options": [],
            "decisions": [],
            "open_questions": [],
            "actions": [],
            "changelog": [],
        }
        self.last_context_snapshot = ""

        self.agent_scores = {}
        self.hand_raise_scores = {}
        self.last_selection_reason = ""
        self.bootstrap_done = False
        self.pointer_history = []  # recent selected pointers

    def update_agents(self, selected_models):
        if not self.paused:
            raise RuntimeError("Pause the session before updating models.")

        normalized = self._normalize_selected_models(selected_models)

        old_quota_by_pair = {
            (a["model"], a.get("section", DEFAULT_SECTION)): self.quotas.get(a["name"], STARTING_QUOTA)
            for a in self.agents
        }
        old_last_spoke_by_pair = {
            (a["model"], a.get("section", DEFAULT_SECTION)): self.last_spoke.get(a["name"], -1)
            for a in self.agents
        }

        self.agents = [
            {"name": f"Agent {idx + 1}", "model": item["model"], "section": item["section"]}
            for idx, item in enumerate(normalized)
        ]
        self.quotas = {
            a["name"]: old_quota_by_pair.get((a["model"], a["section"]), STARTING_QUOTA)
            for a in self.agents
        }
        self.last_spoke = {
            a["name"]: old_last_spoke_by_pair.get((a["model"], a["section"]), -1)
            for a in self.agents
        }

        valid_names = {a["name"] for a in self.agents}
        self.hand_queue = deque([x for x in self.hand_queue if x == HUMAN_NAME or x in valid_names])
        self.intent_queue = deque([x for x in self.intent_queue if x.get("agent") == HUMAN_NAME or x.get("agent") in valid_names])
        self.current_index = 0 if not self.agents else (self.current_index % len(self.agents))
        if self.last_speaker not in valid_names and self.last_speaker != HUMAN_NAME:
            self.last_speaker = None
            self.repeat_streak = 0

        return list(self.agents)

    def _agent_index(self, agent_name):
        return next(i for i, a in enumerate(self.agents) if a["name"] == agent_name)

    def _token_distance(self, agent_name):
        if agent_name == HUMAN_NAME:
            return -1
        idx = self._agent_index(agent_name)
        return (idx - self.current_index) % len(self.agents)

    def _push_facilitator_event(self, kind, message):
        self.facilitator_log.append({
            "turn": self.turn,
            "kind": kind,
            "message": message,
        })

    # --------------------------------------------
    # MAIN TURN EXECUTION LOOP
    # --------------------------------------------
    def step(self):
        if self.stopped_by_human:
            return {
                "status": "done",
                "agent": HUMAN_NAME,
                "agent_model": None,
                "text": "Reasoning stopped by human.",
                "round": self.turn,
                **self._state_payload(),
            }

        if self.paused:
            return {"status": "paused", **self._state_payload()}

        if self.awaiting_human_finalization:
            return {
                "status": "awaiting_human_finalization",
                "agent": HUMAN_NAME,
                "agent_model": None,
                "text": "Completion candidate raised. Human approval required.",
                "round": self.turn,
                "finalization_candidate": self.finalization_candidate,
                **self._state_payload(),
            }

        if self.turn >= MAX_TURNS:
            return {
                "status": "done",
                "agent": "System",
                "agent_model": None,
                "text": "Session ended: max turns reached.",
                "round": self.turn,
                **self._state_payload(),
            }

        selected = None
        if not self.bootstrap_done:
            selected = self._pick_bootstrap_agent()
        else:
            selected = self._next_from_queue_or_broadcast()

        if not selected:
            return {
                "status": "done",
                "agent": "System",
                "agent_model": None,
                "text": "All agents exhausted quotas.",
                "round": self.turn,
                **self._state_payload(),
            }
        agent_name = selected["agent"]
        pointer_text = selected.get("pointer", "")
        intent_priority = selected.get("priority_label", "bootstrap")
        intent_priority_rank = selected.get("priority_rank", None)
        intent_confidence = selected.get("confidence", None)
        intent_hand_raise = selected.get("hand_raise", None)

        self.hand_queue = deque([a for a in self.hand_queue if a != agent_name])

        if agent_name == HUMAN_NAME:
            self.awaiting_human_turn = True
            return {
                "status": "awaiting_human_turn",
                "agent": HUMAN_NAME,
                "agent_model": None,
                "text": "Human turn selected. Please submit your reasoning.",
                "round": self.turn,
                "queued_interrupts": list(self.hand_queue),
                **self._state_payload(),
            }

        model = next(a["model"] for a in self.agents if a["name"] == agent_name)

        context = self.build_context(agent_name, pointer_text=pointer_text, consume_human_instruction=False)

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": "Continue the reasoning."},
        ]

        try:
            time.sleep(TURN_CALL_DELAY_SECONDS)
            answer = call_model(model, messages, max_tokens=None)
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            reason = f"model_http_error_{status_code}"
            self._push_facilitator_event(
                "model_error",
                f"Skipped {agent_name} due to model HTTP error {status_code}.",
            )
            self.ignored_responses.append({
                "turn": self.turn,
                "agent": agent_name,
                "reason": reason,
                "text": str(e),
            })

            # Consume this agent's turn and move on, instead of failing the whole session.
            self.quotas[agent_name] -= 1
            self.turn += 1

            return {
                "status": "ok",
                "agent": agent_name,
                "agent_model": model,
                "text": f"Skipped due to model API error ({status_code}).",
                "round": self.turn,
                "ignored": True,
                "ignored_reason": reason,
                "quota_left": self.quotas[agent_name],
                "queued_interrupts": list(self.hand_queue),
                "intent_pointer": pointer_text,
                "intent_priority": intent_priority,
                "intent_priority_rank": intent_priority_rank,
                "intent_confidence": intent_confidence,
                "intent_hand_raise": intent_hand_raise,
                **self._state_payload(),
            }
        except Exception as e:
            reason = "model_runtime_error"
            self._push_facilitator_event(
                "model_error",
                f"Skipped {agent_name} due to runtime model error: {str(e)}",
            )
            self.ignored_responses.append({
                "turn": self.turn,
                "agent": agent_name,
                "reason": reason,
                "text": str(e),
            })

            # Consume this agent's turn and move on, instead of failing the whole session.
            self.quotas[agent_name] -= 1
            self.turn += 1

            return {
                "status": "ok",
                "agent": agent_name,
                "agent_model": model,
                "text": "Skipped due to model runtime error.",
                "round": self.turn,
                "ignored": True,
                "ignored_reason": reason,
                "quota_left": self.quotas[agent_name],
                "queued_interrupts": list(self.hand_queue),
                "intent_pointer": pointer_text,
                "intent_priority": intent_priority,
                "intent_priority_rank": intent_priority_rank,
                "intent_confidence": intent_confidence,
                "intent_hand_raise": intent_hand_raise,
                **self._state_payload(),
            }

        if self.stopped_by_human:
            return {
                "status": "done",
                "agent": HUMAN_NAME,
                "agent_model": None,
                "text": "Reasoning stopped by human.",
                "round": self.turn,
                **self._state_payload(),
            }

        if self.paused:
            return {
                "status": "paused",
                **self._state_payload(),
            }

        if self.awaiting_human_finalization:
            return {
                "status": "awaiting_human_finalization",
                "agent": HUMAN_NAME,
                "agent_model": None,
                "text": "Completion candidate raised. Human approval required.",
                "round": self.turn,
                "finalization_candidate": self.finalization_candidate,
                **self._state_payload(),
            }

        if self.pending_redirect and self.pending_redirect["remaining"] > 0:
            self.pending_redirect["remaining"] -= 1

        normalized = self._normalize(answer)
        recent = [self._normalize(r["text"]) for r in self.responses if r.get("accepted")][
            -LOOP_WINDOW:
        ]
        ignored_reason = None
        if normalized and normalized in recent:
            ignored_reason = "loop_detected"
            self._push_facilitator_event(
                "loop_detection",
                f"Ignored {agent_name} response due to repeated reasoning loop.",
            )

        alternatives = [
            a["name"]
            for a in self.agents
            if a["name"] != agent_name and self.quotas[a["name"]] > 0
        ]
        if self.last_speaker == agent_name and self.repeat_streak >= MAX_REPEAT_STREAK and alternatives:
            ignored_reason = ignored_reason or "fairness_repeat_limit"
            self._push_facilitator_event(
                "fairness", f"Ignored {agent_name} response to break repeated-turn streak."
            )

        final_design_complete = "FINAL DESIGN COMPLETE" in answer.upper()

        entry = {
            "agent": agent_name,
            "text": answer,
            "accepted": ignored_reason is None,
        }

        self.responses.append(entry)
        
        if ignored_reason:
            ignored_entry = {
                "turn": self.turn,
                "agent": agent_name,
                "reason": ignored_reason,
                "text": answer,
            }
            self.ignored_responses.append(ignored_entry)
        else:
            self._update_shared_memory(agent_name, answer)
            if pointer_text:
                self.pointer_history.append(pointer_text)
                self.pointer_history = self.pointer_history[-8:]
            if not self.bootstrap_done:
                self.bootstrap_done = True

        if final_design_complete:
            # Per DPR principles: Only allow completion when ALL agents have participated
            agents_who_spoke = set(self.last_spoke.keys()) - {0} if 0 in self.last_spoke else set(self.last_spoke.keys())
            agents_not_spoken = [a["name"] for a in self.agents if self.last_spoke.get(a["name"], 0) == 0]
            
            if agents_not_spoken:
                # Don't complete yet - other agents need to contribute
                self._push_facilitator_event(
                    "completion_deferred",
                    f"Completion proposed but deferred: Agents {', '.join(agents_not_spoken)} haven't participated yet. Continuing for iterative refinement as per DPR protocol."
                )
            else:
                # All agents have participated - allow completion
                self.awaiting_human_finalization = True
                self.finalization_candidate = {
                    "agent": agent_name,
                    "model": model,
                    "text": answer,
                }
                return {
                    "status": "awaiting_human_finalization",
                    "agent": HUMAN_NAME,
                    "agent_model": None,
                    "text": "Completion candidate raised. Human approval required.",
                    "round": self.turn,
                    "finalization_candidate": self.finalization_candidate,
                    "ignored": bool(ignored_reason),
                    "ignored_reason": ignored_reason,
                    "quota_left": self.quotas.get(agent_name),
                    "queued_interrupts": list(self.hand_queue),
                    "intent_pointer": pointer_text,
                    "intent_priority": intent_priority,
                    "intent_priority_rank": intent_priority_rank,
                    "intent_confidence": intent_confidence,
                    "intent_hand_raise": intent_hand_raise,
                    **self._state_payload(),
                }

        self.quotas[agent_name] -= 1
        self.last_spoke[agent_name] = self.turn

        if self.last_speaker == agent_name:
            self.repeat_streak += 1
        else:
            self.last_speaker = agent_name
            self.repeat_streak = 1

        self.turn += 1

        return {
            "status": "ok",
            "agent": agent_name,
            "agent_model": model,
            "text": answer,
            "round": self.turn,
            "ignored": bool(ignored_reason),
            "ignored_reason": ignored_reason,
            "quota_left": self.quotas[agent_name],
            "queued_interrupts": list(self.hand_queue),
            "intent_pointer": pointer_text,
            "intent_priority": intent_priority,
            "intent_priority_rank": intent_priority_rank,
            "intent_confidence": intent_confidence,
            "intent_hand_raise": intent_hand_raise,
            **self._state_payload(),
        }

    # --------------------------------------------
    # HUMAN COMMANDS / CONTROLS
    # --------------------------------------------

    def pause(self):
        self.paused = True

    def resume(self):
        if self.stopped_by_human:
            return
        self.paused = False

    def stop(self):
        self.stopped_by_human = True
        self.paused = False
        self.awaiting_human_finalization = False
        self.finalization_candidate = None

    def inject(self, msg):
        self.pending_human_instruction = msg

        entry = {
            "agent": HUMAN_NAME,
            "text": msg,
        }
        self.responses.append(entry)
        self._broadcast_for_queue()
        return entry

    def raise_human_hand(self):
        if HUMAN_NAME not in self.hand_queue:
            self.hand_queue.append(HUMAN_NAME)
            self.intent_queue.append({"agent": HUMAN_NAME, "pointer": "", "priority_rank": 99, "priority_label": "human", "confidence": 1.0, "hand_raise": True})
        self.human_hand_raised = True
        self._push_facilitator_event("human_hand_raise", "Human raised hand for turn.")
        return {
            "agent": HUMAN_NAME,
            "text": "Hand raised for a protocol turn.",
        }

    def submit_human_turn(self, msg):
        if not self.awaiting_human_turn:
            raise RuntimeError("Human turn is not active right now.")

        entry = {
            "agent": HUMAN_NAME,
            "text": msg,
            "accepted": True,
        }
        self.responses.append(entry)
        self._update_shared_memory(HUMAN_NAME, msg)

        self.last_speaker = HUMAN_NAME
        self.repeat_streak = 1

        self.awaiting_human_turn = False
        self.turn += 1

        return {
            "status": "ok",
            "agent": HUMAN_NAME,
            "text": msg,
            "round": self.turn,
            "ignored": False,
            "ignored_reason": None,
            "quota_left": None,
            "queued_interrupts": list(self.hand_queue),
            **self._state_payload(),
        }

    def submit_human_turn_action(self, action, msg, turns=REDIRECT_DURATION_TURNS):
        if not self.awaiting_human_turn:
            raise RuntimeError("Human turn is not active right now.")

        if action == "inject":
            self.pending_human_instruction = msg
            turn_text = f"INJECT: {msg}"
        elif action == "redirect":
            self.pending_redirect = {
                "message": msg,
                "remaining": max(1, int(turns)),
            }
            self._push_facilitator_event("redirect", f"Redirect set: {msg}")
            turn_text = f"REDIRECT ({self.pending_redirect['remaining']} turns): {msg}"
        else:
            raise RuntimeError("Invalid human turn action.")

        entry = {
            "agent": HUMAN_NAME,
            "text": turn_text,
            "accepted": True,
        }
        self.responses.append(entry)
        self._update_shared_memory(HUMAN_NAME, turn_text)

        self.last_speaker = HUMAN_NAME
        self.repeat_streak = 1

        self.awaiting_human_turn = False
        self.turn += 1

        if action in ("inject", "redirect"):
            self._broadcast_for_queue()

        return {
            "status": "ok",
            "agent": HUMAN_NAME,
            "text": turn_text,
            "round": self.turn,
            "ignored": False,
            "ignored_reason": None,
            "quota_left": None,
            "queued_interrupts": list(self.hand_queue),
            **self._state_payload(),
        }

    def redirect(self, msg, turns=REDIRECT_DURATION_TURNS):
        self.pending_redirect = {
            "message": msg,
            "remaining": max(1, int(turns)),
        }
        self._push_facilitator_event("redirect", f"Redirect set: {msg}")
        entry = {
            "agent": HUMAN_NAME,
            "text": f"REDIRECT ({self.pending_redirect['remaining']} turns): {msg}",
        }
        self.responses.append(entry)
        self._broadcast_for_queue()
        return entry

    def approve_finalization(self):
        if not self.awaiting_human_finalization:
            raise RuntimeError("No completion candidate awaiting approval.")

        candidate = self.finalization_candidate or {"agent": "System", "text": "Session finalized by human."}
        self.awaiting_human_finalization = False
        self.finalization_candidate = None
        self.paused = False

        return {
            "status": "done",
            "agent": candidate["agent"],
            "agent_model": candidate.get("model"),
            "text": candidate["text"],
            "round": self.turn,
            **self._state_payload(),
        }

    def continue_after_finalization(self, redirect_message=None, turns=REDIRECT_DURATION_TURNS):
        if not self.awaiting_human_finalization:
            raise RuntimeError("No completion candidate awaiting approval.")

        self.awaiting_human_finalization = False
        self.finalization_candidate = None

        if redirect_message:
            self.pending_redirect = {
                "message": redirect_message,
                "remaining": max(1, int(turns)),
            }
            self._push_facilitator_event("redirect", f"Redirect set: {redirect_message}")
            self.responses.append({
                "agent": HUMAN_NAME,
                "text": f"REDIRECT ({self.pending_redirect['remaining']} turns): {redirect_message}",
            })

        self.paused = False
        return {
            "status": "ok",
            "agent": HUMAN_NAME,
            "agent_model": None,
            "text": "Human requested continuation.",
            "round": self.turn,
            **self._state_payload(),
        }
