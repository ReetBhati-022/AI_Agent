"""Shared helpers: JSON result shape, error handling, formatting."""
import functools
import json
import re
from datetime import datetime

from security.permissions import PermissionDenied
from security.settings import get_settings


def limits() -> dict:
    return get_settings()["limits"]


def clamp_results(value, default: int = 100) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, limits()["max_results"]))


def human_size(num) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def parse_extensions(text) -> set:
    parts = [p.strip().lstrip("*").lower() for p in re.split(r"[,; ]+", text or "") if p.strip()]
    return {p if p.startswith(".") else "." + p for p in parts if p not in ("", ".")}


def file_row(path: str, stat) -> dict:
    name = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name.strip(".") else ""
    return {"name": name, "extension": ext, "size": human_size(stat.st_size),
            "modified": iso(stat.st_mtime), "path": path}


def ok(tool: str, rows: list, notes: list = None, **extra) -> str:
    payload = {"tool": tool, "ok": True, "count": len(rows), "rows": rows, "notes": notes or []}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def fail(tool: str, message: str) -> str:
    return json.dumps({"tool": tool, "ok": False, "error": message}, ensure_ascii=False)


def guarded(tool_name: str):
    """Turn any failure into a clean JSON error instead of crashing the agent."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except PermissionDenied as exc:
                return fail(tool_name, str(exc))
            except PermissionError:
                return fail(tool_name, "Windows denied access to this location.")
            except Exception as exc:  # noqa: BLE001
                return fail(tool_name, f"Unexpected error: {type(exc).__name__}: {exc}")
        return wrapper
    return decorator
