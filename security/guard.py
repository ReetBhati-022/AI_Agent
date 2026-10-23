"""Approval gate for risky changes.

Safe actions (creating something new) run immediately. Risky ones (overwrite, append, replace, rename, move,
delete) are only *staged* when the agent calls the tool: nothing happens until the person clicks Approve in the
Streamlit UI, which calls approve() below. The model has no tool that can approve, so it cannot bypass this.
Set modify.confirm_changes: false in config.yaml to run everything immediately.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime

from security.permissions import PermissionDenied
from security.settings import ROOT, get_settings
from tools.common import fail, ok

_LOCK = threading.Lock()
_AUDIT_LOCK = threading.Lock()
ACTIONS: dict = {}   # id -> {tool, description, action, created, status, result}


def _cfg() -> dict:
    return get_settings().get("modify", {})


def confirmations_required() -> bool:
    return bool(_cfg().get("confirm_changes", True))


# ---------------------------------------------------------------- audit log (append-only JSON lines)

def _audit_cfg() -> dict:
    return get_settings().get("audit", {})


def audit_path() -> str:
    name = str(_audit_cfg().get("file", "logs/audit.jsonl"))
    return name if os.path.isabs(name) else str(ROOT / name)


def audit(tool: str, status: str, description: str = "", path: str = "", detail: str = "") -> None:
    """Append one line to the audit log. Never raises: a logging problem must not break a file action.

    status: done | failed | pending | approved | rejected | expired | blocked | external_upload | clipboard_read ..."""
    if not _audit_cfg().get("enabled", True):
        return
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "tool": tool, "status": status,
             "description": str(description)[:500], "path": str(path)[:500], "detail": str(detail)[:500]}
    try:
        target = audit_path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with _AUDIT_LOCK, open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_audit(limit: int = 50, tool: str = "", status: str = "", days: int = 0) -> list:
    """Newest-first list of audit entries (dicts), optionally filtered."""
    target = audit_path()
    if not os.path.isfile(target):
        return []
    cutoff = None
    if days and days > 0:
        cutoff = datetime.fromtimestamp(time.time() - days * 86400).isoformat(timespec="seconds")
    entries = []
    with _AUDIT_LOCK, open(target, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if tool and item.get("tool") != tool:
                continue
            if status and item.get("status") != status:
                continue
            if cutoff and item.get("time", "") < cutoff:
                continue
            entries.append(item)
    entries.reverse()
    return entries[:max(1, limit)]


def _result_summary(raw: str):
    """(status, detail) from a tool result JSON string."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "done", ""
    if data.get("ok"):
        rows = data.get("rows") or []
        detail = rows[0].get("detail", "") if rows and isinstance(rows[0], dict) else ""
        return "done", detail
    return "failed", str(data.get("error", ""))


def row(action: str, path: str, detail: str = "") -> dict:
    return {"action": action, "path": path, "detail": detail}


def _run(tool: str, action) -> dict:
    try:
        return json.loads(action())
    except PermissionDenied as exc:
        return json.loads(fail(tool, str(exc)))
    except PermissionError:
        return json.loads(fail(tool, "Windows denied access (the file may be open in another program)."))
    except Exception as exc:  # noqa: BLE001
        return json.loads(fail(tool, f"Unexpected error: {type(exc).__name__}: {exc}"))


def _purge():
    default_ttl = float(_cfg().get("pending_ttl_minutes", 15)) * 60
    now = time.time()
    with _LOCK:
        for item in ACTIONS.values():
            ttl = item.get("ttl", default_ttl)
            if item["status"] == "pending" and now - item["created"] > ttl:
                item["status"] = "expired"
                audit(item["tool"], "expired", item["description"], "", "not approved in time")
        if len(ACTIONS) > 200:  # keep memory small
            for pid in sorted(ACTIONS, key=lambda k: ACTIONS[k]["created"])[:100]:
                ACTIONS.pop(pid, None)


def submit(tool: str, description: str, risky: bool, action, path: str = "", required=None) -> str:
    """Run `action()` now (returns a JSON string) or stage it for approval.

    `required` overrides modify.confirm_changes (the computer tools pass computer.confirm_actions)."""
    needs_approval = confirmations_required() if required is None else bool(required)
    if not risky or not needs_approval:
        result = action()
        status_, detail = _result_summary(result)
        audit(tool, status_, description, path, detail)
        return result
    _purge()
    pid = uuid.uuid4().hex[:10]
    with _LOCK:
        ACTIONS[pid] = {"tool": tool, "description": description, "action": action,
                        "created": time.time(), "status": "pending", "result": None, "origin": "chat"}
    audit(tool, "pending", description, path, f"request {pid}")
    rows = [row(description, path, "waiting for your approval")]
    notes = ["NOTHING has been changed yet. The user must click Approve in the app. Do not retry this call."]
    return ok(tool, rows, notes, pending_id=pid, description=description)


def status(pid: str):
    """Public view of a staged action (or None if unknown, e.g. after an app restart)."""
    _purge()
    with _LOCK:
        item = ACTIONS.get(pid)
        if not item:
            return None
        return {k: item[k] for k in ("tool", "description", "status", "result", "origin")}


def annotate(pid: str, origin: str, ttl_minutes: float) -> None:
    """Used by the scheduler (not by the model): label a still-pending action with where it came from and give it
    a longer expiry, since nobody was watching. It stays pending: only approve() can run it."""
    with _LOCK:
        item = ACTIONS.get(pid)
        if item and item["status"] == "pending":
            item["origin"] = origin
            item["ttl"] = float(ttl_minutes) * 60
            item["created"] = time.time()


def list_pending(origin_prefix: str = "") -> list:
    """Pending actions, newest first (for the approval inbox)."""
    _purge()
    with _LOCK:
        rows = [{"pending_id": pid, "tool": i["tool"], "description": i["description"], "origin": i.get("origin", "chat"),
                 "created": i["created"]} for pid, i in ACTIONS.items()
                if i["status"] == "pending" and i.get("origin", "chat").startswith(origin_prefix)]
    return sorted(rows, key=lambda r: r["created"], reverse=True)


def approve(pid: str) -> dict:
    """Called by the UI when the person clicks Approve. Re-runs all permission checks inside the action."""
    _purge()
    with _LOCK:
        item = ACTIONS.get(pid)
        if not item:
            return {"ok": False, "error": "This request expired or the app was restarted. Ask again."}
        if item["status"] != "pending":
            return item["result"] or {"ok": False, "error": f"This request is already {item['status']}."}
        item["status"] = "running"
        action, tool = item["action"], item["tool"]
    result = _run(tool, action)
    with _LOCK:
        item["status"] = "done" if result.get("ok") else "failed"
        item["result"] = result
    rows = result.get("rows") or []
    detail = (rows[0].get("detail", "") if rows and isinstance(rows[0], dict) else "") or result.get("error", "")
    audit(tool, "approved" if result.get("ok") else "failed", item["description"], "", detail)
    return result


def reject(pid: str):
    with _LOCK:
        item = ACTIONS.get(pid)
        if item and item["status"] == "pending":
            item["status"] = "rejected"
            audit(item["tool"], "rejected", item["description"], "", "rejected by the user")