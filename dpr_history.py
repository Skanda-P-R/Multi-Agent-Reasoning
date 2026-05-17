import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "session_history"


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slugify(text, limit=52):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:limit].strip("-") or "session")


def _history_path(history_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", history_id or ""):
        raise ValueError("Invalid history id.")
    return HISTORY_DIR / f"{history_id}.json"


def save_history_document(document):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    saved_at = now_iso()
    history_id = document.get("id") or (
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-"
        f"{_slugify(document.get('question', ''))}-{uuid.uuid4().hex[:8]}"
    )
    document = dict(document)
    document["id"] = history_id
    document["saved_at"] = saved_at
    path = _history_path(history_id)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=True), encoding="utf-8")
    return document


def load_history_document(history_id):
    path = _history_path(history_id)
    if not path.exists():
        raise FileNotFoundError("History file not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def list_history_summaries():
    if not HISTORY_DIR.exists():
        return []

    summaries = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        question = data.get("question", "")
        agents = data.get("agents", []) or []
        responses = data.get("responses", []) or []
        summaries.append({
            "id": data.get("id") or path.stem,
            "saved_at": data.get("saved_at") or "",
            "started_at": data.get("created_at") or "",
            "title": question[:90] + ("..." if len(question) > 90 else ""),
            "question": question,
            "agent_count": len(agents),
            "turn_count": data.get("turn", len(responses)),
            "response_count": len(responses),
            "continuation_of": data.get("continuation_of"),
        })

    summaries.sort(key=lambda item: item.get("saved_at") or item.get("started_at") or "", reverse=True)
    return summaries
