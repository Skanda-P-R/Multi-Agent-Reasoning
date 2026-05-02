import json
import random
import re
import time
from difflib import SequenceMatcher
from collections import deque

import requests
from dpr_constants import (
    AVAILABLE_MODELS,
    DECISION_START_VERBS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    HUMAN_NAME,
    LOOP_WINDOW,
    MAX_REPEAT_STREAK,
    MAX_SUMMARY_TEXT_CHARS,
    MAX_TURNS,
    MEMORY_CALL_DELAY_SECONDS,
    MEMORY_CHANGELOG_LIMIT,
    MEMORY_ITEM_CHAR_LIMIT,
    MEMORY_MODEL,
    MEMORY_SECTION_LIMIT,
    MEMORY_SIMILARITY_THRESHOLD,
    MIN_PERSISTENT_OPEN_QUESTIONS,
    RECENT_TURNS_IN_CONTEXT,
    REDIRECT_DURATION_TURNS,
    SECTION_HEADERS,
    SECTION_PRIORITY_HINTS,
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

    def build_context(self, agent_name, pointer_text=None, consume_human_instruction=False):
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
            if consume_human_instruction:
                self.pending_human_instruction = None

        redirect_block = ""
        if self.pending_redirect and self.pending_redirect["remaining"] > 0:
            redirect_block = f"""
Facilitator redirection (high priority):
{self.pending_redirect['message']}

You MUST keep your response aligned to this redirection.
"""

        pointer_block = ""
        if pointer_text:
            pointer_block = f"""
Agent pointer (focus this turn):
{pointer_text}

You MUST focus your response on this pointer and advance it concretely.
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
{pointer_block}

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

    def _extract_json_object_loose(self, raw_text):
        cleaned = (raw_text or "").strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
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
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
        return None

    def _parse_priority_rank(self, value):
        if value is None:
            return 3, "medium"
        text = str(value).strip().lower()
        name_map = {"high": 1, "medium": 2, "low": 3}
        if text in name_map:
            return name_map[text], text
        match = re.search(r"\d+", text)
        if match:
            n = max(1, min(5, int(match.group(0))))
            return n, f"p{n}"
        return 3, "medium"

    def _parse_intent_response(self, raw_text):
        parsed = self._extract_json_object_loose(raw_text)
        if not isinstance(parsed, dict):
            return {"hand_raise": False, "priority_rank": 3, "priority_label": "medium", "pointer": "", "confidence": 0.0}
        hand_raise = parsed.get("hand_raise", False)
        if isinstance(hand_raise, str):
            hand_raise = hand_raise.strip().lower() in {"true", "yes", "1"}
        rank, label = self._parse_priority_rank(parsed.get("priority"))
        pointer = str(parsed.get("pointer", "")).strip()
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return {
            "hand_raise": bool(hand_raise),
            "priority_rank": rank,
            "priority_label": label,
            "pointer": pointer,
            "confidence": confidence,
        }

    def _pointer_theme(self, pointer_text):
        cleaned = self._normalize(pointer_text or "")
        tokens = [t for t in cleaned.split() if len(t) > 2]
        return " ".join(tokens[:6])

    def _pointer_similarity(self, a, b):
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, self._normalize(a), self._normalize(b)).ratio()

    def _is_vague_pointer(self, pointer_text):
        text = self._normalize(pointer_text or "")
        if len(text) < 12:
            return True
        vague_patterns = [
            "risk mitigation",
            "trade offs",
            "tradeoff",
            "residual risk",
            "overall improvement",
            "general optimization",
            "system reliability",
        ]
        return any(v in text for v in vague_patterns)

    def _section_keywords(self, section):
        mapping = {
            "programming": {"api", "schema", "algorithm", "code", "module", "service", "pipeline", "database", "testing", "interface", "integration"},
            "education": {"student", "curriculum", "learning", "teaching", "pedagogy", "training", "assessment", "awareness"},
            "research": {"evidence", "experiment", "hypothesis", "metric", "analysis", "validation", "study", "benchmark"},
            "general": {"operations", "deployment", "policy", "governance", "budget", "timeline", "stakeholder", "risk"},
        }
        return mapping.get(section, mapping["general"])

    def _section_fit_score(self, section, pointer_text):
        tokens = set(re.findall(r"[a-z0-9]+", (pointer_text or "").lower()))
        if not tokens:
            return 0.0
        keywords = self._section_keywords(section)
        matches = len(tokens & keywords)
        return matches / max(1, min(5, len(keywords)))

    def _fallback_pointer_for_section(self, section):
        seeds = (
            self.shared_memory.get("open_questions", [])[:3]
            + self.shared_memory.get("actions", [])[:3]
            + self.shared_memory.get("options", [])[:3]
        )
        seed = seeds[0] if seeds else self.question
        templates = {
            "programming": "Implementation architecture and API plan for: {seed}",
            "education": "Adoption, onboarding, and learning plan for: {seed}",
            "research": "Validation metrics and experiment design for: {seed}",
            "general": "Operational plan and tradeoff resolution for: {seed}",
        }
        tpl = templates.get(section, templates["general"])
        return self._short_text(tpl.format(seed=seed), 140)

    def _intent_retry_for_section_fit(self, agent, memory_block, recent_block, human_instruction, redirect):
        section = agent.get("section", DEFAULT_SECTION)
        section_header = SECTION_HEADERS.get(section, SECTION_HEADERS[DEFAULT_SECTION])
        prompt = f"""
You are {agent['name']} in a distributed reasoning protocol.
{section_header}

Original problem:
{self.question}

Shared memory state:
{memory_block}

Recent accepted turns:
{recent_block}

Human instruction:
{human_instruction or "(none)"}

Redirect:
{redirect or "(none)"}

Your previous pointer was not aligned to your section.
Return ONLY JSON and make pointer strictly section-aligned:
{{
  "hand_raise": true/false,
  "priority": "high|medium|low OR 1..5",
  "pointer": "section-specific gap/risk/decision",
  "confidence": 0.0 to 1.0,
  "domain": "{section}",
  "why_section_fit": "one short line"
}}
"""
        try:
            raw = call_model(
                agent["model"],
                [{"role": "system", "content": "Return strict JSON only."}, {"role": "user", "content": prompt}],
                max_tokens=260,
                temperature=0.1,
            )
        except Exception:
            return None
        return self._parse_intent_response(raw)

    def _broadcast_for_queue(self):
        memory_block = self._memory_snapshot_text()
        recent = [r for r in self.responses if r.get("accepted")][-RECENT_TURNS_IN_CONTEXT:]
        recent_block = "\n".join(f"- {r['agent']}: {self._short_text(r['text'], 220)}" for r in recent) or "- (none yet)"
        human_instruction = self.pending_human_instruction or ""
        redirect = self.pending_redirect["message"] if self.pending_redirect and self.pending_redirect["remaining"] > 0 else ""
        recent_pointer_themes = [self._pointer_theme(p) for p in self.pointer_history[-3:] if p]
        recent_pointer_block = "\n".join(f"- {p}" for p in recent_pointer_themes) or "- (none yet)"
        covered_keywords = set()
        for key in ["decisions", "actions", "open_questions"]:
            for item in self.shared_memory.get(key, [])[:4]:
                covered_keywords.update(re.findall(r"[a-z0-9]+", item.lower()))

        intents = []
        raw_candidates = []
        for agent in self.agents:
            name = agent["name"]
            if self.quotas.get(name, 0) <= 0:
                continue
            section = agent.get("section", DEFAULT_SECTION)
            section_header = SECTION_HEADERS.get(agent.get("section", DEFAULT_SECTION), SECTION_HEADERS[DEFAULT_SECTION])
            prompt = f"""
You are {name} in a distributed reasoning protocol.
{section_header}

Original problem:
{self.question}

Shared memory state:
{memory_block}

Recent accepted turns:
{recent_block}

Human instruction:
{human_instruction or "(none)"}

Redirect:
{redirect or "(none)"}

Recently selected pointer themes (avoid repeating unless unresolved with concrete new delta):
{recent_pointer_block}

Decide if you should raise hand NOW and return ONLY JSON:
{{
  "hand_raise": true/false,
  "priority": "high|medium|low OR 1..5",
  "pointer": "specific gap/risk/decision you will address next (must be section-aligned)",
  "confidence": 0.0 to 1.0,
  "domain": "{section}",
  "why_section_fit": "one short line"
}}

Rules:
- Avoid vague pointers like generic risk mitigation.
- Do not repeat recent pointer themes unless you add a concrete unresolved constraint.
"""
            raw = ""
            for _ in range(2):
                try:
                    raw = call_model(
                        agent["model"],
                        [{"role": "system", "content": "Return strict JSON only."}, {"role": "user", "content": prompt}],
                        max_tokens=260,
                        temperature=0.1,
                    )
                except Exception:
                    raw = ""
                intent = self._parse_intent_response(raw)
                if intent["hand_raise"] or intent["pointer"]:
                    break
            intent = self._parse_intent_response(raw)
            if intent.get("pointer"):
                raw_candidates.append({"agent": name, "section": section, **intent})
            if not (intent["hand_raise"] and intent["pointer"]):
                continue

            # section-fit validation and one retry if mismatch
            fit = self._section_fit_score(section, intent["pointer"])
            if section != "general" and fit < 0.15:
                retried = self._intent_retry_for_section_fit(agent, memory_block, recent_block, human_instruction, redirect)
                if retried and retried.get("hand_raise") and retried.get("pointer"):
                    intent = retried
                    fit = self._section_fit_score(section, intent["pointer"])
            if section != "general" and fit < 0.15:
                intent["priority_rank"] = max(2, intent["priority_rank"])
                intent["priority_label"] = "medium"
                intent["confidence"] = min(intent["confidence"], 0.55)

            # reject vague/meta pointers unless very concrete length
            if self._is_vague_pointer(intent["pointer"]) and len(intent["pointer"]) < 45:
                intent["priority_rank"] = max(2, intent["priority_rank"])
                intent["priority_label"] = "medium"
                intent["confidence"] = min(intent["confidence"], 0.55)

            # soften if pointer repeats very recent selected theme
            if any(self._pointer_similarity(intent["pointer"], old) >= 0.78 for old in self.pointer_history[-3:]):
                intent["priority_rank"] = max(3, intent["priority_rank"])
                intent["priority_label"] = "low"
                intent["confidence"] = min(intent["confidence"], 0.45)

            # reject if pointer has no overlap with unresolved/open/action coverage terms
            p_tokens = set(re.findall(r"[a-z0-9]+", intent["pointer"].lower()))
            if covered_keywords and len(p_tokens & covered_keywords) == 0:
                intent["priority_rank"] = max(2, intent["priority_rank"])
                intent["priority_label"] = "medium"

            intents.append({"agent": name, "section": section, **intent})

        # dedupe near-duplicate pointers: keep highest confidence among similar themes
        deduped = []
        for cand in sorted(intents, key=lambda x: (x["priority_rank"], -x["confidence"], x["agent"])):
            if any(self._pointer_similarity(cand["pointer"], existing["pointer"]) >= 0.80 for existing in deduped):
                continue
            deduped.append(cand)

        # If queue is too small, recover from raw candidates (even those that did not raise hand),
        # then synthesize section-aligned fallback pointers.
        if len(deduped) < 2:
            for cand in sorted(raw_candidates, key=lambda x: (-x.get("confidence", 0.0), x["agent"])):
                if cand["agent"] in {x["agent"] for x in deduped}:
                    continue
                if any(self._pointer_similarity(cand["pointer"], existing["pointer"]) >= 0.82 for existing in deduped):
                    continue
                cand = dict(cand)
                cand["hand_raise"] = True
                cand["priority_rank"] = max(2, cand.get("priority_rank", 2))
                cand["priority_label"] = "medium"
                deduped.append(cand)
                if len(deduped) >= 2:
                    break

        if len(deduped) < 2:
            existing_agents = {x["agent"] for x in deduped}
            for agent in self.agents:
                if self.quotas.get(agent["name"], 0) <= 0 or agent["name"] in existing_agents:
                    continue
                synthetic_pointer = self._fallback_pointer_for_section(agent.get("section", DEFAULT_SECTION))
                if any(self._pointer_similarity(synthetic_pointer, existing["pointer"]) >= 0.82 for existing in deduped):
                    continue
                deduped.append({
                    "agent": agent["name"],
                    "section": agent.get("section", DEFAULT_SECTION),
                    "hand_raise": True,
                    "priority_rank": 3,
                    "priority_label": "low",
                    "pointer": synthetic_pointer,
                    "confidence": 0.35,
                })
                if len(deduped) >= 2:
                    break
        deduped.sort(key=lambda x: (x["priority_rank"], -x["confidence"], x["agent"]))
        self.intent_queue = deque(deduped)
        self.hand_queue = deque([x["agent"] for x in deduped])
        self.last_selection_reason = f"Broadcast produced {len(deduped)} raised hands."
        self.pending_human_instruction = None

    def _pick_bootstrap_agent(self):
        eligible = [a["name"] for a in self.agents if self.quotas.get(a["name"], 0) > 0]
        if not eligible:
            return None
        chosen = random.choice(eligible)
        self.last_selection_reason = f"Bootstrap random first turn: {chosen}"
        return {
            "agent": chosen,
            "pointer": "",
            "priority_label": "bootstrap",
            "priority_rank": None,
            "confidence": None,
            "hand_raise": False,
        }

    def _next_from_queue_or_broadcast(self):
        while self.intent_queue:
            item = self.intent_queue.popleft()
            if item["agent"] == HUMAN_NAME:
                self.hand_queue = deque([x for x in self.hand_queue if x != HUMAN_NAME])
                return {
                    "agent": HUMAN_NAME,
                    "pointer": "",
                    "priority_label": "human",
                    "priority_rank": 99,
                    "confidence": 1.0,
                    "hand_raise": True,
                }
            if self.quotas.get(item["agent"], 0) > 0:
                self.hand_queue = deque([x["agent"] for x in self.intent_queue if x["agent"] != HUMAN_NAME] + ([HUMAN_NAME] if HUMAN_NAME in self.hand_queue else []))
                self.last_selection_reason = (
                    f"Queue selection: {item['agent']} ({item['priority_label']}, {item['confidence']:.2f})"
                )
                return dict(item)
        self._broadcast_for_queue()
        while self.intent_queue:
            item = self.intent_queue.popleft()
            if self.quotas.get(item["agent"], 0) > 0:
                self.hand_queue = deque([x["agent"] for x in self.intent_queue] + ([HUMAN_NAME] if HUMAN_NAME in self.hand_queue else []))
                self.last_selection_reason = (
                    f"Broadcast selection: {item['agent']} ({item['priority_label']}, {item['confidence']:.2f})"
                )
                return dict(item)
        fallback = self._pick_bootstrap_agent()
        if fallback:
            self.last_selection_reason = f"Fallback random selection: {fallback['agent']}"
        return fallback

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

        selected = None
        if not self.bootstrap_done:
            selected = self._pick_bootstrap_agent()
            self.bootstrap_done = True
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
