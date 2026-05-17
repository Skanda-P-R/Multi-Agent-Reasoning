from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------
# ENVIRONMENT / API CONFIG
# --------------------------------------------
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPEN_ROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GROQ_AVAILABLE_MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]

OPEN_ROUTER_AVAILABLE_MODELS = [
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/nemotron-nano-9b-v2:free",
]

# User-facing/session models remain Groq model IDs because live turns are
# answered through Groq. Broadcast intent collection can use equivalent
# OpenRouter models to reduce Groq rate-limit pressure.
AVAILABLE_MODELS = list(GROQ_AVAILABLE_MODELS)
BROADCAST_MODEL_MAP = {
    "llama-3.1-8b-instant": "nvidia/nemotron-nano-9b-v2:free",
    "llama-3.3-70b-versatile": "nvidia/nemotron-nano-12b-v2-vl:free",
    "openai/gpt-oss-20b": "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b": "openai/gpt-oss-120b:free",
}

# --------------------------------------------
# DEFAULT PANEL CONFIG
# --------------------------------------------
DEFAULT_AGENT_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

SECTION_HEADERS = {
    "general": "You are answering with a general problem-solving focus.",
    "education": "You are answering with an education-focused perspective (clarity, pedagogy, learning outcomes).",
    "programming": "You are answering with a programming-focused perspective (implementation, architecture, code quality).",
    "research": "You are answering with a research/analysis perspective (evidence, tradeoffs, rigor).",
    "product": "You are answering with a product-focused perspective (user needs, scope, prioritization, adoption).",
    "design": "You are answering with a design/UX perspective (usability, flows, accessibility, interaction quality).",
    "business": "You are answering with a business/strategy perspective (market fit, cost, revenue, positioning).",
    "operations": "You are answering with an operations-focused perspective (process, deployment, reliability, ownership).",
    "security": "You are answering with a security/privacy perspective (threats, data protection, abuse prevention).",
    "ethics": "You are answering with an ethics/policy perspective (fairness, harms, governance, accountability).",
}
DEFAULT_SECTION = "general"

# --------------------------------------------
# PROTOCOL RUNTIME LIMITS
# --------------------------------------------
MAX_TURNS = 100
STARTING_QUOTA = 8
REDIRECT_DURATION_TURNS = 3
STARVATION_THRESHOLD = 6
STARVATION_COOLDOWN_TURNS = 3
LOOP_WINDOW = 4
MAX_REPEAT_STREAK = 2
HUMAN_NAME = "Human"

# --------------------------------------------
# MEMORY / BROADCAST TUNING
# --------------------------------------------
MEMORY_MODEL = "openai/gpt-oss-safeguard-20b"  # Groq model used for summarising.
SELECTOR_MODEL = "openrouter/free"  # OpenRouter model used for panel routing.
SELECTOR_PROVIDER = "openrouter"
SELECTOR_MAX_PANEL_SIZE = 16
SELECTOR_MODELS_PER_SECTION = 4
MEMORY_SECTION_LIMIT = 12
MEMORY_CHANGELOG_LIMIT = 20
RECENT_TURNS_IN_CONTEXT = 2
MAX_SUMMARY_TEXT_CHARS = 220
MEMORY_CALL_DELAY_SECONDS = 0.5
BROADCAST_CALL_DELAY_SECONDS = 0.5
TURN_CALL_DELAY_SECONDS = 0.5
MEMORY_ITEM_CHAR_LIMIT = 170
MEMORY_SIMILARITY_THRESHOLD = 0.86
MIN_PERSISTENT_OPEN_QUESTIONS = 2
MIN_COMPLETION_ACCEPTED_TURNS = 10
MIN_COMPLETION_MEMORY_ITEMS = 18
MIN_COMPLETION_POINTERS_ADDRESSED = 6
MIN_COMPLETION_AGENT_COVERAGE_RATIO = 0.80

# --------------------------------------------
# MEMORY PRIORITY HINTS
# --------------------------------------------
SECTION_PRIORITY_HINTS = {
    "facts": ["fact", "assumption", "constraint", "baseline", "capacity", "requirement"],
    "options": ["option", "proposal", "approach", "design", "architecture", "alternative"],
    "decisions": ["decision", "selected", "locked", "final", "chosen", "approved"],
    "open_questions": ["?", "open question", "unknown", "risk", "unclear", "investigate"],
    "actions": ["action", "next step", "owner", "deadline", "implement", "generate", "run"],
}

# --------------------------------------------
# DECISION INFERENCE SIGNALS
# --------------------------------------------
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

# --------------------------------------------
# LEGACY / SCORING WEIGHTS
# --------------------------------------------
HAND_RAISE_THRESHOLD = 0.40
RELEVANCE_WEIGHT = 0.30
NOVELTY_WEIGHT = 0.25
CONFIDENCE_WEIGHT = 0.20
FAIRNESS_WEIGHT = 0.25
SIMILARITY_CHECK_WINDOW = 3
NOVELTY_SIMILARITY_THRESHOLD = 0.7
