import json
import random
import re
import time
from difflib import SequenceMatcher
from collections import deque

import requests
from dpr_constants import (
    AVAILABLE_MODELS,
    CONFIDENCE_WEIGHT,
    DECISION_START_VERBS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    FAIRNESS_WEIGHT,
    HAND_RAISE_THRESHOLD,
    HUMAN_NAME,
    LOOP_WINDOW,
    MAX_REPEAT_STREAK,
    MAX_SUMMARY_TEXT_CHARS,
    MAX_TURNS,
    MAX_TURNS,
    MEMORY_CALL_DELAY_SECONDS,
    MEMORY_CHANGELOG_LIMIT,
    MEMORY_ITEM_CHAR_LIMIT,
    MEMORY_MODEL,
    MEMORY_SECTION_LIMIT,
    MEMORY_SIMILARITY_THRESHOLD,
    MIN_PERSISTENT_OPEN_QUESTIONS,
    NOVELTY_SIMILARITY_THRESHOLD,
    NOVELTY_WEIGHT,
    RECENT_TURNS_IN_CONTEXT,
    REDIRECT_DURATION_TURNS,
    RELEVANCE_WEIGHT,
    SECTION_HEADERS,
    SECTION_PRIORITY_HINTS,
    STARVATION_COOLDOWN_TURNS,
    SIMILARITY_CHECK_WINDOW,
    STARVATION_THRESHOLD,
    STARTING_QUOTA,
)
from dpr_model_client import call_model
from dpr_selector import suggest_models_for_question


class DPRSession:
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

        # Hand-raise protocol with scoring
        self.agent_scores = {a["name"]: 0.5 for a in self.agents}
        self.agent_success_rates = {a["name"]: 0.5 for a in self.agents}
        self.score_history = []
        self.hand_raise_scores = {}  # {agent_name: {relevance, novelty, confidence, fairness, final}}
        self.last_selection_reason = ""  # Tracks HOW the last agent was selected

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

        # Preserve scores for existing agents, initialize for new ones
        self.agent_scores = {a["name"]: self.agent_scores.get(a["name"], 0.5) for a in self.agents}
        self.agent_success_rates = {a["name"]: self.agent_success_rates.get(a["name"], 0.5) for a in self.agents}

        valid_names = {a["name"] for a in self.agents}
        self.hand_queue = deque([x for x in self.hand_queue if x == HUMAN_NAME or x in valid_names])
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

    def _normalize(self, text):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", text.lower())).strip()

    def _short_text(self, text, max_chars=MAX_SUMMARY_TEXT_CHARS):
        compact = re.sub(r"\s+", " ", (text or "").strip())
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 3] + "..."

    def _clean_memory_item(self, text):
        raw = (text or "").strip()
        item = raw
        if not item:
            return ""

        # Drop obvious markdown table/header separator rows early.
        if raw.count("|") >= 2:
            table_like = re.sub(r"[|\-\s:]+", "", raw)
            if not table_like:
                return ""

        item = re.sub(r"`+", "", item)
        item = re.sub(r"[*_>#]+", "", item)
        item = re.sub(r"^\d+[.)]\s*", "", item)
        item = re.sub(r"^[-*]\s*", "", item)
        item = item.replace("|", " ")
        item = re.sub(r"\s+", " ", item).strip(" -:;")
        item = re.sub(
            r"^(facts?/assumptions?|decisions?|open questions?|next steps?)\s*:?\s*",
            "",
            item,
            flags=re.IGNORECASE,
        )
        item = re.sub(r"\s{2,}", " ", item).strip()
        item = self._short_text(item, MEMORY_ITEM_CHAR_LIMIT)
        return item

    def _is_noise_item(self, item):
        if not item:
            return True
        lowered = item.lower()
        if lowered in {"none", "(none)", "n/a", "na"}:
            return True
        if len(item) < 8:
            return True
        if set(item) <= {"-", ".", " "}:
            return True
        lowered = item.lower()
        header_fragments = {
            "criterion candidate region rationale",
            "sub-system design concept key advantages implementation notes",
            "aspect design decision",
            "section key innovation rationale",
            "body composition decision frequency scope oversight",
        }
        if lowered in header_fragments:
            return True
        if re.fullmatch(r"[-: ]{3,}", item):
            return True
        return False

    def _best_clean_line(self, text):
        for line in (text or "").splitlines():
            cleaned = self._clean_memory_item(line)
            if cleaned and not self._is_noise_item(cleaned):
                return cleaned
        cleaned = self._clean_memory_item(self._short_text(text, 180))
        return "" if self._is_noise_item(cleaned) else cleaned

    def _similarity(self, a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _dedupe_items(self, items):
        deduped = []
        for item in items:
            if self._is_noise_item(item):
                continue
            if any(self._similarity(item, existing) >= MEMORY_SIMILARITY_THRESHOLD for existing in deduped):
                continue
            deduped.append(item)
        return deduped

    def _section_score(self, section_key, item):
        score = 0
        lowered = item.lower()
        hints = SECTION_PRIORITY_HINTS.get(section_key, [])
        for hint in hints:
            if hint in lowered:
                score += 3
        if ":" in item:
            score += 1
        if 35 <= len(item) <= 130:
            score += 1
        return score

    def _prioritize_section_items(self, section_key, items, limit):
        scored = [(self._section_score(section_key, item), len(item), item) for item in items]
        scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        return [item for _, _, item in scored[:limit]]

    def _sentence_candidates(self, text):
        lines = []
        for raw in (text or "").splitlines():
            cleaned = self._clean_memory_item(raw)
            if cleaned and not self._is_noise_item(cleaned):
                lines.append(cleaned)
        return lines

    def _infer_implicit_decisions(self, turn_text, delta):
        inferred = []
        for line in self._sentence_candidates(turn_text):
            lowered = line.lower()
            tokens = lowered.split()
            if not tokens:
                continue
            first = tokens[0]
            if first in DECISION_START_VERBS and len(tokens) > 3:
                inferred.append(f"Decision: {line[0].upper() + line[1:]}")
                continue
            if any(
                kw in lowered
                for kw in ["will ", "must ", "is selected", "is chosen", "is locked", "we will", "we choose"]
            ):
                inferred.append(f"Decision: {line[0].upper() + line[1:]}")

        combined = self._dedupe_items((delta.get("decisions", []) or []) + inferred)
        delta["decisions"] = combined[:4]

    def _infer_open_questions(self, turn_text, delta):
        inferred = []
        for line in self._sentence_candidates(turn_text):
            lowered = line.lower()
            if "?" in line:
                inferred.append(line if line.endswith("?") else f"{line}?")
                continue
            if any(
                kw in lowered
                for kw in ["tbd", "unknown", "unclear", "pending", "risk", "tradeoff", "unresolved"]
            ):
                inferred.append(f"What is the resolution for: {line}?")

        if not inferred:
            option_candidates = delta.get("options", []) or []
            action_candidates = delta.get("actions", []) or []
            for seed in (option_candidates + action_candidates)[:2]:
                lowered = seed.lower()
                if any(x in lowered for x in ["deploy", "implement", "design", "build", "use"]):
                    inferred.append(f"What are the final operating thresholds and fallback plan for: {seed}?")

        combined = self._dedupe_items((delta.get("open_questions", []) or []) + inferred)
        delta["open_questions"] = combined[:4]

    def _question_resolved(self, question, decisions):
        q_tokens = {t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3}
        if not q_tokens:
            return False
        for d in decisions:
            dl = d.lower()
            if "resolved" in dl or "decision:" in dl or "selected" in dl or "locked" in dl:
                d_tokens = {t for t in re.findall(r"[a-z0-9]+", dl) if len(t) > 3}
                if len(q_tokens & d_tokens) >= 2:
                    return True
        return False

    def _ensure_open_questions_continuity(self, open_questions, decisions):
        existing = self.shared_memory.get("open_questions", [])
        carry = []
        for q in existing:
            if self._question_resolved(q, decisions):
                continue
            carry.append(q)
        merged = self._dedupe_items(open_questions + carry)
        if len(merged) < MIN_PERSISTENT_OPEN_QUESTIONS:
            merged = self._dedupe_items(carry + merged)
        return merged

    def _extract_json_object(self, raw_text):
        cleaned = (raw_text or "").strip()

        # Fast path: exact JSON object response.
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()

        # Common pattern: JSON code block embedded in text.
        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
        if code_match:
            try:
                parsed = json.loads(code_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        candidate = cleaned[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    def _fallback_memory_delta_from_text(self, turn_text):
        text = (turn_text or "").strip()
        if not text:
            return {
                "facts": [],
                "options": [],
                "decisions": [],
                "open_questions": [],
                "actions": [],
            }

        raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        cleaned_lines = []
        for line in raw_lines:
            line = self._clean_memory_item(line)
            if line and set(line) != {"-"}:
                cleaned_lines.append(line)

        lower_text = text.lower()
        facts = []
        options = []
        decisions = []
        open_questions = []
        actions = []

        for line in cleaned_lines:
            l = line.lower()
            if "fact" in l or "assumption" in l:
                facts.append(line)
            elif "decision" in l or "chosen" in l or "finalized" in l or "selected" in l:
                decisions.append(line)
            elif "?" in line or "open question" in l or "unresolved" in l:
                open_questions.append(line)
            elif "next step" in l or l.startswith("action") or "must " in l or "should " in l:
                actions.append(line)
            elif len(line) > 25:
                options.append(line)

        if not facts and ("fact" in lower_text or "assumption" in lower_text):
            facts.append(self._clean_memory_item(self._short_text(text, 180)))

        if not decisions and ("decision" in lower_text or "we choose" in lower_text):
            decisions.append(self._clean_memory_item(self._short_text(text, 180)))

        if not open_questions and ("?" in text or "open question" in lower_text):
            open_questions.append(self._clean_memory_item(self._short_text(text, 180)))

        if not actions and ("next step" in lower_text or "action" in lower_text):
            actions.append(self._clean_memory_item(self._short_text(text, 180)))

        if not options:
            options.append(self._clean_memory_item(self._short_text(text, 180)))

        return {
            "facts": self._dedupe_items(facts)[:4],
            "options": self._dedupe_items(options)[:4],
            "decisions": self._dedupe_items(decisions)[:4],
            "open_questions": self._dedupe_items(open_questions)[:4],
            "actions": self._dedupe_items(actions)[:4],
        }

    def _memory_snapshot_text(self):
        lines = []
        section_titles = [
            ("facts", "Facts / Assumptions"),
            ("options", "Options / Proposals"),
            ("decisions", "Decisions"),
            ("open_questions", "Open Questions"),
            ("actions", "Action Items"),
        ]

        for key, title in section_titles:
            values = self.shared_memory.get(key, [])
            lines.append(f"{title}:")
            if values:
                for v in values:
                    lines.append(f"- {v}")
            else:
                lines.append("- (none)")
            lines.append("")

        return "\n".join(lines).strip()

    def _sanitize_memory_delta(self, obj):
        if not isinstance(obj, dict):
            return None

        keys = ["facts", "options", "decisions", "open_questions", "actions"]
        delta = {}

        for key in keys:
            val = obj.get(key, [])
            if not isinstance(val, list):
                val = []

            cleaned = []
            for item in val:
                if not isinstance(item, str):
                    continue
                text = self._clean_memory_item(item)
                if text:
                    cleaned.append(text)
            delta[key] = self._dedupe_items(cleaned)[:4]

        return delta

    def _merge_memory_delta(self, delta, speaker, turn_text):
        if not delta:
            fallback_item = self._best_clean_line(turn_text)
            delta = {
                "facts": [],
                "options": [fallback_item] if fallback_item else [],
                "decisions": [],
                "open_questions": [],
                "actions": [],
            }

        self._infer_implicit_decisions(turn_text, delta)
        self._infer_open_questions(turn_text, delta)
        delta["open_questions"] = self._ensure_open_questions_continuity(
            delta.get("open_questions", []),
            delta.get("decisions", []),
        )

        for key in ["facts", "options", "decisions", "open_questions", "actions"]:
            existing = [self._clean_memory_item(x) for x in self.shared_memory[key]]
            incoming = [self._clean_memory_item(x) for x in delta.get(key, [])]
            merged = self._dedupe_items(existing + incoming)
            self.shared_memory[key] = self._prioritize_section_items(
                key, merged, MEMORY_SECTION_LIMIT
            )

        change_core = self._best_clean_line(turn_text)
        if not change_core:
            change_core = self._short_text(turn_text, 140)
        change = f"Turn {self.turn} {speaker}: {change_core}"
        self.shared_memory["changelog"].append(change)
        if len(self.shared_memory["changelog"]) > MEMORY_CHANGELOG_LIMIT:
            self.shared_memory["changelog"] = self.shared_memory["changelog"][-MEMORY_CHANGELOG_LIMIT:]

    def _update_shared_memory(self, speaker, turn_text):
        memory_json = json.dumps(self.shared_memory, ensure_ascii=True)

        prompt = f"""
You are a protocol memory updater.

Given the current memory and one new protocol turn, output ONLY valid JSON with keys:
- facts
- options
- decisions
- open_questions
- actions

Rules:
- Each key must be a JSON array of short bullet strings.
- Keep only new or updated information from the latest turn.
- Do not repeat old memory content.
- If no update for a key, return [].
- No markdown. No extra text.

Current memory JSON:
{memory_json}

Latest turn speaker: {speaker}
Latest turn text:
{turn_text}
"""

        messages = [
            {"role": "system", "content": "You produce strict JSON only."},
            {"role": "user", "content": prompt},
        ]

        delta = None
        last_error = None
        try:
            for _ in range(2):
                time.sleep(MEMORY_CALL_DELAY_SECONDS)
                raw = call_model(MEMORY_MODEL, messages, max_tokens=500, temperature=0.1)
                parsed = self._extract_json_object(raw)
                delta = self._sanitize_memory_delta(parsed)
                if delta is not None:
                    break
                # Retry with stricter ask when first parse fails.
                messages = [
                    {"role": "system", "content": "Return only minified JSON object. No markdown, no explanation."},
                    {"role": "user", "content": prompt},
                ]
        except Exception as e:
            last_error = str(e)

        if delta is None:
            if last_error:
                self._push_facilitator_event("memory_warning", f"Memory summarizer fallback: {last_error}")
            else:
                self._push_facilitator_event("memory_warning", "Memory summarizer returned non-JSON; using heuristic fallback.")
            delta = self._fallback_memory_delta_from_text(turn_text)

        self._merge_memory_delta(delta, speaker, turn_text)

    def _state_payload(self):
        return {
            "memory": {
                "facts": list(self.shared_memory["facts"]),
                "options": list(self.shared_memory["options"]),
                "decisions": list(self.shared_memory["decisions"]),
                "open_questions": list(self.shared_memory["open_questions"]),
                "actions": list(self.shared_memory["actions"]),
                "changelog": list(self.shared_memory["changelog"]),
            },
            "context_preview": self.last_context_snapshot,
            "agent_scores": dict(self.agent_scores),
            "hand_raise_scores": dict(self.hand_raise_scores),
            "selection_reason": self.last_selection_reason,
        }

    # --------------------------------------------
    # CONTEXT
    # --------------------------------------------

    def build_context(self, agent_name):
        agent_section = next(
            (a.get("section", DEFAULT_SECTION) for a in self.agents if a["name"] == agent_name),
            DEFAULT_SECTION,
        )
        section_header = SECTION_HEADERS.get(agent_section, SECTION_HEADERS[DEFAULT_SECTION])

        memory_block = self._memory_snapshot_text()

        accepted = [r for r in self.responses if r.get("accepted")]
        recent_turns = accepted[-RECENT_TURNS_IN_CONTEXT:]
        recent_block = "\n\n".join(
            f"{r['agent']}: {self._short_text(r['text'], 500)}" for r in recent_turns
        )
        if not recent_block:
            recent_block = "(none yet)"

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

        context = f"""
You are {agent_name} in a distributed reasoning protocol.
{section_header}

Original problem:
{self.question}

Shared memory state:
{memory_block}

Recent accepted turns:
{recent_block}

{human_block}
{redirect_block}

Instructions:
- Continue the design logically.
- Do NOT repeat earlier reasoning.
- Add new improvements or extensions.
- Use shared memory as background context, but do NOT mirror or restate section headings like
  "Facts / Assumptions", "Options / Proposals", "Decisions", "Open Questions", or "Action Items"
  in your reply format.
- Reply in plain engineering prose or a compact numbered plan focused only on new contributions.
- If the design is complete write: FINAL DESIGN COMPLETE
"""

        self.last_context_snapshot = context.strip()
        return context

    # --------------------------------------------
    # HAND RAISE WITH SCORING
    # --------------------------------------------

    def _compute_relevance_score(self, agent_name):
        """
        Compute relevance score based on agent's section matching recent discussion.
        Returns 0.0-1.0 where 1.0 = highly relevant
        """
        agent_section = next(
            (a.get("section", DEFAULT_SECTION) for a in self.agents if a["name"] == agent_name),
            DEFAULT_SECTION,
        )
        
        # Check if agent section matches any recent memory
        memory_text = (
            " ".join(self.shared_memory.get("options", [])[:3]) + " " +
            " ".join(self.shared_memory.get("decisions", [])[:2])
        ).lower()
        
        section_keywords = {
            "programming": ["code", "algorithm", "debug", "api", "backend", "frontend", "software", "implement"],
            "education": ["teach", "learn", "student", "explain", "clarity", "pedagogy", "curriculum"],
            "research": ["research", "evidence", "tradeoff", "analysis", "rigorous", "study"],
            "general": [],
        }
        
        keywords = section_keywords.get(agent_section, [])
        if not keywords:
            return 0.6  # General section gets moderate score
        
        matches = sum(1 for kw in keywords if kw in memory_text)
        relevance = min(0.95, 0.4 + (matches * 0.15))
        return relevance

    def _compute_novelty_score(self, agent_name):
        """
        Compute novelty score based on dissimilarity from recent responses.
        Returns 0.0-1.0 where 1.0 = very different from recent
        """
        if not self.responses:
            return 0.8  # First agent gets high novelty
        
        # Get recent accepted responses
        recent = [r["text"] for r in self.responses if r.get("accepted")][-SIMILARITY_CHECK_WINDOW:]
        if not recent:
            return 0.75
        
        # Normalize texts for comparison
        agent_model = next(
            (a["model"] for a in self.agents if a["name"] == agent_name),
            ""
        )
        model_name = agent_model.split("/")[-1] if "/" in agent_model else agent_model
        
        # novelty based on turn recency: agents who haven't spoken recently get higher novelty
        turns_since_spoke = self.turn - self.last_spoke.get(agent_name, -SIMILARITY_CHECK_WINDOW)
        recency_novelty = min(1.0, turns_since_spoke / (SIMILARITY_CHECK_WINDOW * 2))
        
        return 0.4 + (recency_novelty * 0.6)

    def _compute_confidence_score(self, agent_name):
        """
        Compute confidence score based on historical acceptance rate.
        Returns 0.0-1.0 where 1.0 = always accepted
        """
        return self.agent_success_rates.get(agent_name, 0.5)

    def _compute_fairness_score(self, agent_name):
        """
        Compute fairness score based on turn distribution.
        Returns 0.0-1.0 where 1.0 = long time since last speaking
        """
        if agent_name == HUMAN_NAME:
            return 0.3
        
        turns_since_spoke = self.turn - self.last_spoke.get(agent_name, -MAX_TURNS)
        fairness = min(1.0, turns_since_spoke / (MAX_TURNS * 0.5))
        return fairness

    def _compute_hand_raise_score(self, agent_name):
        """
        Compute overall hand-raise score using weighted components.
        Returns score, breakdown dict
        """
        if agent_name == HUMAN_NAME:
            return 0.0, {}
        
        if self.quotas.get(agent_name, 0) <= 0:
            return 0.0, {}
        
        relevance = self._compute_relevance_score(agent_name)
        novelty = self._compute_novelty_score(agent_name)
        confidence = self._compute_confidence_score(agent_name)
        fairness = self._compute_fairness_score(agent_name)
        
        final_score = (
            RELEVANCE_WEIGHT * relevance +
            NOVELTY_WEIGHT * novelty +
            CONFIDENCE_WEIGHT * confidence +
            FAIRNESS_WEIGHT * fairness
        )
        
        breakdown = {
            "relevance": round(relevance, 3),
            "novelty": round(novelty, 3),
            "confidence": round(confidence, 3),
            "fairness": round(fairness, 3),
            "final": round(final_score, 3),
        }
        
        return final_score, breakdown

    def _update_agent_success_rate(self, agent_name, accepted):
        """
        Update agent's success rate based on this response.
        Uses exponential moving average to give recent results more weight.
        """
        current_rate = self.agent_success_rates.get(agent_name, 0.5)
        alpha = 0.3  # Weight for new observation
        new_rate = alpha * (1.0 if accepted else 0.0) + (1 - alpha) * current_rate
        self.agent_success_rates[agent_name] = new_rate

    def _build_scored_hand_queue(self):
        """
        Build a priority queue of agents based on hand-raise scores.
        Returns sorted list of (score, agent_name) tuples.
        """
        candidates = []
        for a in self.agents:
            agent_name = a["name"]
            if self.quotas.get(agent_name, 0) <= 0:
                continue
            if agent_name in self.hand_queue:
                score, breakdown = self._compute_hand_raise_score(agent_name)
                self.hand_raise_scores[agent_name] = breakdown
                if score >= HAND_RAISE_THRESHOLD:
                    candidates.append((score, agent_name))
        
        # Sort by score descending
        candidates.sort(key=lambda x: (-x[0], self._token_distance(x[1])))
        return candidates

    def maybe_raise_hand(self, agent):
        """
        Raise hand based on score rather than random probability.
        """
        # Prevent immediate self re-queue right after speaking.
        if self.last_spoke.get(agent) == self.turn:
            return

        score, breakdown = self._compute_hand_raise_score(agent)
        self.hand_raise_scores[agent] = breakdown
        if score >= HAND_RAISE_THRESHOLD and agent not in self.hand_queue:
            self.hand_queue.append(agent)

    def enqueue_interrupts(self, speaker):
        """
        Compute interrupt scores for all agents and queue those above threshold.
        """
        for a in self.agents:
            agent = a["name"]
            if agent == speaker or self.quotas[agent] <= 0:
                continue

            score, breakdown = self._compute_hand_raise_score(agent)
            self.hand_raise_scores[agent] = breakdown
            
            starved = (self.turn - self.last_spoke[agent]) >= STARVATION_THRESHOLD
            turns_since_spoke = self.turn - self.last_spoke.get(agent, -1)
            # Throttle auto-queueing so queue doesn't become permanently saturated.
            if starved or (score >= HAND_RAISE_THRESHOLD and turns_since_spoke >= 2):
                if agent not in self.hand_queue:
                    self.hand_queue.append(agent)

    # --------------------------------------------
    # PRIORITY ORDER
    # --------------------------------------------

    def select_next_agent(self):
        live_agents = [a["name"] for a in self.agents if self.quotas[a["name"]] > 0]
        if not live_agents and not self.human_hand_raised:
            return None

        # Score-based selection from hand_queue
        if self.hand_queue:
            ordered_candidates = [
                a for a in list(self.hand_queue) if a == HUMAN_NAME or self.quotas[a] > 0
            ]
            # Human is prioritized only when they naturally reach the front of queue.
            if (
                ordered_candidates
                and ordered_candidates[0] == HUMAN_NAME
                and self.human_hand_raised
            ):
                self.hand_queue = deque([a for a in self.hand_queue if a != HUMAN_NAME])
                self.human_hand_raised = False
                self.last_selection_reason = "Human turn reached front of hand queue"
                return HUMAN_NAME

            # Until Human reaches front, keep Human in queue and score-select agents.
            candidates = [a for a in ordered_candidates if a != HUMAN_NAME]
            if candidates:
                # Re-compute fresh scores for ALL candidates before selecting
                scored_candidates = []
                for candidate in candidates:
                    score, breakdown = self._compute_hand_raise_score(candidate)
                    self.agent_scores[candidate] = score
                    self.hand_raise_scores[candidate] = breakdown
                    scored_candidates.append((score, candidate))
                
                scored_candidates.sort(key=lambda x: (-x[0], self._token_distance(x[1])))
                agent = scored_candidates[0][1]
                agent_score = scored_candidates[0][0]
                
                self.hand_queue = deque([a for a in self.hand_queue if a != agent])
                self.last_selection_reason = f"Hand-raise queue: {agent} raised hand with score {agent_score:.1%}"
                self.current_index = (self._agent_index(agent) + 1) % len(self.agents)
                return agent

        # Starvation protection fallback: only after queue-scoring is exhausted.
        # Cooldown prevents immediate re-selection from starvation.
        recent_speakers = {
            r.get("agent")
            for r in self.responses[-STARVATION_COOLDOWN_TURNS:]
            if r.get("agent")
        }
        starved_agents = [
            a
            for a in live_agents
            if (self.turn - self.last_spoke[a]) >= STARVATION_THRESHOLD and a not in recent_speakers
        ]
        if starved_agents:
            chosen = sorted(
                starved_agents,
                key=lambda a: (self.turn - self.last_spoke[a], -self._token_distance(a)),
                reverse=True,
            )[0]
            self._push_facilitator_event(
                "anti_starvation",
                f"Prioritized {chosen} due to starvation protection.",
            )
            score, breakdown = self._compute_hand_raise_score(chosen)
            self.agent_scores[chosen] = score
            self.hand_raise_scores[chosen] = breakdown
            self.last_selection_reason = (
                f"Starvation fallback ({STARVATION_COOLDOWN_TURNS}-turn cooldown): "
                f"{chosen} starved for {self.turn - self.last_spoke[chosen]} turns"
            )
            self.current_index = (self._agent_index(chosen) + 1) % len(self.agents)
            return chosen

        # Round-robin fallback: use true round-robin if no agent qualifies threshold
        # Otherwise use score-based selection
        fallback_candidates = []
        for i in range(len(self.agents)):
            idx = (self.current_index + i) % len(self.agents)
            agent = self.agents[idx]["name"]

            if self.quotas[agent] > 0:
                score, breakdown = self._compute_hand_raise_score(agent)
                self.agent_scores[agent] = score
                self.hand_raise_scores[agent] = breakdown
                fallback_candidates.append((score, agent, idx))
        
        if fallback_candidates:
            # Check if ANY agent qualifies (score >= threshold)
            qualified = [c for c in fallback_candidates if c[0] >= HAND_RAISE_THRESHOLD]
            
            if qualified:
                # SCORE-BASED: Select highest scorer who meets threshold
                qualified.sort(key=lambda x: (-x[0], self._token_distance(x[1])))
                agent = qualified[0][1]
                agent_score = qualified[0][0]
                self.last_selection_reason = f"Score-based: {agent} qualifies with {agent_score:.1%} (threshold: {HAND_RAISE_THRESHOLD:.1%})"
            else:
                # ROUND-ROBIN: No one qualifies, use pure round-robin order
                # (preserve current_index for fair rotation)
                agent = fallback_candidates[0][1]
                agent_score = fallback_candidates[0][0]
                self.last_selection_reason = f"Round-robin (all below {HAND_RAISE_THRESHOLD:.1%} threshold): {agent}'s turn ({agent_score:.1%})"
                
            self.current_index = (self._agent_index(agent) + 1) % len(self.agents)
            return agent

        return None

    # --------------------------------------------
    # STEP
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

        agent_name = self.select_next_agent()

        if not agent_name:
            return {
                "status": "done",
                "agent": "System",
                "agent_model": None,
                "text": "All agents exhausted quotas.",
                "round": self.turn,
                **self._state_payload(),
            }

        # Once selected, remove agent from hand queue regardless of selection path
        # (starvation, score-based queue, round-robin fallback, or human hand-raise).
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

        context = self.build_context(agent_name)

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": "Continue the reasoning."},
        ]

        try:
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
                **self._state_payload(),
            }

        # If a human control command arrived while this model call was in-flight,
        # do not emit/process the model output.
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
        
        # Update agent success rate
        self._update_agent_success_rate(agent_name, ignored_reason is None)

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
                    **self._state_payload(),
                }

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
        
        # Recalculate all scores after this turn so UI shows current values
        for agent in self.agents:
            agent_name_iter = agent["name"]
            score, breakdown = self._compute_hand_raise_score(agent_name_iter)
            self.agent_scores[agent_name_iter] = score
            self.hand_raise_scores[agent_name_iter] = breakdown

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
            **self._state_payload(),
        }

    # --------------------------------------------
    # HUMAN COMMANDS
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
        return entry

    def raise_human_hand(self):
        if HUMAN_NAME not in self.hand_queue:
            self.hand_queue.append(HUMAN_NAME)
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

        self.enqueue_interrupts(HUMAN_NAME)
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

        self.enqueue_interrupts(HUMAN_NAME)
        self.awaiting_human_turn = False
        self.turn += 1

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
