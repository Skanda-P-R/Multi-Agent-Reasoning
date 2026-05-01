import json
import os
import re

import requests

from dpr_constants import (
    API_URL,
    AVAILABLE_MODELS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    MEMORY_MODEL,
    SECTION_HEADERS,
)


def _extract_json_object_loose(raw_text):
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


def _heuristic_suggest_models_for_question(question):
    q = (question or "").lower()

    programming_hits = any(
        k in q
        for k in [
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
        ]
    )
    education_hits = any(
        k in q
        for k in [
            "teach",
            "education",
            "student",
            "curriculum",
            "lesson",
            "learning",
            "classroom",
            "school",
            "exam",
        ]
    )

    if programming_hits and not education_hits:
        section = "programming"
        model_order = [
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
            "moonshotai/kimi-k2-instruct",
            "openai/gpt-oss-20b",
        ]
    elif education_hits and not programming_hits:
        section = "education"
        model_order = [
            "llama-3.3-70b-versatile",
            "moonshotai/kimi-k2-instruct",
            "openai/gpt-oss-20b",
            "llama-3.1-8b-instant",
        ]
    else:
        section = "general"
        model_order = list(DEFAULT_AGENT_MODELS)

    chosen = []
    seen = set()
    for model in model_order:
        if model in AVAILABLE_MODELS and model not in seen:
            chosen.append({"model": model, "section": section})
            seen.add(model)
        if len(chosen) >= 4:
            break

    if len(chosen) < 2:
        for model in AVAILABLE_MODELS:
            if model not in seen:
                chosen.append({"model": model, "section": section})
                seen.add(model)
            if len(chosen) >= 2:
                break

    return {
        "section": section,
        "models": chosen,
        "_selector_meta": {
            "source": "heuristic",
            "reason": "keyword_fallback",
            "raw_output": None,
        },
    }


def _call_selector_model_json(question, retry=False):
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY environment variable.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    schema_hint = {
        "section": "general|education|programming|research",
        "models": [{"model": "<allowed model>", "section": "general|education|programming|research"}],
    }

    system_prompt = (
        "You are ONLY a model-router. "
        "Do NOT answer the user's question content. "
        "Return ONLY one JSON object with keys: section, models. "
        "No markdown. No prose."
    )
    if retry:
        system_prompt += " PRIOR ATTEMPT FAILED. STRICT JSON OBJECT ONLY."

    user_prompt = (
        "Select a panel for multi-agent discussion.\n"
        f"Allowed models: {json.dumps(AVAILABLE_MODELS)}\n"
        f"Allowed sections: {json.dumps(list(SECTION_HEADERS.keys()))}\n"
        "Rules: choose a dynamic number of panelists based on task complexity "
        "(min 2, max 12). You MAY reuse the same model in different sections if useful. "
        "Avoid exact duplicate entries of the same model+section. "
        "Use mixed sections for multi-domain questions.\n"
        f"Question: {question}\n"
        f"Output schema: {json.dumps(schema_hint)}"
    )

    payload = {
        "model": MEMORY_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": 420,
        "response_format": {"type": "json_object"},
    }

    r = requests.post(API_URL, headers=headers, json=payload)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return msg.get("content", "")


def suggest_models_for_question(question):
    question = (question or "").strip()
    if not question:
        return _heuristic_suggest_models_for_question(question)

    try:
        raw = _call_selector_model_json(question, retry=False)
        parsed = _extract_json_object_loose(raw)
        if not isinstance(parsed, dict):
            raw_retry = _call_selector_model_json(question, retry=True)
            raw = raw_retry
            parsed = _extract_json_object_loose(raw_retry)
            if not isinstance(parsed, dict):
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

        raw_models = parsed.get("models", [])
        if not isinstance(raw_models, list):
            fallback = _heuristic_suggest_models_for_question(question)
            fallback["_selector_meta"] = {
                "source": "heuristic",
                "reason": "llm_invalid_schema",
                "raw_output": raw,
            }
            return fallback

        chosen = []
        seen_pairs = set()
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model = str(item.get("model", "")).strip()
            section = str(item.get("section", primary_section)).strip().lower() or primary_section
            if model not in AVAILABLE_MODELS:
                continue
            if section not in SECTION_HEADERS:
                section = primary_section
            pair = (model, section)
            if pair in seen_pairs:
                continue
            chosen.append({"model": model, "section": section})
            seen_pairs.add(pair)
            if len(chosen) >= 12:
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
                "source": "llm",
                "reason": "ok",
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
