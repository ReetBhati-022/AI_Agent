"""Scheduled agent tasks (APScheduler BackgroundScheduler) wired through the approval gate.

How it stays safe
  * A schedule is just "run this prompt through the SAME LangGraph agent at this time". The agent only reaches the
    disk / shell through the tools, and the tools call security/guard.submit(), so every risky action is *staged*,
    never executed, exactly as in a normal chat. Nothing here can approve anything.
  * Only the person can create / edit / delete schedules (Flask API or the functions below). The agent has no tool
    for it, so a prompt injection cannot schedule itself.
  * Unattended runs are refused when approvals are switched off (modify.confirm_changes / computer.confirm_actions
    false), unless scheduler.allow_without_approval is set to true in config.yaml.
  * Staged actions from a run are tagged with the schedule name and get a longer expiry (scheduler.pending_ttl_minutes)
    so the person can approve them when they come back.
  * Every start / finish / block is written to the audit log (tool = "scheduler").

Jobs live in data/schedules.json (plain JSON, editable). APScheduler only keeps the triggers in memory and they are
rebuilt from that file at start-up, so nothing is pickled.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import tools  # noqa: F401  - load tools first: security.guard and tools import each other
from security import guard, permissions
from security.settings import ROOT, get_settings

_LOCK = threading.RLock()
_SCHEDULER = None
_AGENT_FACTORY = None
_AGENT = None

def _prompt_prefix() -> str:
    today = datetime.now().strftime("%A, %d %B %Y")
    return (
        f"[SCHEDULED RUN - today's date is {today}. Nobody is watching this conversation right now. Do the task below "
        "without asking questions. For anything about news, events or other current information you MUST call web_search "
        "(topic='news') first and report only what the results say, with their dates; never answer from memory or "
        "mention old years as if they were current. Risky actions will wait for the user's approval in the app: do not "
        "retry them, just say what is waiting. Only do what the task says.]\n\n"
    )


def _cfg() -> dict:
    return get_settings().get("scheduler", {}) or {}


def _store_path() -> str:
    name = str(_cfg().get("file", "data/schedules.json"))
    return name if os.path.isabs(name) else str(ROOT / name)


# ---------------------------------------------------------------- job store (JSON file)

def _load() -> dict:
    try:
        with open(_store_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(jobs: dict) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(jobs, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written file


def _trigger(kind: str, value):
    tz = _cfg().get("timezone") or None
    if kind == "cron":
        return CronTrigger.from_crontab(str(value).strip(), timezone=tz)  # e.g. "0 9 * * 1-5"
    if kind == "interval":
        return IntervalTrigger(minutes=int(value), timezone=tz)
    raise ValueError("kind must be 'cron' or 'interval'")


def _validate(name, prompt, kind, value) -> tuple:
    name, prompt = str(name or "").strip(), str(prompt or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Name is required (max 80 characters).")
    if not prompt or len(prompt) > int(_cfg().get("max_prompt_chars", 2000)):
        raise ValueError("Task text is required and must be short and specific.")
    if kind == "interval":
        value = int(value)
        minimum = int(_cfg().get("min_interval_minutes", 5))
        if value < minimum:
            raise ValueError(f"Interval must be at least {minimum} minutes.")
    elif kind == "cron":
        value = str(value).strip()
    else:
        raise ValueError("kind must be 'cron' or 'interval'.")
    _trigger(kind, value)  # raises ValueError on a bad expression
    return name, prompt, value


def _public(job: dict) -> dict:
    out = dict(job)
    sched = _SCHEDULER.get_job(job["id"]) if _SCHEDULER else None
    out["next_run"] = sched.next_run_time.isoformat(timespec="seconds") if sched and sched.next_run_time else None
    return out


# ---------------------------------------------------------------- public API (called by the UI, never by the model)

def list_jobs() -> list:
    with _LOCK:
        return [_public(j) for j in sorted(_load().values(), key=lambda j: j["created"])]


def add_job(name: str, prompt: str, kind: str, value, enabled: bool = True) -> dict:
    name, prompt, value = _validate(name, prompt, kind, value)
    with _LOCK:
        jobs = _load()
        if len(jobs) >= int(_cfg().get("max_jobs", 20)):
            raise ValueError("Too many scheduled tasks. Delete one first.")
        job = {"id": uuid.uuid4().hex[:8], "name": name, "prompt": prompt, "kind": kind, "value": value,
               "enabled": bool(enabled), "created": datetime.now().isoformat(timespec="seconds"),
               "last_run": None, "last_status": None, "last_summary": ""}
        jobs[job["id"]] = job
        _save(jobs)
        _register(job)
    guard.audit("scheduler", "created", f"{name}: {kind} {value}", "", job["id"])
    return _public(job)


def update_job(job_id: str, **fields) -> dict:
    with _LOCK:
        jobs = _load()
        job = jobs.get(job_id)
        if not job:
            raise KeyError("Unknown schedule.")
        merged = {**job, **{k: v for k, v in fields.items() if k in ("name", "prompt", "kind", "value", "enabled")}}
        merged["name"], merged["prompt"], merged["value"] = _validate(
            merged["name"], merged["prompt"], merged["kind"], merged["value"])
        merged["enabled"] = bool(merged["enabled"])
        jobs[job_id] = merged
        _save(jobs)
        _register(merged)
    guard.audit("scheduler", "updated", merged["name"], "", job_id)
    return _public(merged)


def delete_job(job_id: str) -> bool:
    with _LOCK:
        jobs = _load()
        job = jobs.pop(job_id, None)
        if not job:
            return False
        _save(jobs)
        if _SCHEDULER and _SCHEDULER.get_job(job_id):
            _SCHEDULER.remove_job(job_id)
    guard.audit("scheduler", "deleted", job["name"], "", job_id)
    return True


def run_now(job_id: str) -> bool:
    """Queue one immediate run (in the scheduler thread, so the web request returns at once)."""
    if not _SCHEDULER or job_id not in _load():
        return False
    _SCHEDULER.add_job(_run_job, args=[job_id], id=f"now-{job_id}-{uuid.uuid4().hex[:6]}",
                       misfire_grace_time=60, max_instances=1)
    return True


# ---------------------------------------------------------------- running a job

def _register(job: dict) -> None:
    if not _SCHEDULER:
        return
    if _SCHEDULER.get_job(job["id"]):
        _SCHEDULER.remove_job(job["id"])
    if not job.get("enabled"):
        return
    _SCHEDULER.add_job(
        _run_job, _trigger(job["kind"], job["value"]), args=[job["id"]], id=job["id"], name=job["name"],
        max_instances=1,        # a slow run is never started twice at the same time
        coalesce=True,          # missed runs (laptop asleep) collapse into ONE run, not a burst
        misfire_grace_time=int(_cfg().get("misfire_grace_seconds", 600)),
        replace_existing=True)


def _get_agent():
    global _AGENT
    if _AGENT_FACTORY:
        return _AGENT_FACTORY()
    with _LOCK:
        if _AGENT is None:
            from agent.graph import build_graph  # imported late: keeps `import agent.scheduler` cheap
            _AGENT = build_graph()
        return _AGENT


def _parse(msg: ToolMessage) -> dict:
    try:
        data = json.loads(msg.content)
        if isinstance(data, dict):
            return data
    except (TypeError, ValueError):
        pass
    return {"ok": False, "tool": msg.name, "error": str(msg.content)}


def _invoke(thread_id: str, prompt: str) -> tuple:
    config = {"configurable": {"thread_id": thread_id},
              "recursion_limit": get_settings()["agent"]["recursion_limit"]}
    out = _get_agent().invoke({"messages": [HumanMessage(content=prompt)]}, config)
    msgs = out["messages"]
    last_human = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    new = msgs[last_human + 1:]
    results = [_parse(m) for m in new if isinstance(m, ToolMessage)]
    final = next((m.content for m in reversed(new) if isinstance(m, AIMessage) and m.content), "(no answer)")
    return str(final), results


def _approvals_ok() -> bool:
    """Unattended runs need the approval gate switched on, unless the person explicitly allowed otherwise."""
    if _cfg().get("allow_without_approval", False):
        return True
    return guard.confirmations_required() and permissions.computer_confirm_required()


def _remember(job_id: str, status: str, summary: str) -> None:
    with _LOCK:
        jobs = _load()
        if job_id in jobs:
            jobs[job_id].update(last_run=datetime.now().isoformat(timespec="seconds"),
                                last_status=status, last_summary=summary[:8000])
            _save(jobs)


def _run_job(job_id: str) -> None:
    job = _load().get(job_id)
    if not job or not job.get("enabled", True):
        return
    name = job["name"]
    if not _approvals_ok():
        msg = "Approvals are switched off (confirm_changes / confirm_actions). Unattended run skipped."
        guard.audit("scheduler", "blocked", name, "", msg)
        _remember(job_id, "blocked", msg)
        return

    guard.audit("scheduler", "started", name, "", job_id)
    started = time.perf_counter()
    try:
        text, results = _invoke(f"sched-{job_id}-{int(time.time())}", _prompt_prefix() + job["prompt"])
    except Exception as exc:  # noqa: BLE001  - a failing job must never kill the scheduler thread
        msg = f"{type(exc).__name__}: {exc}"
        guard.audit("scheduler", "failed", name, "", msg)
        _remember(job_id, "failed", msg)
        return

    # Everything the run wanted to change is only STAGED. Tag it so the UI can show where it came from, and keep it
    # alive longer than a chat request, because nobody was there to click Approve.
    pending = [r["pending_id"] for r in results if isinstance(r, dict) and r.get("pending_id")]
    ttl = float(_cfg().get("pending_ttl_minutes", 720))
    for pid in pending:
        guard.annotate(pid, origin=f"scheduler: {name}", ttl_minutes=ttl)

    status = "waiting_approval" if pending else "done"
    detail = f"{len(pending)} action(s) waiting for approval" if pending else f"{time.perf_counter() - started:.0f}s"
    guard.audit("scheduler", status, name, "", detail)
    _remember(job_id, status, text)


# ---------------------------------------------------------------- lifecycle

def start(agent_factory=None):
    """Start the background scheduler once per process. Safe to call again (Streamlit re-runs the script)."""
    global _SCHEDULER, _AGENT_FACTORY
    with _LOCK:
        if agent_factory:
            _AGENT_FACTORY = agent_factory
        if _SCHEDULER is not None:
            return _SCHEDULER
        if not _cfg().get("enabled", True):
            return None
        _SCHEDULER = BackgroundScheduler(timezone=_cfg().get("timezone") or None, daemon=True,
                                         job_defaults={"misfire_grace_time": 600})
        _SCHEDULER.start()
        for job in _load().values():
            try:
                _register(job)
            except Exception as exc:  # noqa: BLE001  - one broken entry must not stop the others
                guard.audit("scheduler", "failed", job.get("name", "?"), "", f"could not schedule: {exc}")
        return _SCHEDULER


def shutdown():
    global _SCHEDULER
    if _SCHEDULER:
        _SCHEDULER.shutdown(wait=False)
        _SCHEDULER = None