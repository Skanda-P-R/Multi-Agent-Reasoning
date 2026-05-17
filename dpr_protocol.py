import re
import time
from copy import deepcopy
from collections import deque

import requests
from dpr_history import now_iso
from dpr_constants import (
    AVAILABLE_MODELS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    HUMAN_NAME,
    LOOP_WINDOW,
    MAX_REPEAT_STREAK,
    MIN_COMPLETION_ACCEPTED_TURNS,
    MIN_COMPLETION_AGENT_COVERAGE_RATIO,
    MIN_COMPLETION_MEMORY_ITEMS,
    MIN_COMPLETION_POINTERS_ADDRESSED,
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
        self.max_turns = MAX_TURNS
        self.created_at = now_iso()
        self.finished_at = None
        self.ended = False
        self.end_reason = None
        self.loaded_from_history_id = None
        self.awaiting_history_redirect = False

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
        self.broadcast_events = []
        self.history_events = []

        self.record_event("session_started", {
            "question": self.question,
            "agents": self.agents,
        })

    def _jsonable(self, value):
        if isinstance(value, deque):
            return [self._jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._jsonable(v) for v in value]
        return value

    def record_event(self, kind, payload=None):
        self.history_events.append({
            "timestamp": now_iso(),
            "turn": self.turn,
            "kind": kind,
            "payload": self._jsonable(payload or {}),
        })

    def _mark_ended(self, reason):
        self.ended = True
        self.end_reason = reason
        self.finished_at = self.finished_at or now_iso()

    def _runtime_state_snapshot(self):
        return {
            "turn": self.turn,
            "max_turns": self.max_turns,
            "quotas": dict(self.quotas),
            "last_spoke": dict(self.last_spoke),
            "hand_queue": list(self.hand_queue),
            "intent_queue": list(self.intent_queue),
            "human_hand_raised": self.human_hand_raised,
            "awaiting_human_turn": self.awaiting_human_turn,
            "awaiting_human_finalization": self.awaiting_human_finalization,
            "finalization_candidate": deepcopy(self.finalization_candidate),
            "current_index": self.current_index,
            "last_speaker": self.last_speaker,
            "repeat_streak": self.repeat_streak,
            "pending_human_instruction": self.pending_human_instruction,
            "pending_redirect": deepcopy(self.pending_redirect),
            "stopped_by_human": self.stopped_by_human,
            "paused": self.paused,
            "bootstrap_done": self.bootstrap_done,
            "pointer_history": list(self.pointer_history),
            "last_context_snapshot": self.last_context_snapshot,
            "agent_scores": dict(self.agent_scores),
            "hand_raise_scores": dict(self.hand_raise_scores),
            "last_selection_reason": self.last_selection_reason,
        }

    def to_history_document(self):
        return self._jsonable({
            "schema_version": 1,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "question": self.question,
            "agents": self.agents,
            "turn": self.turn,
            "max_turns": self.max_turns,
            "ended": self.ended,
            "end_reason": self.end_reason,
            "continuation_of": self.loaded_from_history_id,
            "responses": self.responses,
            "ignored_responses": self.ignored_responses,
            "facilitator_log": self.facilitator_log,
            "shared_memory": self.shared_memory,
            "broadcast_events": self.broadcast_events,
            "history_events": self.history_events,
            "runtime_state": self._runtime_state_snapshot(),
        })

    @classmethod
    def from_history_document(cls, document, history_id=None):
        agents = document.get("agents") or []
        selected = [
            {"model": a.get("model"), "section": a.get("section", DEFAULT_SECTION)}
            for a in agents
            if a.get("model")
        ]
        session = cls(document.get("question", ""), selected_models=selected or None)
        state = document.get("runtime_state") or {}

        session.created_at = document.get("created_at") or session.created_at
        session.finished_at = None
        session.ended = False
        session.end_reason = None
        session.loaded_from_history_id = history_id or document.get("id")
        session.awaiting_history_redirect = True

        session.responses = deepcopy(document.get("responses", []))
        session.ignored_responses = deepcopy(document.get("ignored_responses", []))
        session.facilitator_log = deepcopy(document.get("facilitator_log", []))
        memory = deepcopy(document.get("shared_memory", {}))
        for key in ["facts", "options", "decisions", "open_questions", "actions", "changelog"]:
            session.shared_memory[key] = list(memory.get(key, []))

        session.broadcast_events = deepcopy(document.get("broadcast_events", []))
        session.history_events = deepcopy(document.get("history_events", []))

        session.turn = int(state.get("turn", document.get("turn", session.turn)) or 0)
        session.max_turns = int(
            state.get("max_turns", document.get("max_turns", session.turn + MAX_TURNS))
            or (session.turn + MAX_TURNS)
        )
        session.quotas = {
            a["name"]: int((state.get("quotas") or {}).get(a["name"], STARTING_QUOTA) or 0)
            for a in session.agents
        }
        if all(q <= 0 for q in session.quotas.values()):
            session.quotas = {a["name"]: STARTING_QUOTA for a in session.agents}
        session.last_spoke = {
            a["name"]: int((state.get("last_spoke") or {}).get(a["name"], -1))
            for a in session.agents
        }
        session.hand_queue = deque()
        session.intent_queue = deque()
        session.human_hand_raised = False
        session.awaiting_human_turn = False
        session.awaiting_human_finalization = False
        session.finalization_candidate = None
        session.current_index = int(state.get("current_index", 0) or 0)
        session.last_speaker = state.get("last_speaker")
        session.repeat_streak = int(state.get("repeat_streak", 0) or 0)
        session.pending_human_instruction = None
        session.pending_redirect = None
        session.stopped_by_human = False
        session.paused = True
        session.bootstrap_done = bool(state.get("bootstrap_done", bool(session.responses)))
        session.pointer_history = list(state.get("pointer_history", []))
        session.last_context_snapshot = state.get("last_context_snapshot", "")
        session.agent_scores = dict(state.get("agent_scores", {}))
        session.hand_raise_scores = dict(state.get("hand_raise_scores", {}))
        session.last_selection_reason = "Loaded previous chat. Redirect required to continue."
        session.record_event("history_loaded", {
            "history_id": session.loaded_from_history_id,
            "source_saved_at": document.get("saved_at"),
        })
        return session

    def restore_payload(self):
        return {
            "question": self.question,
            "agents": list(self.agents),
            "responses": deepcopy(self.responses),
            "ignored_responses": deepcopy(self.ignored_responses),
            "facilitator_log": deepcopy(self.facilitator_log),
            "broadcast_events": deepcopy(self.broadcast_events),
            "history_events": deepcopy(self.history_events),
            "queued_interrupts": list(self.hand_queue),
            "turn": self.turn,
            "max_turns": self.max_turns,
            **self._state_payload(),
        }

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

        self.record_event("models_updated", {"agents": self.agents})
        return list(self.agents)

    def _agent_index(self, agent_name):
        return next(i for i, a in enumerate(self.agents) if a["name"] == agent_name)

    def _token_distance(self, agent_name):
        if agent_name == HUMAN_NAME:
            return -1
        idx = self._agent_index(agent_name)
        return (idx - self.current_index) % len(self.agents)

    def _completion_readiness(self, current_pointer=""):
        accepted_agent_turns = [
            r for r in self.responses
            if r.get("accepted") and r.get("agent") not in {HUMAN_NAME, "System"}
        ]
        agents_who_spoke = {r.get("agent") for r in accepted_agent_turns}
        agent_count = max(1, len(self.agents))
        coverage_ratio = len(agents_who_spoke) / agent_count
        memory_items = sum(
            len(self.shared_memory.get(key, []))
            for key in ["facts", "options", "decisions", "open_questions", "actions"]
        )
        pending_pointer_count = len([
            item for item in self.intent_queue
            if isinstance(item, dict) and item.get("agent") != HUMAN_NAME and item.get("pointer")
        ])

        blockers = []
        if len(accepted_agent_turns) < MIN_COMPLETION_ACCEPTED_TURNS:
            blockers.append(
                f"accepted agent turns {len(accepted_agent_turns)}/{MIN_COMPLETION_ACCEPTED_TURNS}"
            )
        if memory_items < MIN_COMPLETION_MEMORY_ITEMS:
            blockers.append(f"shared memory items {memory_items}/{MIN_COMPLETION_MEMORY_ITEMS}")
        if len(self.pointer_history) < MIN_COMPLETION_POINTERS_ADDRESSED:
            blockers.append(
                f"addressed pointers {len(self.pointer_history)}/{MIN_COMPLETION_POINTERS_ADDRESSED}"
            )
        if coverage_ratio < MIN_COMPLETION_AGENT_COVERAGE_RATIO:
            blockers.append(
                f"agent coverage {coverage_ratio:.0%}/{MIN_COMPLETION_AGENT_COVERAGE_RATIO:.0%}"
            )
        if current_pointer:
            blockers.append("current turn still has an active pointer")
        if pending_pointer_count:
            blockers.append(f"pending broadcast pointers {pending_pointer_count}")

        return {
            "ready": not blockers,
            "blockers": blockers,
            "accepted_agent_turns": len(accepted_agent_turns),
            "memory_items": memory_items,
            "pointers_addressed": len(self.pointer_history),
            "agent_coverage_ratio": coverage_ratio,
            "pending_pointer_count": pending_pointer_count,
        }

    def _strip_final_design_marker(self, text):
        return re.sub(r"\bFINAL\s+DESIGN\s+COMPLETE\b", "", text or "", flags=re.IGNORECASE).strip()

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
            self._mark_ended("stopped_by_human")
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

        if self.awaiting_history_redirect:
            return {
                "status": "awaiting_history_redirect",
                "agent": HUMAN_NAME,
                "agent_model": None,
                "text": "Loaded chat requires a human redirect before continuing.",
                "round": self.turn,
                **self._state_payload(),
            }

        if self.turn >= self.max_turns:
            self._mark_ended("max_turns_reached")
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
            self._mark_ended("all_agents_exhausted_quotas")
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
        completion_readiness = None
        if final_design_complete:
            if ignored_reason:
                answer = self._strip_final_design_marker(answer)
                final_design_complete = False
                self._push_facilitator_event(
                    "completion_deferred",
                    f"Completion marker ignored because the turn was rejected: {ignored_reason}.",
                )
            else:
                completion_readiness = self._completion_readiness(current_pointer=pointer_text)
            if final_design_complete and not completion_readiness["ready"]:
                stripped_answer = self._strip_final_design_marker(answer)
                if stripped_answer:
                    answer = stripped_answer
                else:
                    ignored_reason = ignored_reason or "premature_completion_marker"
                final_design_complete = False
                self._push_facilitator_event(
                    "completion_deferred",
                    "Premature completion marker deferred: "
                    + "; ".join(completion_readiness["blockers"]),
                )

        entry = {
            "agent": agent_name,
            "model": model,
            "text": answer,
            "accepted": ignored_reason is None,
            "round": self.turn,
            "selection_reason": self.last_selection_reason,
            "intent_pointer": pointer_text,
            "intent_priority": intent_priority,
            "intent_priority_rank": intent_priority_rank,
            "intent_confidence": intent_confidence,
            "intent_hand_raise": intent_hand_raise,
            "completion_readiness": completion_readiness,
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
            self.awaiting_human_finalization = True
            self.finalization_candidate = {
                "agent": agent_name,
                "model": model,
                "text": answer,
                "completion_readiness": completion_readiness,
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
        if self.awaiting_history_redirect:
            return
        self.paused = False

    def stop(self):
        self.stopped_by_human = True
        self.paused = False
        self.awaiting_human_finalization = False
        self.finalization_candidate = None
        self._mark_ended("stopped_by_human")

    def inject(self, msg):
        self.pending_human_instruction = msg

        entry = {
            "agent": HUMAN_NAME,
            "text": msg,
            "accepted": True,
            "round": self.turn,
            "kind": "inject",
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
            "round": self.turn,
            "kind": "human_turn",
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
            "round": self.turn,
            "kind": action,
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
            "accepted": True,
            "round": self.turn,
            "kind": "redirect",
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
        self._mark_ended("finalization_approved")

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
        self.ended = False
        self.end_reason = None
        self.finished_at = None

        if redirect_message:
            self.pending_redirect = {
                "message": redirect_message,
                "remaining": max(1, int(turns)),
            }
            self._push_facilitator_event("redirect", f"Redirect set: {redirect_message}")
            self.responses.append({
                "agent": HUMAN_NAME,
                "text": f"REDIRECT ({self.pending_redirect['remaining']} turns): {redirect_message}",
                "accepted": True,
                "round": self.turn,
                "kind": "finalize_redirect",
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

    def continue_from_history_redirect(self, redirect_message, turns=REDIRECT_DURATION_TURNS):
        if not self.awaiting_history_redirect:
            raise RuntimeError("No loaded history is awaiting redirect.")
        redirect_message = (redirect_message or "").strip()
        if not redirect_message:
            raise RuntimeError("redirect message is required")

        self.awaiting_history_redirect = False
        self.paused = False
        self.stopped_by_human = False
        self.ended = False
        self.end_reason = None
        self.finished_at = None
        self.pending_redirect = {
            "message": redirect_message,
            "remaining": max(1, int(turns)),
        }
        if self.turn >= self.max_turns:
            self.max_turns = self.turn + MAX_TURNS
        self._push_facilitator_event("history_redirect", f"Loaded chat redirected: {redirect_message}")

        turn_text = f"REDIRECT ({self.pending_redirect['remaining']} turns): {redirect_message}"
        self.responses.append({
            "agent": HUMAN_NAME,
            "text": turn_text,
            "accepted": True,
            "round": self.turn,
            "kind": "history_redirect",
        })
        self._update_shared_memory(HUMAN_NAME, turn_text)
        self._broadcast_for_queue()
        self.record_event("history_continue_redirect", {
            "message": redirect_message,
            "turns": self.pending_redirect["remaining"],
            "loaded_from_history_id": self.loaded_from_history_id,
        })

        return {
            "status": "ok",
            "agent": HUMAN_NAME,
            "agent_model": None,
            "text": turn_text,
            "round": self.turn,
            "ignored": False,
            "ignored_reason": None,
            "quota_left": None,
            "queued_interrupts": list(self.hand_queue),
            **self._state_payload(),
        }
