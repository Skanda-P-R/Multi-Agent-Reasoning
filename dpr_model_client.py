import os

import requests

from dpr_constants import (
    BROADCAST_MODEL_MAP,
    GROQ_API_URL,
    OPEN_ROUTER_API_URL,
    OPEN_ROUTER_AVAILABLE_MODELS,
)


GROQ_PROVIDER = "groq"
OPEN_ROUTER_PROVIDER = "openrouter"
BROADCAST_PROVIDER = "broadcast"


def get_broadcast_model(model):
    return BROADCAST_MODEL_MAP.get(model, model)


def _env_value(*names):
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _resolve_provider_and_model(model, provider):
    provider = (provider or GROQ_PROVIDER).strip().lower()
    if provider == BROADCAST_PROVIDER:
        provider = OPEN_ROUTER_PROVIDER
        model = get_broadcast_model(model)
    elif provider == "auto":
        if model in OPEN_ROUTER_AVAILABLE_MODELS or model.endswith(":free") or model.startswith("openrouter/"):
            provider = OPEN_ROUTER_PROVIDER
        else:
            provider = GROQ_PROVIDER

    if provider not in {GROQ_PROVIDER, OPEN_ROUTER_PROVIDER}:
        raise ValueError(f"Unsupported model provider: {provider}")

    return provider, model


# --------------------------------------------
# MODEL API CALLER
# --------------------------------------------
def call_model(
    model,
    messages,
    max_tokens=None,
    temperature=0.6,
    provider=GROQ_PROVIDER,
    response_format=None,
):
    provider, routed_model = _resolve_provider_and_model(model, provider)
    if provider == OPEN_ROUTER_PROVIDER:
        api_key = _env_value("OPEN_ROUTER_API_KEY", "OPENROUTER_API_KEY")
        api_url = OPEN_ROUTER_API_URL
        provider_label = "OPEN_ROUTER"
    else:
        api_key = _env_value("GROQ_API_KEY")
        api_url = GROQ_API_URL
        provider_label = "GROQ"

    if not api_key:
        raise RuntimeError(f"Missing {provider_label}_API_KEY environment variable.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if provider == OPEN_ROUTER_PROVIDER:
        site_url = _env_value("OPEN_ROUTER_SITE_URL", "OPENROUTER_SITE_URL")
        app_name = _env_value("OPEN_ROUTER_APP_NAME", "OPENROUTER_APP_NAME") or "DPR Multi-Agent Reasoning"
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_name:
            headers["X-Title"] = app_name

    payload = {
        "model": routed_model,
        "messages": messages,
        "temperature": temperature,
    }

    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_format is not None:
        payload["response_format"] = response_format

    r = requests.post(api_url, headers=headers, json=payload)
    r.raise_for_status()

    msg = r.json()["choices"][0]["message"]
    return msg.get("content", "")
