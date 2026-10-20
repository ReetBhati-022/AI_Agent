"""Model configuration (reuses the Ollama endpoint + model from config.yaml / .env)."""
import json
import urllib.request

from langchain_ollama import ChatOllama

from security.settings import get_settings


def get_llm() -> ChatOllama:
    cfg = get_settings()["llm"]
    return ChatOllama(
        model=cfg["model"],
        base_url=cfg["base_url"],
        temperature=cfg["temperature"],
        client_kwargs={"timeout": cfg["timeout_seconds"]},
    )


def check_ollama():
    """Return (level, message) where level is ok / warn / error."""
    cfg = get_settings()["llm"]
    url = cfg["base_url"].rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        return "error", f"Cannot reach Ollama at {cfg['base_url']}: {exc}"
    names = [m.get("name") or m.get("model") for m in data.get("models", [])]
    if cfg["model"] in names:
        return "ok", f"Ollama is running and '{cfg['model']}' is available."
    return "warn", (f"Ollama is running but '{cfg['model']}' is not listed "
                    f"(found: {', '.join(n for n in names if n) or 'none'}).")
