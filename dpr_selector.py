import ast
import json
import re

from dpr_constants import (
    AVAILABLE_MODELS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    SECTION_HEADERS,
    SELECTOR_MAX_PANEL_SIZE,
    SELECTOR_MODEL,
    SELECTOR_MODELS_PER_SECTION,
    SELECTOR_PROVIDER,
)
from dpr_model_client import call_model


# --------------------------------------------
# JSON PARSING HELPERS
# --------------------------------------------
def _parse_mapping_candidate(candidate):
    candidate = (candidate or "").strip()
    if not candidate:
        return None

    repairs = [
        candidate,
        candidate.replace("“", '"').replace("”", '"').replace("’", "'"),
        re.sub(r",\s*([}\]])", r"\1", candidate),
    ]
    for text in repairs:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return parsed
        except (SyntaxError, ValueError):
            pass
    return None


def _balanced_json_object_candidates(text):
    candidates = []
    start = None
    depth = 0
    in_string = False
    escaped = False

    for idx, char in enumerate(text or ""):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : idx + 1])
                start = None

    return candidates


def _extract_json_object_loose(raw_text):
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return None

    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    parsed = _parse_mapping_candidate(cleaned)
    if isinstance(parsed, dict):
        return parsed

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
        parsed = _parse_mapping_candidate(cleaned)
        if isinstance(parsed, dict):
            return parsed

    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if code_match:
        parsed = _parse_mapping_candidate(code_match.group(1))
        if isinstance(parsed, dict):
            return parsed

    for candidate in reversed(_balanced_json_object_candidates(cleaned)):
        parsed = _parse_mapping_candidate(candidate)
        if isinstance(parsed, dict):
            return parsed

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = cleaned[start : end + 1]
    parsed = _parse_mapping_candidate(candidate)
    if isinstance(parsed, dict):
        return parsed

    return None


def _normalize_section(value, fallback=DEFAULT_SECTION):
    section = str(value or fallback).strip().lower()
    return section if section in SECTION_HEADERS else fallback


def _selector_seed_items(parsed, primary_section):
    seed_items = []
    raw_models = parsed.get("models", []) if isinstance(parsed, dict) else []
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict):
                model = str(item.get("model", "")).strip()
                section = _normalize_section(item.get("section", primary_section), primary_section)
                if model in AVAILABLE_MODELS:
                    seed_items.append({"model": model, "section": section})
            elif isinstance(item, str):
                model = item.strip()
                if model in AVAILABLE_MODELS:
                    seed_items.append({"model": model, "section": primary_section})

    return seed_items


def _salvage_selector_response(raw_text, primary_section=DEFAULT_SECTION):
    seed_items = []
    for line in (raw_text or "").splitlines():
        lowered = line.lower()
        line_sections = [
            (match.start(), section)
            for section in SECTION_HEADERS
            for match in [re.search(rf"\b{re.escape(section)}\b", lowered)]
            if match
        ]
        section = sorted(line_sections)[0][1] if line_sections else primary_section
        for model in AVAILABLE_MODELS:
            if model in line:
                seed_items.append({"model": model, "section": section})

    if not seed_items:
        return None

    return {
        "section": seed_items[0]["section"],
        "models": seed_items,
    }


# --------------------------------------------
# HEURISTIC FALLBACK SELECTOR
# --------------------------------------------
def _model_order_for_section(section):
    per_section_models = {
        "programming": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"],
        "education": ["llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
    }
    model_order = list(per_section_models.get(section, DEFAULT_AGENT_MODELS))
    for model in AVAILABLE_MODELS:
        if model not in model_order:
            model_order.append(model)
    return [model for model in model_order if model in AVAILABLE_MODELS]


def _append_model_section(chosen, seen_pairs, model, section):
    pair = (model, section)
    if model not in AVAILABLE_MODELS or section not in SECTION_HEADERS or pair in seen_pairs:
        return False
    chosen.append({"model": model, "section": section})
    seen_pairs.add(pair)
    return True


def _expand_model_section_panel(seed_items, primary_section):
    sections = []
    for item in seed_items:
        section = str(item.get("section", primary_section)).strip().lower() or primary_section
        if section not in SECTION_HEADERS:
            section = primary_section
        if section not in sections:
            sections.append(section)
    if primary_section not in sections:
        sections.insert(0, primary_section)
    if not sections:
        sections = [DEFAULT_SECTION]

    chosen = []
    seen_pairs = set()

    # First add a balanced set of repeated model IDs across the selected sections.
    # This is what turns one-model-per-section LLM output into a fuller agent panel.
    for section in sections:
        for model in _model_order_for_section(section)[:SELECTOR_MODELS_PER_SECTION]:
            _append_model_section(chosen, seen_pairs, model, section)
            if len(chosen) >= SELECTOR_MAX_PANEL_SIZE:
                return chosen

    # Preserve any extra exact pairs the selector asked for, such as the 8b model
    # in a section where it was not part of the default balanced expansion.
    for item in seed_items:
        model = str(item.get("model", "")).strip()
        section = str(item.get("section", primary_section)).strip().lower() or primary_section
        if section not in SECTION_HEADERS:
            section = primary_section
        _append_model_section(chosen, seen_pairs, model, section)
        if len(chosen) >= SELECTOR_MAX_PANEL_SIZE:
            return chosen

    if len(chosen) < 2:
        for model in AVAILABLE_MODELS:
            _append_model_section(chosen, seen_pairs, model, primary_section)
            if len(chosen) >= 2:
                break

    return chosen


def _heuristic_suggest_models_for_question(question):
    q = (question or "").lower()

    section_keywords = {
        "programming": [
            "code",
            "program",
            "algorithm",
            "bug",
            "debug",
            "python",
            "java",
            "javascript",
            "api",
            "database",
            "backend",
            "frontend",
            "software",
        ],
        "education": [
            "teach",
            "education",
            "student",
            "curriculum",
            "lesson",
            "learning",
            "classroom",
            "school",
            "exam",
        ],
        "research": ["research", "study", "evidence", "experiment", "benchmark", "metric", "analysis"],
        "product": ["product", "user", "customer", "feature", "roadmap", "persona", "adoption"],
        "design": ["design", "ux", "ui", "usability", "accessibility", "wireframe", "prototype", "flow"],
        "business": ["business", "market", "pricing", "revenue", "sales", "cost", "roi", "strategy"],
        "operations": ["operations", "process", "workflow", "deployment", "handoff", "maintenance", "runbook"],
        "security": ["security", "privacy", "threat", "abuse", "auth", "compliance", "encryption"],
        "ethics": ["ethics", "fairness", "bias", "harm", "policy", "governance", "accountability"],
    }

    scored_sections = []
    for section, keywords in section_keywords.items():
        hits = sum(1 for keyword in keywords if keyword in q)
        if hits:
            scored_sections.append((hits, section))
    scored_sections.sort(key=lambda item: (-item[0], item[1]))

    sections = [section for _, section in scored_sections[:4]] or [DEFAULT_SECTION]
    primary_section = sections[0]
    chosen = _expand_model_section_panel(
        [{"model": model, "section": section} for section in sections for model in _model_order_for_section(section)],
        primary_section,
    )

    return {
        "section": primary_section,
        "models": chosen,
        "_selector_meta": {
            "source": "heuristic",
            "reason": "keyword_fallback",
            "raw_output": None,
        },
    }


# --------------------------------------------
# LLM-BASED SELECTOR CALL
# --------------------------------------------
def _call_selector_model_json(question, retry=False):
    allowed_sections = list(SECTION_HEADERS.keys())
    section_schema = "|".join(allowed_sections)
    schema_hint = {
        "section": section_schema,
        "sections": [section_schema],
        "models": [{"model": "<allowed model>", "section": section_schema}],
    }

    system_prompt = (
        "You are ONLY a model-router. "
        "Do NOT answer the user's question content. "
        "Return ONLY one JSON object with keys: section, sections, models. "
        "No markdown. No prose."
    )
    if retry:
        system_prompt += " PRIOR ATTEMPT FAILED. STRICT JSON OBJECT ONLY."

    user_prompt = (
        "Select a panel for multi-agent discussion.\n"
        f"Allowed models: {json.dumps(AVAILABLE_MODELS)}\n"
        f"Allowed sections: {json.dumps(allowed_sections)}\n"
        "Rules: choose a dynamic number of panelists based on task complexity "
        f"(min 2, max {SELECTOR_MAX_PANEL_SIZE}). Reuse the same model ID across different sections whenever that improves coverage. "
        "The models array is the final selected agent panel; the application will not add extra pairs for you. "
        "Prefer multiple model-section pairs per important section when useful. Avoid exact duplicate entries of the same model+section. "
        "Use mixed sections for multi-domain questions. Do not return only sections; each selected agent must appear in models.\n"
        f"Question: {question}\n"
        f"Output schema: {json.dumps(schema_hint)}"
    )

    return call_model(
        SELECTOR_MODEL,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
        max_tokens=420,
        provider=SELECTOR_PROVIDER,
    )


# --------------------------------------------
# PUBLIC SELECTOR API
# --------------------------------------------
def suggest_models_for_question(question):
    question = (question or "").strip()
    if not question:
        return _heuristic_suggest_models_for_question(question)

    try:
        selector_source = "llm"
        selector_reason = "ok"
        raw = _call_selector_model_json(question, retry=False)
        parsed = _extract_json_object_loose(raw)
        if not isinstance(parsed, dict):
            raw_retry = _call_selector_model_json(question, retry=True)
            raw = raw_retry
            parsed = _extract_json_object_loose(raw_retry)
            if not isinstance(parsed, dict):
                parsed = _salvage_selector_response(raw_retry)
                if isinstance(parsed, dict):
                    selector_source = "llm_salvaged"
                    selector_reason = "text_model_pairs"
                else:
                    fallback = _heuristic_suggest_models_for_question(question)
                    fallback["_selector_meta"] = {
                        "source": "heuristic",
                        "reason": "llm_parse_failed",
                        "raw_output": raw,
                    }
                    return fallback

        primary_section = str(parsed.get("section", DEFAULT_SECTION)).strip().lower() or DEFAULT_SECTION
        if primary_section not in SECTION_HEADERS:
            primary_section = DEFAULT_SECTION

        if not any(key in parsed for key in ("model", "models", "section", "sections")):
            fallback = _heuristic_suggest_models_for_question(question)
            fallback["_selector_meta"] = {
                "source": "heuristic",
                "reason": "llm_invalid_schema",
                "raw_output": raw,
            }
            return fallback

        seed_items = _selector_seed_items(parsed, primary_section)
        chosen = []
        seen_pairs = set()
        for item in seed_items:
            _append_model_section(chosen, seen_pairs, item["model"], item["section"])
            if len(chosen) >= SELECTOR_MAX_PANEL_SIZE:
                break

        if len(chosen) < 2:
            fallback = _heuristic_suggest_models_for_question(question)
            fallback["_selector_meta"] = {
                "source": "heuristic",
                "reason": "llm_insufficient_models",
                "raw_output": raw,
            }
            return fallback

        return {
            "section": primary_section,
            "models": chosen,
            "_selector_meta": {
                "source": selector_source,
                "reason": selector_reason,
                "raw_output": raw,
            },
        }
    except Exception as e:
        fallback = _heuristic_suggest_models_for_question(question)
        fallback["_selector_meta"] = {
            "source": "heuristic",
            "reason": f"llm_exception: {str(e)}",
            "raw_output": None,
        }
        return fallback
