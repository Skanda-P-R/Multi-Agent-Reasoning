import os

import requests

from dpr_constants import API_URL


# --------------------------------------------
# MODEL API CALLER
# --------------------------------------------
def call_model(model, messages, max_tokens=None, temperature=0.6):
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY environment variable.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    r = requests.post(API_URL, headers=headers, json=payload)
    r.raise_for_status()

    msg = r.json()["choices"][0]["message"]
    return msg.get("content", "")
