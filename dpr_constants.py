from pathlib import Path

from dotenv import load_dotenv

API_URL = "https://api.groq.com/openai/v1/chat/completions"

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AVAILABLE_MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "groq/compound-mini",
    "openai/gpt-oss-safeguard-20b",
    "qwen/qwen3-32b",
    "moonshotai/kimi-k2-instruct",
]

DEFAULT_AGENT_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "openai/gpt-oss-20b",
]

SECTION_HEADERS = {
    "general": "You are answering with a general problem-solving focus.",
    "education": "You are answering with an education-focused perspective (clarity, pedagogy, learning outcomes).",
    "programming": "You are answering with a programming-focused perspective (implementation, architecture, code quality).",
    "research": "You are answering with a research/analysis perspective (evidence, tradeoffs, rigor).",
}
DEFAULT_SECTION = "general"

MAX_TURNS = 60
STARTING_QUOTA = 8
REDIRECT_DURATION_TURNS = 3
STARVATION_THRESHOLD = 5
LOOP_WINDOW = 4
MAX_REPEAT_STREAK = 2
HUMAN_NAME = "Human"

MEMORY_MODEL = "groq/compound"
MEMORY_SECTION_LIMIT = 12
MEMORY_CHANGELOG_LIMIT = 20
RECENT_TURNS_IN_CONTEXT = 2
MAX_SUMMARY_TEXT_CHARS = 220
MEMORY_CALL_DELAY_SECONDS = 1.0
MEMORY_ITEM_CHAR_LIMIT = 170
MEMORY_SIMILARITY_THRESHOLD = 0.86
MIN_PERSISTENT_OPEN_QUESTIONS = 2

SECTION_PRIORITY_HINTS = {
    "facts": ["fact", "assumption", "constraint", "baseline", "capacity", "requirement"],
    "options": ["option", "proposal", "approach", "design", "architecture", "alternative"],
    "decisions": ["decision", "selected", "locked", "final", "chosen", "approved"],
    "open_questions": ["?", "open question", "unknown", "risk", "unclear", "investigate"],
    "actions": ["action", "next step", "owner", "deadline", "implement", "generate", "run"],
}

DECISION_START_VERBS = {
    "deploy",
    "use",
    "adopt",
    "select",
    "choose",
    "implement",
    "prioritize",
    "standardize",
    "lock",
    "baseline",
    "mandate",
    "assign",
    "establish",
}
