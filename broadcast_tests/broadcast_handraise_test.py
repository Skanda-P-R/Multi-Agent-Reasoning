import argparse
import json
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dpr_constants import DEFAULT_AGENT_MODELS, DEFAULT_SECTION  # noqa: E402
from dpr_model_client import call_model  # noqa: E402


def extract_json_object_loose(raw_text):
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


def normalize_raise_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"yes", "true", "1", "raise", "raised"}


def normalize_priority(value):
    if value is None:
        return 3, "medium"

    text = str(value).strip().lower()
    map_named = {"high": 1, "medium": 2, "low": 3}
    if text in map_named:
        return map_named[text], text

    match = re.search(r"\d+", text)
    if match:
        n = int(match.group(0))
        n = max(1, min(5, n))
        return n, f"p{n}"

    return 3, "medium"


def fallback_extract_intent(raw_text):
    text = (raw_text or "").strip()

    raise_match = re.search(r"hand[_\s-]?raise\s*[:=]\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    pointer_match = re.search(r"pointer\s*[:=]\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    priority_match = re.search(r"priority\s*[:=]\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    value_add_match = re.search(r"value[_\s-]?add\s*[:=]\s*([^\n\r]+)", text, flags=re.IGNORECASE)

    hand_raise = normalize_raise_bool(raise_match.group(1) if raise_match else "no")
    priority_rank, priority_label = normalize_priority(priority_match.group(1) if priority_match else None)
    pointer = (pointer_match.group(1).strip() if pointer_match else "").strip()
    value_add = (value_add_match.group(1).strip() if value_add_match else "").strip()

    if not pointer:
        first_line = text.splitlines()[0].strip() if text else ""
        pointer = first_line[:180]

    return {
        "hand_raise": hand_raise,
        "priority_rank": priority_rank,
        "priority_label": priority_label,
        "pointer": pointer or "(missing pointer)",
        "value_add": value_add or "(not provided)",
        "parse_source": "fallback_text",
    }


def salvage_from_jsonish_text(raw_text):
    text = (raw_text or "").strip()
    if not text:
        return None

    # Recover common fields even when JSON is truncated/malformed.
    hand_raise = None
    priority_raw = None
    pointer = ""
    value_add = ""
    confidence = 0.0

    m_raise = re.search(r'"hand_raise"\s*:\s*(true|false|"[^"]+"|[a-zA-Z]+)', text, flags=re.IGNORECASE)
    if m_raise:
        token = m_raise.group(1).strip().strip('"')
        hand_raise = normalize_raise_bool(token)

    m_priority = re.search(r'"priority"\s*:\s*("([^"]*)"|[0-9]+)', text, flags=re.IGNORECASE)
    if m_priority:
        priority_raw = m_priority.group(2) if m_priority.group(2) is not None else m_priority.group(1)
        priority_raw = str(priority_raw).strip('"')

    m_pointer = re.search(r'"pointer"\s*:\s*"([^"]*)', text, flags=re.IGNORECASE)
    if m_pointer:
        pointer = m_pointer.group(1).strip()

    m_value = re.search(r'"value_add"\s*:\s*"([^"]*)', text, flags=re.IGNORECASE)
    if m_value:
        value_add = m_value.group(1).strip()

    m_conf = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', text, flags=re.IGNORECASE)
    if m_conf:
        try:
            confidence = float(m_conf.group(1))
        except ValueError:
            confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    has_any = any([
        hand_raise is not None,
        priority_raw is not None,
        bool(pointer),
        bool(value_add),
        confidence > 0.0,
    ])
    if not has_any:
        return None

    priority_rank, priority_label = normalize_priority(priority_raw)
    return {
        "hand_raise": bool(hand_raise) if hand_raise is not None else False,
        "priority_rank": priority_rank,
        "priority_label": priority_label,
        "pointer": pointer or "(missing pointer)",
        "value_add": value_add or "(not provided)",
        "confidence": confidence,
        "parse_source": "salvaged_jsonish",
    }


def parse_intent(raw_text):
    parsed = extract_json_object_loose(raw_text)
    if isinstance(parsed, dict):
        hand_raise = normalize_raise_bool(parsed.get("hand_raise", False))
        priority_rank, priority_label = normalize_priority(parsed.get("priority"))
        pointer = str(parsed.get("pointer", "")).strip() or "(missing pointer)"
        value_add = str(parsed.get("value_add", "")).strip() or "(not provided)"
        confidence = parsed.get("confidence")
        try:
            confidence_num = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            confidence_num = 0.0
        confidence_num = max(0.0, min(1.0, confidence_num))
        return {
            "hand_raise": hand_raise,
            "priority_rank": priority_rank,
            "priority_label": priority_label,
            "pointer": pointer,
            "value_add": value_add,
            "confidence": confidence_num,
            "parse_source": "json",
        }
    salvaged = salvage_from_jsonish_text(raw_text)
    if salvaged is not None:
        return salvaged
    fallback = fallback_extract_intent(raw_text)
    fallback["confidence"] = 0.0
    return fallback


def lexical_novelty_score(pointer, recent_turns):
    ptr = (pointer or "").strip().lower()
    if not ptr:
        return 0.0
    ptr_tokens = set(re.findall(r"[a-z0-9]+", ptr))
    if not ptr_tokens:
        return 0.0

    recent_text = " ".join((t.get("text", "") for t in (recent_turns or []))).lower()
    recent_tokens = set(re.findall(r"[a-z0-9]+", recent_text))
    if not recent_tokens:
        return 1.0

    overlap = len(ptr_tokens & recent_tokens)
    novelty = 1.0 - (overlap / max(1, len(ptr_tokens)))
    return round(max(0.0, min(1.0, novelty)), 3)


def build_intent_prompt(agent_name, section, question, shared_memory, recent_turns):
    memory_json = json.dumps(shared_memory, ensure_ascii=True)
    recent_json = json.dumps(recent_turns, ensure_ascii=True)
    return f"""
You are {agent_name} with section focus "{section}" in a multi-agent reasoning protocol.

Question:
{question}

Shared memory:
{memory_json}

Recent accepted turns:
{recent_json}

Decide if you should raise hand NOW.
Return ONLY JSON object with this schema:
{{
  "hand_raise": true/false,
  "priority": "high|medium|low OR 1..5",
  "pointer": "one specific gap/decision/risk you will address",
  "value_add": "what new contribution you can add",
  "confidence": 0.0 to 1.0
}}

Rules:
- No markdown.
- No explanation outside JSON.
- Raise hand only if you can add new non-duplicate value.
""".strip()


def build_contribution_prompt(agent_name, section, question, shared_memory, pointer):
    memory_block = json.dumps(shared_memory, ensure_ascii=True)
    return f"""
You are {agent_name} with section focus "{section}".

Question:
{question}

Shared memory:
{memory_block}

Your pointer to address:
{pointer}

Instructions:
- Provide normal reasoning output in plain text.
- Focus on resolving the pointer with concrete contribution.
- Avoid repeating previous ideas.
""".strip()


def run_broadcast_cycle(
    question,
    agents,
    shared_memory,
    recent_turns,
    run_contribution,
    show_selected_only=False,
    cycle_index=1,
):
    print(f"\n=== Broadcast Intent Phase (Cycle {cycle_index}) ===")
    intents = []

    for agent in agents:
        prompt = build_intent_prompt(
            agent_name=agent["name"],
            section=agent["section"],
            question=question,
            shared_memory=shared_memory,
            recent_turns=recent_turns,
        )
        messages = [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ]

        raw = ""
        attempts = []
        max_attempts = 3
        for attempt_idx in range(max_attempts):
            t0 = time.time()
            try:
                candidate = call_model(agent["model"], messages, max_tokens=320, temperature=0.1)
                elapsed = round(time.time() - t0, 2)
                candidate = (candidate or "").strip()
                attempts.append({
                    "attempt": attempt_idx + 1,
                    "status": "ok",
                    "elapsed_sec": elapsed,
                    "chars": len(candidate),
                })
                if candidate:
                    raw = candidate
                    break
                messages = [
                    {"role": "system", "content": "Return only a minified JSON object. No prose."},
                    {"role": "user", "content": prompt},
                ]
            except Exception as e:
                elapsed = round(time.time() - t0, 2)
                attempts.append({
                    "attempt": attempt_idx + 1,
                    "status": "error",
                    "elapsed_sec": elapsed,
                    "error": str(e),
                })

        extracted = parse_intent(raw)
        intents.append({
            "agent": agent,
            "raw": raw,
            "extracted": extracted,
            "attempts": attempts,
        })

    if not show_selected_only:
        for item in intents:
            name = item["agent"]["name"]
            model = item["agent"]["model"]
            print(f"\n--- {name} ({model}) call status ---")
            print(json.dumps(item["attempts"], indent=2, ensure_ascii=True))
            print(f"\n--- {name} ({model}) raw response ---")
            print(item["raw"] if item["raw"] else "(empty response after retries)")
            print(f"--- {name} extracted ---")
            print(json.dumps(item["extracted"], indent=2, ensure_ascii=True))

    raised = [i for i in intents if i["extracted"]["hand_raise"]]
    for item in raised:
        item["extracted"]["novelty"] = lexical_novelty_score(item["extracted"]["pointer"], recent_turns)

    raised.sort(
        key=lambda i: (
            i["extracted"]["priority_rank"],
            -float(i["extracted"].get("confidence", 0.0)),
            -float(i["extracted"].get("novelty", 0.0)),
            0 if i["extracted"]["parse_source"] == "json" else 1,
            i["agent"]["name"],
        )
    )

    print(f"\n=== Queue Built From Self-Raised Hands (Cycle {cycle_index}) ===")
    if not raised:
        print("No agents raised hand in this cycle.")
        return {
            "cycle": cycle_index,
            "intents": intents,
            "raised_queue": [],
            "selected": None,
            "contribution": None,
        }

    for idx, item in enumerate(raised, start=1):
        ex = item["extracted"]
        print(
            f"{idx}. {item['agent']['name']} | priority={ex['priority_label']} "
            f"| confidence={ex.get('confidence', 0.0):.2f} "
            f"| novelty={ex.get('novelty', 0.0):.2f} "
            f"| pointer={ex['pointer']} | parse={ex['parse_source']}"
        )

    if not run_contribution:
        return {
            "cycle": cycle_index,
            "intents": intents,
            "raised_queue": raised,
            "selected": None,
            "contribution": None,
        }

    selected = raised[0]
    selected_agent = selected["agent"]
    pointer = selected["extracted"]["pointer"]
    print("\n=== Contribution Phase ===")
    print(f"Selected: {selected_agent['name']} ({selected_agent['model']})")
    print(f"Pointer: {pointer}")

    contribution_prompt = build_contribution_prompt(
        agent_name=selected_agent["name"],
        section=selected_agent["section"],
        question=question,
        shared_memory=shared_memory,
        pointer=pointer,
    )
    messages = [
        {"role": "system", "content": contribution_prompt},
        {"role": "user", "content": "Continue reasoning."},
    ]
    try:
        contribution = call_model(selected_agent["model"], messages, max_tokens=500, temperature=0.6)
    except Exception as e:
        contribution = f"[contribution_error] {str(e)}"
        print("\n--- Contribution Output ---")
        print(contribution)
        return {
            "cycle": cycle_index,
            "intents": intents,
            "raised_queue": raised,
            "selected": selected,
            "contribution": contribution,
        }
    print("\n--- Contribution Output ---")
    print(contribution)
    return {
        "cycle": cycle_index,
        "intents": intents,
        "raised_queue": raised,
        "selected": selected,
        "contribution": contribution,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Broadcast shared context to all agents, collect self-raised hand intents, and debug extraction."
    )
    parser.add_argument("--question", required=True, help="Question/prompt to reason about.")
    parser.add_argument(
        "--models",
        nargs="*",
        default=list(DEFAULT_AGENT_MODELS),
        help="Model list for agents (default: DEFAULT_AGENT_MODELS).",
    )
    parser.add_argument(
        "--run-contribution",
        action="store_true",
        help="Run contribution phase for top queued self-raised agent.",
    )
    parser.add_argument(
        "--show-selected-only",
        action="store_true",
        help="Reduce log verbosity by printing only queue + selected/contribution outputs.",
    )
    parser.add_argument(
        "--save-json",
        default="",
        help="Optional output path to save full debug artifact JSON.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Number of broadcast cycles to run (default: 1).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    agents = [
        {"name": f"Agent {idx + 1}", "model": model, "section": DEFAULT_SECTION}
        for idx, model in enumerate(args.models)
    ]

    # Seed test memory to mimic DPR context.
    shared_memory = {
        "facts": ["Target is fast prototype validation for hand-raise behavior."],
        "options": ["Use broadcast intent pass before normal contribution."],
        "decisions": ["Need to compare raw model replies vs parsed extraction."],
        "open_questions": ["Will agents follow strict JSON consistently?"],
        "actions": ["Run isolated test before integrating into main protocol."],
        "changelog": [],
    }
    recent_turns = [
        {"agent": "Human", "text": "Focus on outcome-driven contributions, not turn fairness."}
    ]

    cycles = max(1, int(args.cycles))
    history = []
    selected_counts = {a["name"]: 0 for a in agents}

    for cycle_idx in range(1, cycles + 1):
        result = run_broadcast_cycle(
            question=args.question,
            agents=agents,
            shared_memory=shared_memory,
            recent_turns=recent_turns,
            run_contribution=args.run_contribution,
            show_selected_only=args.show_selected_only,
            cycle_index=cycle_idx,
        )
        history.append(result)

        selected = result.get("selected")
        contribution = result.get("contribution")
        if selected:
            selected_agent = selected["agent"]["name"]
            selected_counts[selected_agent] = selected_counts.get(selected_agent, 0) + 1
            pointer = selected["extracted"]["pointer"]
            shared_memory["actions"].append(f"Cycle {cycle_idx}: {selected_agent} pointer -> {pointer}")
            shared_memory["actions"] = shared_memory["actions"][-12:]
            if contribution:
                recent_turns.append({"agent": selected_agent, "text": contribution})
                recent_turns[:] = recent_turns[-4:]

    print("\n=== Multi-Cycle Summary ===")
    for agent_name, count in selected_counts.items():
        print(f"{agent_name}: selected {count} time(s)")

    if args.save_json:
        out_path = Path(args.save_json)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        serializable_history = []
        for h in history:
            intents_out = []
            for i in h.get("intents", []):
                intents_out.append({
                    "agent": i["agent"],
                    "attempts": i.get("attempts", []),
                    "raw": i.get("raw", ""),
                    "extracted": i.get("extracted", {}),
                })
            queue_out = [
                {
                    "agent": q["agent"],
                    "extracted": q["extracted"],
                }
                for q in h.get("raised_queue", [])
            ]
            selected = h.get("selected")
            selected_out = None
            if selected:
                selected_out = {
                    "agent": selected["agent"],
                    "extracted": selected["extracted"],
                }
            serializable_history.append({
                "cycle": h.get("cycle"),
                "intents": intents_out,
                "raised_queue": queue_out,
                "selected": selected_out,
                "contribution": h.get("contribution"),
            })

        artifact = {
            "question": args.question,
            "agents": agents,
            "cycles": cycles,
            "run_contribution": bool(args.run_contribution),
            "summary": {
                "selected_counts": selected_counts,
            },
            "history": serializable_history,
        }
        out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=True), encoding="utf-8")
        print(f"\nSaved debug artifact: {out_path}")


if __name__ == "__main__":
    main()
