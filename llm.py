"""llm.py: free Groq API transport with polite retry on rate limits (same key as your other agents)."""
from __future__ import annotations

import json
import os
import re
import time

import requests

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class LLMUnavailable(RuntimeError):
    pass


def chat(prompt: str, model: str, max_tokens: int, temperature: float = 0.3) -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not set")
    last = None
    for attempt in range(4):
        resp = requests.post(
            GROQ_ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": temperature, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=180,
        )
        if resp.status_code == 429:                     # free tier rate limit: wait and retry
            wait = min(float(resp.headers.get("retry-after", 15)) + 1, 65)
            last = f"rate limited (waited {wait:.0f}s)"
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    raise RuntimeError(f"Groq kept rate-limiting: {last}")


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(raw[start:end + 1])
