"""Flask UI for the Personal AI Agent.

Same real LangGraph agent and the same real tools as ui/streamlit_app.py (Streamlit), with a different front end:
a purple / blue / pink sidebar on the left that holds every feature, and a chat workspace on the right.

Run:   python -m ui.flask_app    ->  http://127.0.0.1:5000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root, so `agent`, `tools`, `security` import from ui/
import json
import os
import subprocess
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent.graph import build_graph
from agent.llm import check_ollama
from agent.memory import STATUS as memory_status
from security import permissions
from security.settings import get_settings
from tools import GROUPS, TOOLS_BY_NAME
from security import guard

app = Flask(__name__)
SETTINGS = get_settings()
HOME = os.path.expanduser("~")

_AGENT = None
_AGENT_LOCK = threading.Lock()


def get_agent():
    global _AGENT
    with _AGENT_LOCK:
        if _AGENT is None:
            _AGENT = build_graph()
        return _AGENT


# ------------------------------------------------------------------ helpers

def parse_tool_message(msg: ToolMessage) -> dict:
    try:
        data = json.loads(msg.content)
        if isinstance(data, dict):
            return data
    except (TypeError, ValueError):
        pass
    return {"ok": False, "tool": msg.name, "error": str(msg.content)}


def load_history(thread_id: str) -> list:
    """Rebuild the chat view from the saved conversation (Postgres or in-memory)."""
    try:
        state = get_agent().get_state({"configurable": {"thread_id": thread_id}})
        msgs = (state.values or {}).get("messages", [])
    except Exception:  # noqa: BLE001
        return []
    history, pending = [], []
    for m in msgs:
        if isinstance(m, HumanMessage):
            text = str(m.content).split("\n\n(UI drive filter:")[0]
            history.append({"role": "user", "content": text, "results": []})
            pending = []
        elif isinstance(m, ToolMessage):
            pending.append(parse_tool_message(m))
        elif isinstance(m, AIMessage) and m.content:
            history.append({"role": "assistant", "content": str(m.content), "results": pending})
            pending = []
    return history


def run_agent(thread_id: str, prompt: str, drive: str):
    hint = f"\n\n(UI drive filter: {drive}: only)" if drive and drive != "all" else ""
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": SETTINGS["agent"]["recursion_limit"],
    }
    try:
        out = get_agent().invoke({"messages": [HumanMessage(content=prompt + hint)]}, config)
    except GraphRecursionError:
        return "⚠️ The agent took too many steps. Try a more specific request.", []
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Agent error: {type(exc).__name__}: {exc}", []

    msgs = out["messages"]
    last_human = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    new = msgs[last_human + 1:]
    results = [parse_tool_message(m) for m in new if isinstance(m, ToolMessage)]
    final = next((m.content for m in reversed(new) if isinstance(m, AIMessage) and m.content), "(no answer)")
    return (final if isinstance(final, str) else str(final)), results


def pending_view(pid: str) -> dict:
    info = guard.status(pid)
    if info is None:
        return {"status": "gone"}
    return {"status": info["status"], "description": info["description"], "result": info["result"]}


# ------------------------------------------------------------------ manual-tool form schema

def T(name, label, default="", help=""):
    return {"name": name, "label": label, "type": "text", "default": default, "help": help}


def A(name, label, default="", help=""):
    return {"name": name, "label": label, "type": "textarea", "default": default, "help": help}


def N(name, label, lo, hi, default):
    return {"name": name, "label": label, "type": "number", "min": lo, "max": hi, "default": default}


def C(name, label, default=False):
    return {"name": name, "label": label, "type": "checkbox", "default": default}


def S(name, label, options, default=None):
    return {"name": name, "label": label, "type": "select", "options": options,
            "default": default if default is not None else options[0]}


def form_fields() -> dict:
    approved = permissions.approved_read_dirs()
    read_default = approved[0] if approved else HOME
    wdirs = permissions.write_dirs()
    write_default = wdirs[0] if wdirs else HOME
    j = lambda *p: os.path.join(*p)  # noqa: E731
    return {
        "list_directory": [T("path", "Directory path", HOME), N("max_items", "Max items", 1, 500, 200)],
        "search_files": [T("query", "Filename (partial, case-insensitive; * and ? allowed)"),
                         T("directory", "Limit to directory (optional)"), N("max_results", "Max results", 1, 500, 100)],
        "search_by_extension": [T("extension", "Extension(s)", ".pdf", "e.g. .py or .txt,.csv"),
                                T("directory", "Limit to directory (optional)"),
                                T("name_contains", "Filename must contain (optional)"),
                                N("max_results", "Max results", 1, 500, 100)],
        "search_by_content": [T("query", "Text to find (case-insensitive)"),
                              T("directory", "Directory (inside an approved read folder)", read_default),
                              T("extensions", "Extensions (optional)", "", "e.g. .py,.md"),
                              N("max_results", "Max matches", 1, 500, 50)],
        "read_file": [T("path", "Full file path", read_default), T("pages", "PDF pages (optional)", "", "e.g. 1-5"),
                      N("max_chars", "Max characters", 500, 50000, 12000)],
        "get_file_info": [T("path", "Full file path")],
        "find_duplicates": [T("directory", "Directory (inside an approved read folder)", read_default),
                            T("extensions", "Extensions (optional)"), N("min_size_kb", "Minimum size (KB)", 0, 1000000, 1)],
        "create_file": [T("path", "New file path", j(write_default, "new_file.txt")), A("content", "Content (optional)")],
        "write_file": [T("path", "File path", j(write_default, "notes.txt")), A("content", "Content"),
                       C("overwrite", "Overwrite if the file exists (needs approval)")],
        "append_file": [T("path", "Existing file path"), A("content", "Text to add at the end"),
                        C("newline_before", "Start on a new line", True)],
        "replace_text": [T("path", "Existing file path"), T("old_text", "Text to find (exact, case-sensitive)"),
                         T("new_text", "Replace with"), C("replace_all", "Replace all occurrences", True)],
        "rename_file": [T("path", "File or folder path"), T("new_name", "New name only (no folder)")],
        "copy_file": [T("source", "Source file path"), T("destination", "Destination folder or full file path", write_default),
                      C("overwrite", "Replace if the destination exists (needs approval)")],
        "move_file": [T("source", "Source file path"), T("destination", "Destination folder or full file path", write_default),
                      C("overwrite", "Replace if the destination exists (needs approval)")],
        "create_directory": [T("path", "New folder path", j(write_default, "new_folder"))],
        "delete_file": [T("path", "File to delete (goes to the Recycle Bin)")],
        "delete_directory": [T("path", "Folder to delete (goes to the Recycle Bin)"),
                             C("recursive", "Also delete everything inside it")],
        "get_system_info": [], "get_current_directory": [],
        "get_disk_usage": [],  # drive is injected from the sidebar
        "run_shell_command": [A("command", "PowerShell command (needs your Approve click)", "", "e.g. Get-Date"),
                              T("working_directory", "Working folder (optional)"),
                              N("timeout_seconds", "Timeout (seconds, 0 = default)", 0, 120, 0)],
        "open_file_location": [T("path", "File or folder path (shown in Explorer)")],
        "open_file": [T("path", "File to open in its default application (needs your Approve click)")],
        "read_clipboard": [N("max_chars", "Max characters", 100, 20000, 5000)],
        "create_note": [T("title", "Title"), A("content", "Note text"), S("format", "Format", ["md", "txt"]),
                        T("folder", "Folder (optional, blank = notes folder from config.yaml)")],
        "extract_text": [T("path", "Full file path (pdf, docx, pptx, xlsx, csv, json, html, text, image)", read_default),
                         S("engine", "Engine", ["local", "whisperer"]), T("pages", "PDF pages (optional)", "", "e.g. 1-5"),
                         N("max_chars", "Max characters", 500, 50000, 12000)],
        "convert_document": [T("source", "Source file path"), T("target_format", "Convert to (extension)", "", "pdf, docx, md, txt, csv, json, xlsx, png, jpg, webp"),
                             T("destination", "Destination folder or file (optional)"),
                             C("overwrite", "Replace if the destination exists (needs approval)")],
        "generate_report": [S("kind", "Report about", ["search", "operations"]), T("title", "Title (optional)"),
                            T("query", "File name contains (search)"), T("extensions", "Extensions (search, optional)"),
                            T("directory", "Directory (search, optional)"), N("days", "Days back (operations)", 0, 3650, 7),
                            T("tool_filter", "Only this tool (operations, optional)"), S("format", "Format", ["md", "html"])],
        "file_operation_history": [N("limit", "Entries", 1, 500, 50), T("tool", "Only this tool (optional)"),
                                   S("status", "Status", ["", "done", "failed", "pending", "approved", "rejected", "expired", "external_upload"]),
                                   N("days", "Last N days (0 = all)", 0, 3650, 0)],
        "get_approved_result": [T("request_id", "Request id (pending_id)")],
        "web_search": [T("query", "Search query"), S("topic", "Topic", ["general", "news"]),
                       N("max_results", "Max results", 1, 10, 5)],
        "research_report": [T("topic", "Topic to research"), N("pages", "Pages (3-8)", 3, 8, 4), S("output", "Output", ["pdf", "md"])],
    }


# Features shown in the left sidebar: (group key, label, icon, accent, one-line blurb)
FEATURES = [
    ("traversal", "Find files", "search", "blue", "Browse folders, search by name or type, spot duplicates"),
    ("reading", "Read & search text", "book", "purple", "Read a file or search inside many files"),
    ("documents", "Documents", "doc", "pink", "Notes, text extraction, conversion, reports, research"),
    ("web", "Web search", "globe", "blue", "Look things up on the internet"),
    ("modify", "Edit files", "edit", "purple", "Create, write, rename, move, delete (with approval)"),
    ("computer", "Computer", "cpu", "pink", "System info, disk usage, PowerShell, open files"),
    ("audit", "History & audit", "clock", "blue", "Everything the agent did, and what you approved"),
]

SUGGESTIONS = [
    {"icon": "search", "title": "Find files", "text": "Find all .pdf files in my Documents"},
    {"icon": "globe", "title": "Research the web", "text": "Search the web for the latest Flask release"},
    {"icon": "disk", "title": "Check my disks", "text": "Show disk usage"},
    {"icon": "book", "title": "Read & summarize", "text": "Search my Documents for 'budget'"},
]


# ------------------------------------------------------------------ routes

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_config():
    fields = form_fields()
    groups = []
    for key, label, icon, accent, blurb in FEATURES:
        tools = []
        for t in GROUPS.get(key, []):
            desc = (t.description or "").strip().splitlines()
            tools.append({"name": t.name, "description": desc[0] if desc else "", "fields": fields.get(t.name, [])})
        groups.append({"key": key, "label": label, "icon": icon, "accent": accent, "blurb": blurb, "tools": tools})
    return jsonify({"groups": groups, "suggestions": SUGGESTIONS, "model": SETTINGS["llm"]["model"]})


@app.get("/api/status")
def api_status():
    allow_reads, allow_writes = permissions.allow_all_reads(), permissions.allow_all_writes()
    return jsonify({
        "model": SETTINGS["llm"]["model"],
        "memory": memory_status["detail"],
        "allow_all_reads": allow_reads,
        "read_dirs": [] if allow_reads else permissions.approved_read_dirs(),
        "modify_enabled": permissions.modify_enabled(),
        "allow_all_writes": allow_writes,
        "write_dirs": [] if allow_writes else permissions.write_dirs(),
        "confirm_changes": guard.confirmations_required(),
        "computer_enabled": permissions.computer_enabled(),
        "computer_confirm": permissions.computer_confirm_required(),
        "whisperer": bool(SETTINGS.get("whisperer", {}).get("enabled")),
        "whisperer_key": bool(os.getenv("LLMWHISPERER_API_KEY")),
        "tavily": bool(os.getenv("TAVILY_API_KEY")),
    })


@app.post("/api/model-check")
def api_model_check():
    level, message = check_ollama()
    return jsonify({"level": level, "message": message})


@app.get("/api/history")
def api_history():
    thread = request.args.get("thread", "")
    return jsonify({"history": load_history(thread) if thread else []})


@app.post("/api/chat")
def api_chat():
    data = request.get_json(force=True)
    prompt = (data.get("message") or "").strip()
    thread = data.get("thread") or str(uuid.uuid4())
    if not prompt:
        return jsonify({"error": "Empty message"}), 400
    started = time.perf_counter()
    text, results = run_agent(thread, prompt, data.get("drive", "all"))
    return jsonify({"text": text, "results": results, "elapsed": round(time.perf_counter() - started, 1),
                    "thread": thread})


@app.post("/api/clear")
def api_clear():
    thread = (request.get_json(force=True) or {}).get("thread", "")
    try:
        get_agent().checkpointer.delete_thread(thread)
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"ok": True})


@app.post("/api/tool/<name>")
def api_tool(name):
    if name not in TOOLS_BY_NAME:
        return jsonify({"ok": False, "tool": name, "error": "Unknown tool"}), 404
    body = request.get_json(force=True) or {}
    args = dict(body.get("args") or {})
    if name == "get_disk_usage":
        args["drive"] = body.get("drive", "all")
    for key in ("search_files", "search_by_extension", "generate_report"):
        if name == key:
            args["drive"] = body.get("drive", "all")
    started = time.perf_counter()
    try:
        result = json.loads(TOOLS_BY_NAME[name].invoke(args))
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "tool": name, "error": f"Invalid input: {exc}"}
    return jsonify({"result": result, "elapsed": round(time.perf_counter() - started, 2)})


@app.get("/api/pending/<pid>")
def api_pending(pid):
    return jsonify(pending_view(pid))


@app.post("/api/approve/<pid>")
def api_approve(pid):
    guard.approve(pid)
    return jsonify(pending_view(pid))


@app.post("/api/reject/<pid>")
def api_reject(pid):
    guard.reject(pid)
    return jsonify(pending_view(pid))


@app.post("/api/open")
def api_open():
    """Reveal a path in Windows Explorer. Never launches/executes files; the path goes through the permission check."""
    path = (request.get_json(force=True) or {}).get("path", "")
    try:
        real = permissions.check_search_path(path)
    except permissions.PermissionDenied as exc:
        return jsonify({"ok": False, "error": str(exc)})
    if os.name != "nt":
        return jsonify({"ok": False, "error": "Opening Explorer only works when the app runs on Windows."})
    if not os.path.exists(real):
        return jsonify({"ok": False, "error": f"Path no longer exists: {real}"})
    if os.path.isdir(real):
        subprocess.Popen(["explorer", real])
    else:
        subprocess.Popen(["explorer", f"/select,{real}"])
    return jsonify({"ok": True})


if __name__ == "__main__":
    # threaded so a long agent run never blocks Approve / Reject clicks
    app.run(host=os.getenv("FLASK_HOST", "127.0.0.1"), port=int(os.getenv("FLASK_PORT", "5000")),
            debug=False, threaded=True)
