"""LLMWhisperer (Unstract) client wrapper. Cloud OCR / layout-preserving text extraction.

PRIVACY: the file is uploaded to the service. This module is only reached through extract_text(engine='whisperer'),
which is off by default (whisperer.enabled) and always waits for the person's Approve click."""
import os

from security.permissions import PermissionDenied
from security.settings import get_settings


def cfg() -> dict:
    return get_settings().get("whisperer", {})


def api_key() -> str:
    return os.getenv("LLMWHISPERER_API_KEY", "").strip()


def base_url() -> str:
    return (os.getenv("LLMWHISPERER_BASE_URL_V2", "").strip() or str(cfg().get("base_url", "")).strip()
            or "https://llmwhisperer-api.us-central.unstract.com/api/v2")


def ready() -> bool:
    return bool(cfg().get("enabled", False)) and bool(api_key())


def check_ready():
    if not cfg().get("enabled", False):
        raise PermissionDenied("LLMWhisperer is turned off (whisperer.enabled: false in config.yaml). "
                               "It uploads the file to a cloud service, so it is off by default.")
    if not api_key():
        raise PermissionDenied("LLMWHISPERER_API_KEY is not set. Add it to your .env file and restart the app.")


def extract(path: str, pages: str = "") -> tuple:
    """Upload `path` and return (text, notes). Raises RuntimeError with a readable message on failure."""
    try:
        from unstract.llmwhisperer import LLMWhispererClientV2
        from unstract.llmwhisperer.client_v2 import LLMWhispererClientException
    except ImportError as exc:
        raise RuntimeError("Package 'llmwhisperer-client' is not installed. Run: python -m pip install llmwhisperer-client") from exc

    c = cfg()
    client = LLMWhispererClientV2(base_url=base_url(), api_key=api_key(), logging_level="ERROR")
    try:
        reply = client.whisper(
            file_path=path,
            mode=str(c.get("mode", "form")),
            output_mode=str(c.get("output_mode", "layout_preserving")),
            lang=str(c.get("language", "eng")),
            pages_to_extract=(pages or "").strip(),
            wait_for_completion=True,
            wait_timeout=int(c.get("timeout_seconds", 180)),
        )
    except LLMWhispererClientException as exc:
        raise RuntimeError(f"LLMWhisperer error: {str(exc)[:300]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"LLMWhisperer request failed ({type(exc).__name__}): {str(exc)[:300]}") from exc

    status = reply.get("status")
    if reply.get("status_code") != 200 or status not in ("processed", None):
        raise RuntimeError(f"LLMWhisperer did not finish: {reply.get('message') or status}")
    extraction = reply.get("extraction") or {}
    text = extraction.get("result_text") or ""
    notes = [f"Extracted by LLMWhisperer (mode {c.get('mode', 'form')}, {c.get('output_mode', 'layout_preserving')})."]
    return text, notes
