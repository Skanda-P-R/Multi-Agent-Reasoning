import json
import random
import re
import time
from collections import deque
from difflib import SequenceMatcher

from dpr_constants import (
    BROADCAST_CALL_DELAY_SECONDS,
    DEFAULT_SECTION,
    HUMAN_NAME,
    RECENT_TURNS_IN_CONTEXT,
    SECTION_HEADERS,
    STARVATION_COOLDOWN_TURNS,
    STARVATION_THRESHOLD,
    STARTING_QUOTA,
)
from dpr_model_client import call_model


class DPRIntentMixin:
    # --------------------------------------------
    # INTENT PARSING / SCORING HELPERS
    # --------------------------------------------
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

    # --------------------------------------------
    # BROADCAST INTENT COLLECTION
    # --------------------------------------------
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
        low_quota_threshold = max(1, STARTING_QUOTA // 3)

        intents = []
        raw_candidates = []
        broadcast_event = {
            "turn": self.turn,
            "memory_snapshot": memory_block,
            "recent_turns": recent,
            "human_instruction": human_instruction,
            "redirect": redirect,
            "recent_pointer_themes": recent_pointer_themes,
            "agent_intents": [],
            "raw_candidates": [],
            "queued_intents": [],
        }
        for agent in self.agents:
            name = agent["name"]
            remaining_quota = self.quotas.get(name, 0)
            if remaining_quota <= 0:
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

Your remaining turns quota:
- Remaining quota: {remaining_quota}
- Initial quota per agent: {STARTING_QUOTA}

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
- If your remaining quota is low (<= {low_quota_threshold}), raise hand ONLY for high-value contributions:
  strong confidence, concrete pointer, and clear unresolved impact.
"""
            raw = ""
            agent_record = {
                "agent": name,
                "model": agent["model"],
                "section": section,
                "remaining_quota": remaining_quota,
                "attempts": [],
                "accepted_for_queue": False,
                "reject_reason": None,
            }
            for _ in range(2):
                try:
                    time.sleep(BROADCAST_CALL_DELAY_SECONDS)
                    raw = call_model(
                        agent["model"],
                        [{"role": "system", "content": "Return strict JSON only."}, {"role": "user", "content": prompt}],
                        max_tokens=260,
                        temperature=0.1,
                    )
                    parsed_attempt = self._parse_intent_response(raw)
                    agent_record["attempts"].append({
                        "status": "ok",
                        "raw": raw,
                        "parsed": parsed_attempt,
                    })
                except Exception as e:
                    raw = ""
                    parsed_attempt = self._parse_intent_response(raw)
                    agent_record["attempts"].append({
                        "status": "error",
                        "error": str(e),
                        "raw": raw,
                        "parsed": parsed_attempt,
                    })
                intent = self._parse_intent_response(raw)
                if intent["hand_raise"] or intent["pointer"]:
                    break
            intent = self._parse_intent_response(raw)
            agent_record["parsed_intent"] = dict(intent)
            if intent.get("pointer"):
                raw_candidates.append({"agent": name, "section": section, **intent})
                broadcast_event["raw_candidates"].append({"agent": name, "section": section, **intent})
            if not (intent["hand_raise"] and intent["pointer"]):
                agent_record["reject_reason"] = "no_hand_raise_or_pointer"
                broadcast_event["agent_intents"].append(agent_record)
                continue

            # section-fit validation and one retry if mismatch
            fit = self._section_fit_score(section, intent["pointer"])
            agent_record["section_fit_score"] = fit
            if section != "general" and fit < 0.15:
                retried = self._intent_retry_for_section_fit(agent, memory_block, recent_block, human_instruction, redirect)
                if retried and retried.get("hand_raise") and retried.get("pointer"):
                    agent_record["section_retry"] = dict(retried)
                    intent = retried
                    fit = self._section_fit_score(section, intent["pointer"])
                    agent_record["section_fit_score"] = fit
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

            # Near quota exhaustion: only allow strong hand-raise candidates.
            if remaining_quota <= low_quota_threshold:
                low_quota_reject = (
                    intent["confidence"] < 0.75
                    or self._is_vague_pointer(intent["pointer"])
                    or (section != "general" and fit < 0.25)
                )
                if low_quota_reject:
                    agent_record["reject_reason"] = "low_quota_quality_gate"
                    agent_record["final_intent"] = dict(intent)
                    broadcast_event["agent_intents"].append(agent_record)
                    continue

            queued_intent = {"agent": name, "section": section, **intent}
            agent_record["accepted_for_queue"] = True
            agent_record["final_intent"] = dict(queued_intent)
            broadcast_event["agent_intents"].append(agent_record)
            intents.append(queued_intent)

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
                cand_section = cand.get("section", DEFAULT_SECTION)
                cand_fit = self._section_fit_score(cand_section, cand.get("pointer", ""))
                cand_quota = self.quotas.get(cand["agent"], 0)
                if cand_quota <= low_quota_threshold and (
                    cand.get("confidence", 0.0) < 0.82
                    or self._is_vague_pointer(cand.get("pointer", ""))
                    or (cand_section != "general" and cand_fit < 0.25)
                ):
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
        broadcast_event["valid_intents"] = list(intents)
        broadcast_event["queued_intents"] = list(deduped)
        self.broadcast_events.append(broadcast_event)
        if hasattr(self, "record_event"):
            self.record_event("broadcast", {
                "raised_count": len(deduped),
                "queued_agents": [x["agent"] for x in deduped],
            })
        self.pending_human_instruction = None

    # --------------------------------------------
    # QUEUE SELECTION / FALLBACK
    # --------------------------------------------
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

    def _pick_starvation_agent(self):
        queued_agents = {x.get("agent") for x in self.intent_queue if isinstance(x, dict)}
        candidates = []
        for agent in self.agents:
            name = agent["name"]
            if self.quotas.get(name, 0) <= 0:
                continue
            if name in queued_agents:
                continue
            last = self.last_spoke.get(name, -1)
            turns_waited = self.turn - last if last >= 0 else self.turn + 1
            if turns_waited >= STARVATION_THRESHOLD:
                candidates.append((turns_waited, -self._token_distance(name), name))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        turns_waited, _, chosen = candidates[0]
        self.last_selection_reason = (
            f"Starvation selection: {chosen} waited {turns_waited} turns (threshold={STARVATION_THRESHOLD}, cooldown={STARVATION_COOLDOWN_TURNS})."
        )
        return {
            "agent": chosen,
            "pointer": "",
            "priority_label": "starvation",
            "priority_rank": 0,
            "confidence": 1.0,
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
        starvation_pick = self._pick_starvation_agent()
        if starvation_pick:
            return starvation_pick
        self._broadcast_for_queue()
        while self.intent_queue:
            item = self.intent_queue.popleft()
            if self.quotas.get(item["agent"], 0) > 0:
                self.hand_queue = deque([x["agent"] for x in self.intent_queue] + ([HUMAN_NAME] if HUMAN_NAME in self.hand_queue else []))
                self.last_selection_reason = (
                    f"Broadcast selection: {item['agent']} ({item['priority_label']}, {item['confidence']:.2f})"
                )
                return dict(item)
        starvation_pick = self._pick_starvation_agent()
        if starvation_pick:
            return starvation_pick
        fallback = self._pick_bootstrap_agent()
        if fallback:
            self.last_selection_reason = f"Fallback random selection: {fallback['agent']}"
        return fallback
