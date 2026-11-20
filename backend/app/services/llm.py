"""LLM provider layer. Every provider is free: Gemini free tier, or a local Ollama model."""

import json
import os
import time
import urllib.error
import urllib.request

PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # gemini | ollama
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
TIMEOUT_S = 30
RETRIES = 2


class LLMError(RuntimeError):
    pass


def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # 429 = free-tier rate limit, 5xx = provider hiccup: back off and retry; anything else is final.
            if e.code in (429, 500, 503) and attempt < RETRIES:
                time.sleep(2 ** (attempt + 1))
                continue
            if e.code == 429:
                raise LLMError("Free-tier rate limit or daily quota reached. Try again later.") from e
            raise LLMError(f"LLM provider returned HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < RETRIES:
                time.sleep(2 ** (attempt + 1))
                continue
            raise LLMError(f"Could not reach LLM provider: {e}") from e
    raise AssertionError("unreachable")


def generate(prompt: str, json_mode: bool = False) -> str:
    """json_mode asks the provider to return raw JSON text (still validate it: models can ignore the schema)."""
    if PROVIDER == "ollama":
        body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, **({"format": "json"} if json_mode else {})}
        return _post(f"{OLLAMA_URL}/api/generate", body, {})["response"]

    key = (os.getenv("GEMINI_API_KEY") or "").strip()  # pasted keys often carry stray spaces
    if not key:
        raise LLMError("GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey or set LLM_PROVIDER=ollama.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    data = _post(url, body, {"x-goog-api-key": key})
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Gemini response (possibly blocked): {str(data)[:300]}") from e
