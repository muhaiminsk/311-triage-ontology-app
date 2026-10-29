"""Tiny LLM client. Default backend is a local Ollama server."""
import os

import requests

BACKEND = os.environ.get("LLM_BACKEND", "ollama")  # "ollama" or "stub" (offline UI testing only)
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


class LLMError(Exception):
    pass


def chat(system: str, user: str) -> str:
    if BACKEND == "stub":
        return "[STUB OUTPUT, not a model response]"
    try:
        r = requests.post(
            f"{HOST}/api/chat",
            json={"model": MODEL, "stream": False, "options": {"temperature": 0},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=300,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except requests.RequestException as e:
        raise LLMError(
            f"Could not get a response from Ollama at {HOST} using model {MODEL}. "
            f"Start Ollama and run: ollama pull {MODEL}. Details: {e}"
        )
