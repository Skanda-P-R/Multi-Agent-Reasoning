import json
import re
import time
from difflib import SequenceMatcher

from dpr_constants import (
    DECISION_START_VERBS,
    DEFAULT_SECTION,
    HUMAN_NAME,
    MAX_SUMMARY_TEXT_CHARS,
    MEMORY_CALL_DELAY_SECONDS,
    MEMORY_CHANGELOG_LIMIT,
    MEMORY_ITEM_CHAR_LIMIT,
    MEMORY_MODEL,
    MEMORY_SECTION_LIMIT,
    MEMORY_SIMILARITY_THRESHOLD,
    MIN_COMPLETION_ACCEPTED_TURNS,
    MIN_COMPLETION_AGENT_COVERAGE_RATIO,
    MIN_COMPLETION_MEMORY_ITEMS,
    MIN_COMPLETION_POINTERS_ADDRESSED,
    MIN_PERSISTENT_OPEN_QUESTIONS,
    RECENT_TURNS_IN_CONTEXT,
    SECTION_HEADERS,
    SECTION_PRIORITY_HINTS,
)
from dpr_model_client import call_model


class DPRMemoryMixin:
    # --------------------------------------------
    # TEXT NORMALIZATION / CLEANING
    # --------------------------------------------
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

    # --------------------------------------------
    # MEMORY INFERENCE HELPERS
    # --------------------------------------------
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

    # --------------------------------------------
    # JSON EXTRACTION / FALLBACK MEMORY BUILDING
    # --------------------------------------------
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

    # --------------------------------------------
    # MEMORY SNAPSHOT / MERGE
    # --------------------------------------------
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

    # --------------------------------------------
    # MEMORY UPDATE PIPELINE
    # --------------------------------------------
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

    # --------------------------------------------
    # SESSION STATE / CONTEXT CONSTRUCTION
    # --------------------------------------------
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

        completion_block = f"""
Completion gate:
- Do NOT write FINAL DESIGN COMPLETE while this turn has an active pointer, while the broadcast queue has pending pointers, or while you can identify any meaningful gap to add.
- Only write FINAL DESIGN COMPLETE when the shared context is broad and settled: at least {MIN_COMPLETION_ACCEPTED_TURNS} accepted agent turns, at least {MIN_COMPLETION_MEMORY_ITEMS} shared memory items, at least {MIN_COMPLETION_POINTERS_ADDRESSED} addressed pointers, and at least {MIN_COMPLETION_AGENT_COVERAGE_RATIO:.0%} agent coverage.
- If any condition is not clearly satisfied, keep contributing without using the final-completion phrase.
"""
        if hasattr(self, "_completion_readiness"):
            readiness = self._completion_readiness(current_pointer=pointer_text or "")
            if readiness.get("ready"):
                completion_block += "\nCurrent completion gate status: eligible if no new gap remains.\n"
            else:
                completion_block += (
                    "\nCurrent completion gate status: not eligible yet. Missing: "
                    + "; ".join(readiness.get("blockers", []))
                    + "\n"
                )

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
{completion_block}

Instructions:
- Continue the design logically.
- Do NOT repeat earlier reasoning.
- Add new improvements or extensions.
- Use shared memory as background context, but do NOT mirror or restate section headings like
  "Facts / Assumptions", "Options / Proposals", "Decisions", "Open Questions", or "Action Items"
  in your reply format.
- Reply in plain engineering prose or a compact numbered plan focused only on new contributions.
- Use FINAL DESIGN COMPLETE only when the completion gate says eligible and your own analysis finds no useful remaining pointer.
"""

        self.last_context_snapshot = context.strip()
        return context
