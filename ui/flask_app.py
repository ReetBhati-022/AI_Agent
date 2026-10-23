"""Flask UI for the Personal AI Agent.

Same real LangGraph agent and the same real tools as ui/streamlit_app.py (Streamlit), with a different front end:
a purple / blue / pink sidebar on the left that holds every feature, and a chat workspace on the right.

Run:   python -m ui.flask_app    ->  http://127.0.0.1:5000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root, so `agent`, `tools`, `security` import from ui/
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request, send_file
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent.graph import build_graph
from agent.llm import check_ollama
from agent.memory import STATUS as memory_status
from security import permissions
from security.settings import get_settings
from tools import GROUPS, TOOLS_BY_NAME
from tools.common import human_size
from tools.documents.pdf import _common as pdfc
from security import guard
from agent import scheduler

app = Flask(__name__)
SETTINGS = get_settings()
HOME = os.path.expanduser("~")
app.config["MAX_CONTENT_LENGTH"] = int((SETTINGS.get("uploads") or {}).get("max_request_mb", 500)) * 1024 * 1024

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


PDF_HELP = {  # hints for the auto-built PDF forms
    "source": "full path of the PDF", "sources": "one full path per line, in the order you want",
    "pages": "e.g. 2  or  1-3,7  or  9- (to the end)", "ranges": "groups split by ;  e.g. 1-3;4-6;7-",
    "page_order": "e.g. 3,1,2,5-7 - pages left out are dropped", "rotate_pages": "e.g. 2,4-6 or all",
    "destination": "Leave blank to save in your Uploads folder",
    "language": "optional, e.g. eng or eng+hin", "overwrite": "Replace if it exists (needs approval)",
}


def downloads_dir() -> str:
    """The user's Downloads folder if the app may write there, else the uploads folder."""
    d = os.path.join(HOME, "Downloads")
    try:
        return permissions.check_write_path(d)
    except permissions.PermissionDenied:
        return uploads_dir()


PDF_TOOL_NAMES = {t.name for t in GROUPS.get("pdf", [])}


def with_default_destination(name: str, args: dict) -> dict:
    """PDF results go to Downloads unless the user typed another place. split_pdf gets its own folder in there."""
    dest = str(args.get("destination") or "").strip().strip("\"'")
    root = downloads_dir()
    if dest and os.path.normcase(os.path.normpath(dest)) != os.path.normcase(os.path.normpath(root)):
        return args
    args = dict(args)
    if name == "split_pdf":
        base = os.path.splitext(os.path.basename(str(args.get("source") or "")))[0] or "split"
        args["destination"] = os.path.join(root, base + "_split")
    else:
        args["destination"] = root
    return args


PDF_LABELS = {"destination": "Save to"}
ENUM_LABELS = {"level": {"low": "Low - best quality", "medium": "Medium - balanced", "high": "High - smallest file"}}


def file_field(tool, name: str) -> dict:
    """Upload box instead of a typed path: `source` = one PDF, `sources` = several files (PDFs, or images for scan_to_pdf)."""
    kind = "image" if tool.name == "scan_to_pdf" else "pdf"
    many = name == "sources"
    if name in ("source_a", "source_b"):
        label, help_ = ("First PDF (original)", "") if name == "source_a" else ("Second PDF (new)", "")
        return {"name": name, "label": label, "type": "file", "kind": "pdf", "multiple": False, "help": help_,
                "accept": ".pdf", "max_mb": pdfc.cfg("max_source_mb", 100)}
    if kind == "image":
        label, help_ = "Images", "JPG, PNG, TIF... one image per page, in the order shown"
    elif many:
        label, help_ = "PDF files", "Add 2 or more. They are joined in the order shown"
    else:
        label, help_ = "PDF file", ""
    return {"name": name, "label": label, "type": "file", "kind": kind, "multiple": many, "help": help_,
            "accept": ".pdf" if kind == "pdf" else ",".join(sorted(pdfc.IMAGE_EXTS)),
            "max_mb": pdfc.cfg("max_source_mb", 100)}


def auto_fields(tool, read_default: str = "") -> list:
    """Form fields straight from a tool's signature: Literal -> select, bool -> checkbox, int -> number, else text.
    Input files (`source` / `sources`) become an upload box."""
    fields = []
    for name, p in tool.args.items():
        label = PDF_LABELS.get(name, name.replace("_", " ").capitalize())
        default = p.get("default", "")
        if name in ("source", "sources", "source_a", "source_b"):
            fields.append(file_field(tool, name))
        elif name == "password":
            lock = tool.name == "protect_pdf"
            fields.append({"name": name, "label": "Set a password" if lock else "PDF password", "type": "password", "default": "",
                           "help": "4 to 100 characters. If you forget it, it cannot be recovered" if lock
                           else "The password this PDF asks for when you open it"})
        elif "enum" in p:
            f = S(name, label, p["enum"], default)
            if name in ENUM_LABELS:
                f["labels"] = ENUM_LABELS[name]
            fields.append(f)
        elif p.get("type") == "boolean":
            fields.append(C(name, PDF_HELP.get(name, label), bool(default)))
        elif p.get("type") == "integer":
            fields.append(N(name, label, 0, 100000, default or 0))
        else:
            f = T(name, label, default, PDF_HELP.get(name, ""))
            if name == "destination":
                f["default"] = downloads_dir()
                f["help"] = "Saved in your Downloads folder. Change it only if you want another place"
                f["placeholder"] = "Folder or .pdf name"
            fields.append(f)
    return fields


# Features shown in the left sidebar: (group key, label, icon, accent, one-line blurb)
FEATURES = [
    ("traversal", "Find files", "search", "blue", "Browse folders, search by name or type, spot duplicates"),
    ("reading", "Read & search text", "book", "purple", "Read a file or search inside many files"),
    ("documents", "Documents", "doc", "pink", "Notes, text extraction, conversion, reports, research"),
    ("pdf", "PDF tools", "pdf", "pink", "Organize, Optimize (compress, repair, OCR), Security (unlock, protect, compare)"),
    ("web", "Web search", "globe", "blue", "Look things up on the internet"),
    ("modify", "Edit files", "edit", "purple", "Create, write, rename, move, delete (with approval)"),
    ("computer", "Computer", "cpu", "pink", "System info, disk usage, PowerShell, open files"),
    ("audit", "History & audit", "clock", "blue", "Everything the agent did, and what you approved"),
]

# Friendly tab names (tools not listed here show their technical name)
TOOL_LABELS = {
    "merge_pdf": "Merge PDF", "split_pdf": "Split PDF", "remove_pdf_pages": "Remove pages",
    "extract_pdf_pages": "Extract pages", "organize_pdf": "Organize PDF", "scan_to_pdf": "Scan to PDF",
    "compress_pdf": "Compress PDF", "repair_pdf": "Repair PDF", "ocr_pdf": "OCR PDF",
    "unlock_pdf": "Unlock PDF", "protect_pdf": "Protect PDF", "compare_pdf": "Compare PDF",
}

# Short, plain-language text shown above each form. (The tools' own long docstrings are written for the AI model.)
TOOL_HINTS = {
    "merge_pdf": "Join several PDFs into one.",
    "split_pdf": "Cut one PDF into smaller PDFs.",
    "remove_pdf_pages": "Delete the pages you don't need.",
    "extract_pdf_pages": "Keep only the pages you pick, in a new PDF.",
    "organize_pdf": "Reorder, reverse or rotate pages.",
    "scan_to_pdf": "Turn photos or scans into one PDF.",
    "compress_pdf": "Make your PDF smaller. Your original file stays safe.",
    "repair_pdf": "Fix a PDF that won't open or looks broken.",
    "ocr_pdf": "Make a scanned PDF searchable and copyable.",
    "unlock_pdf": "Remove the password from a PDF. You need to know the password.",
    "protect_pdf": "Lock your PDF with a password.",
    "compare_pdf": "See what changed between two PDFs. You get a report PDF.",
}


def short_desc(t) -> str:
    """Our own short text if we have one, else the first sentence of the docstring (max ~110 chars)."""
    if t.name in TOOL_HINTS:
        return TOOL_HINTS[t.name]
    lines = (t.description or "").strip().splitlines()
    first = re.split(r"(?<!e\.g)(?<!i\.e)\.\s", lines[0], maxsplit=1)[0].rstrip(".;:, ") if lines else ""
    return first if len(first) <= 110 else first[:107].rsplit(" ", 1)[0] + "..."


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
    approved = permissions.approved_read_dirs()
    read_default = approved[0] if approved else HOME
    groups = []
    for key, label, icon, accent, blurb in FEATURES:
        tools = []
        for t in GROUPS.get(key, []):
            tools.append({"name": t.name, "label": TOOL_LABELS.get(t.name, t.name),
                          "description": short_desc(t), "fields": auto_fields(t, read_default) if key == "pdf" else fields.get(t.name, [])})
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
    if name in PDF_TOOL_NAMES:
        args = with_default_destination(name, args)
    started = time.perf_counter()
    try:
        result = json.loads(TOOLS_BY_NAME[name].invoke(args))
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "tool": name, "error": f"Invalid input: {exc}"}
    return jsonify({"result": result, "elapsed": round(time.perf_counter() - started, 2)})


# ------------------------------------------------------------------ file upload / download

UPLOAD_EXTS = {"pdf": {".pdf"}, "image": set(pdfc.IMAGE_EXTS)}
UPLOAD_LABEL = {"pdf": "a PDF", "image": "an image"}


def uploads_dir() -> str:
    """Where uploaded files are kept. Default: 'AI Agent Uploads' inside the first write folder. It must be a folder
    the app may write to, so the result (saved next to the upload) is allowed too."""
    raw = (SETTINGS.get("uploads") or {}).get("dir")
    if not raw:
        wdirs = permissions.write_dirs()
        raw = os.path.join(wdirs[0] if wdirs else os.path.join(HOME, "Documents"), "AI Agent Uploads")
    return permissions.check_write_path(raw)


def clean_filename(name: str) -> str:
    """Browser file name -> a safe Windows file name (folders dropped, bad characters replaced)."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = "".join("_" if (c in permissions.INVALID_NAME_CHARS) else c for c in name).strip(" .")
    return name or "file"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_upload(storage, folder: str, kind: str) -> dict:
    """Validate one uploaded file and store it. The same file uploaded twice is stored once."""
    name = clean_filename(storage.filename)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in UPLOAD_EXTS[kind]:
        raise permissions.PermissionDenied(f"'{name}' is not {UPLOAD_LABEL[kind]} file (allowed: {', '.join(sorted(UPLOAD_EXTS[kind]))}).")
    permissions.check_write_path(os.path.join(folder, name), check_ext=True)  # reserved names, blocked patterns, ...

    tmp = os.path.join(folder, f".{uuid.uuid4().hex[:10]}.upload.tmp")
    try:
        storage.save(tmp)
        size = os.path.getsize(tmp)
        if size == 0:
            raise permissions.PermissionDenied(f"'{name}' is empty.")
        if size > pdfc.cfg("max_source_mb", 100) * 1024 * 1024:
            raise permissions.PermissionDenied(f"'{name}' is over the {pdfc.cfg('max_source_mb', 100)} MB limit.")
        if kind == "pdf":
            with open(tmp, "rb") as fh:
                if b"%PDF-" not in fh.read(1024):
                    raise permissions.PermissionDenied(f"'{name}' does not look like a real PDF.")
        else:
            try:
                from PIL import Image
                with Image.open(tmp) as im:
                    im.verify()
            except Exception as exc:  # noqa: BLE001
                raise permissions.PermissionDenied(f"'{name}' could not be read as an image.") from exc

        digest, n = sha256_of(tmp), 0
        while True:  # name, name (1), name (2) ... reuse the first one that is the very same file
            target = os.path.join(folder, name if n == 0 else f"{stem} ({n}){ext}")
            if not os.path.lexists(target):
                os.replace(tmp, target)
                guard.audit("upload", "done", f"Uploaded {name} ({human_size(size)})", target)
                break
            if os.path.isfile(target) and os.path.getsize(target) == size and sha256_of(target) == digest:
                break
            n += 1
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"name": os.path.basename(target), "path": target, "size": human_size(size)}


@app.post("/api/upload")
def api_upload():
    kind = request.form.get("kind", "pdf")
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if kind not in UPLOAD_EXTS:
        return jsonify({"ok": False, "error": "Unknown upload type."}), 400
    if not files:
        return jsonify({"ok": False, "error": "No file received."}), 400
    if len(files) > max(pdfc.cfg("max_merge_files", 100), pdfc.cfg("max_scan_images", 300)):
        return jsonify({"ok": False, "error": "Too many files at once."}), 400
    try:
        pdfc.check_enabled()
        folder = uploads_dir()
        os.makedirs(folder, exist_ok=True)
        saved = [save_upload(f, folder, kind) for f in files]
    except permissions.PermissionDenied as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not save the upload: {exc}"})
    return jsonify({"ok": True, "files": saved})


@app.errorhandler(413)
def too_large(_):
    return jsonify({"ok": False, "error": "These files are too big to upload in one go."}), 413


@app.get("/api/download")
def api_download():
    """Hand a finished PDF to the browser. Only .pdf files inside a write folder are served."""
    raw = request.args.get("path", "")
    try:
        real = permissions.check_search_path(raw)
        inside = permissions.allow_all_writes() or any(permissions.is_within(real, w) for w in permissions.write_dirs())
        if (not inside or not os.path.isfile(real) or os.path.splitext(real)[1].lower() != ".pdf"
                or permissions.is_blocked_filename(os.path.basename(real))):
            raise permissions.PermissionDenied("This file cannot be downloaded.")
    except permissions.PermissionDenied as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    return send_file(real, as_attachment=True, download_name=os.path.basename(real))


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


# ------------------------------------------------------------------ scheduled tasks + approval inbox
# The agent has no tool for any of this: only the person (through these routes) can create or change schedules,
# and approving still goes through guard.approve() via /api/approve/<pid>.

@app.get("/api/pending")
def api_pending_list():
    """Actions waiting for approval that came from scheduled runs (shown in the inbox banner)."""
    return jsonify({"items": guard.list_pending("scheduler")})


@app.get("/api/schedules")
def api_schedules():
    return jsonify({"jobs": scheduler.list_jobs()})


@app.post("/api/schedules")
def api_schedule_add():
    b = request.get_json(force=True) or {}
    try:
        job = scheduler.add_job(b.get("name"), b.get("prompt"), b.get("kind"), b.get("value"), b.get("enabled", True))
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "job": job})


@app.patch("/api/schedules/<job_id>")
def api_schedule_update(job_id):
    try:
        job = scheduler.update_job(job_id, **(request.get_json(force=True) or {}))
    except KeyError:
        return jsonify({"ok": False, "error": "Unknown schedule."}), 404
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "job": job})


@app.delete("/api/schedules/<job_id>")
def api_schedule_delete(job_id):
    return jsonify({"ok": scheduler.delete_job(job_id)})


@app.post("/api/schedules/<job_id>/run")
def api_schedule_run(job_id):
    return jsonify({"ok": scheduler.run_now(job_id)})


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
    scheduler.start(agent_factory=get_agent)  # one background thread; debug=False so there is no reloader double-start
    # threaded so a long agent run never blocks Approve / Reject clicks
    app.run(host=os.getenv("FLASK_HOST", "127.0.0.1"), port=int(os.getenv("FLASK_PORT", "5000")),
            debug=False, threaded=True)