"""Streamlit UI. Everything runs through the real LangGraph agent and the real tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root, so `agent`, `tools`, `security` import from ui/
import json
import os
import subprocess
import threading
import time
import uuid

import pandas as pd
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent.graph import build_graph
from agent.llm import check_ollama
from agent.memory import STATUS as memory_status
from security import permissions
from security.settings import get_settings
from tools import GROUPS, TOOLS_BY_NAME
from security import guard

MODIFY_TOOLS = {t.name for t in GROUPS["modify"]}
OUTPUT_TOOLS = {"create_note", "convert_document", "generate_report", "research_report"}  # create a file and report its path

st.set_page_config(page_title="Personal AI Agent", page_icon="🗂️", layout="wide")

SETTINGS = get_settings()
HOME = os.path.expanduser("~")


@st.cache_resource(show_spinner="Starting agent…")
def get_agent():
    return build_graph()


def init_state():
    if "thread_id" not in st.session_state:
        # thread id lives in the URL so a browser refresh or app restart resumes the same chat
        thread_id = st.query_params.get("thread") or str(uuid.uuid4())
        st.query_params["thread"] = thread_id
        st.session_state.thread_id = thread_id
        st.session_state.history = None  # loaded from memory once the agent exists
    st.session_state.setdefault("manual", None)


def clear_chat():
    try:
        get_agent().checkpointer.delete_thread(st.session_state.thread_id)
    except Exception:  # noqa: BLE001
        pass
    st.session_state.thread_id = str(uuid.uuid4())
    st.query_params["thread"] = st.session_state.thread_id
    st.session_state.history = []


# ---------- rendering ----------

def open_in_explorer(path: str):
    """Reveal a path in Windows Explorer. Never launches/executes files; path goes through the permission check."""
    try:
        real = permissions.check_search_path(path)
    except permissions.PermissionDenied as exc:
        st.error(str(exc))
        return
    if os.name != "nt":
        st.error("Opening Explorer only works when the app runs on Windows.")
    elif not os.path.exists(real):
        st.error(f"Path no longer exists: {real}")
    elif os.path.isdir(real):
        subprocess.Popen(["explorer", real])
    else:
        subprocess.Popen(["explorer", f"/select,{real}"])


def path_actions(path: str, key: str):
    st.code(path, language=None)  # has a copy button
    if st.button("📂 Open in Explorer", key=f"open_{key}"):
        open_in_explorer(path)


def path_table(df: pd.DataFrame, key: str):
    event = st.dataframe(df, hide_index=True, on_select="rerun",
                         selection_mode="single-row", key=f"tbl_{key}")
    rows = event.selection.rows
    if rows and rows[0] < len(df):
        path_actions(str(df.iloc[rows[0]]["path"]), key)
    else:
        st.caption("Select a row to open its location in Explorer.")


def render_pending(res: dict, key: str):
    """Approve / Reject card for a staged change. Only these buttons can execute it (the model cannot)."""
    pid, desc = res["pending_id"], res.get("description", "")
    info = guard.status(pid)
    if info is None:
        st.warning(f"⏱️ {desc}\n\nThis request expired (or the app was restarted). Ask again.")
        return
    state = info["status"]
    if state == "pending":
        st.warning(f"⚠️ **Approval needed:** {desc}")
        c1, c2, _ = st.columns([1, 1, 5])
        if c1.button("✅ Approve", key=f"ok_{pid}_{key}", type="primary"):
            guard.approve(pid)
            st.rerun()
        if c2.button("✖ Reject", key=f"no_{pid}_{key}"):
            guard.reject(pid)
            st.rerun()
    elif state in ("done", "failed"):
        render_result(info["result"], f"{key}_res")
    elif state == "rejected":
        st.info(f"Rejected, nothing was changed: {desc}")
    elif state == "expired":
        st.warning(f"⏱️ Expired, nothing was changed: {desc}")
    else:
        st.info(f"{state}: {desc}")


def render_result(res, key="r"):
    if not isinstance(res, dict):
        st.write(res)
        return
    if not res.get("ok", False):
        st.error(res.get("error", "Tool failed."))
        return
    if res.get("pending_id"):
        render_pending(res, key)
        return
    if res.get("tool") in MODIFY_TOOLS:
        st.success(" · ".join(res.get("notes") or ["Done."]))
        st.dataframe(pd.DataFrame(res.get("rows") or []), hide_index=True)
        if res.get("rows") and res["rows"][0].get("action") not in ("deleted",):
            path_actions(res["rows"][0]["path"], key)
        return

    tool, rows = res.get("tool"), res.get("rows") or []
    if tool in OUTPUT_TOOLS and rows:
        st.success(" · ".join(res.get("notes") or ["Done."]))
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        path_actions(rows[0]["path"], key)
        if tool == "generate_report" and res.get("preview"):
            with st.expander("Report preview"):
                if res.get("format") == "html":
                    st.code(res["preview"], language="html")
                else:
                    st.markdown(res["preview"])
        return
    if tool in ("extract_text", "read_clipboard", "get_approved_result") and rows is not None and "content" in res:
        meta = rows[0] if rows else {}
        if meta.get("name"):
            st.markdown(f"**{meta['name']}** · {meta.get('size', '')} · engine: {res.get('engine', 'local')}")
        st.text_area("Content (untrusted data)", res.get("content", ""), height=320, key=f"txt_{key}", disabled=True)
        if meta.get("path"):
            path_actions(meta["path"], key)
        st.caption(" · ".join(res.get("notes") or []))
        return
    if tool == "file_operation_history":
        st.dataframe(pd.DataFrame(rows), hide_index=True)
        st.caption(" · ".join([f"{res.get('count', 0)} entr(ies)"] + (res.get("notes") or [])))
        return
    if tool == "find_duplicates":
        groups = res.get("groups") or []
        if not groups:
            st.info("No duplicate files found.")
        for i, g in enumerate(groups, start=1):
            title = f"Group {i} · {g['copies']} copies · {g['size']} each · {g['wasted']} reclaimable"
            with st.expander(title, expanded=i <= 3):
                st.caption(f"SHA-256: {g['hash']}")
                path_table(pd.DataFrame(g["files"]), f"{key}_g{i}")
    elif tool == "read_file" and rows:
        meta = rows[0]
        st.markdown(f"**{meta['name']}** · {meta['size']} · modified {meta['modified']}")
        table = res.get("table")
        if table is not None:
            st.dataframe(pd.DataFrame(table["rows"], columns=table["columns"]), hide_index=True)
        else:
            st.text_area("Content (untrusted file data)", res.get("content", ""), height=320,
                         key=f"txt_{key}", disabled=True)
        path_actions(meta["path"], key)
    elif tool == "get_file_info" and rows:
        info = pd.DataFrame([(k, str(v)) for k, v in rows[0].items()], columns=["Property", "Value"])
        st.dataframe(info, hide_index=True)
        path_actions(rows[0]["full_path"], key)
    elif tool == "get_system_info" and rows:
        st.dataframe(pd.DataFrame([(k, str(v)) for k, v in rows[0].items()], columns=["Property", "Value"]),
                     hide_index=True)
    elif tool == "get_disk_usage":
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    elif tool == "run_shell_command" and rows:
        meta = rows[0]
        st.code(meta["command"], language="powershell")
        c1, c2, c3 = st.columns(3)
        c1.metric("Exit code", meta["exit_code"])
        c2.metric("Duration", meta["duration"])
        c3.caption(f"Ran in: {meta['working_directory']}")
        st.text_area("stdout (untrusted command output)", res.get("stdout", ""), height=240,
                     key=f"out_{key}", disabled=True)
        if res.get("stderr"):
            st.text_area("stderr", res["stderr"], height=140, key=f"err_{key}", disabled=True)
    elif tool in ("open_file_location", "open_file") and rows:
        st.success(" · ".join(res.get("notes") or ["Done."]))
        st.code(rows[0]["path"], language=None)
    elif tool == "web_search" and rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     column_config={"url": st.column_config.LinkColumn("url")})
    elif rows:
        path_table(pd.DataFrame(rows), key)
    else:
        st.info("No results.")

    notes = res.get("notes") or []
    st.caption(" · ".join([f"{res.get('count', 0)} row(s)"] + notes))


def parse_tool_message(msg: ToolMessage) -> dict:
    try:
        data = json.loads(msg.content)
        if isinstance(data, dict):
            return data
    except (TypeError, ValueError):
        pass
    return {"ok": False, "tool": msg.name, "error": str(msg.content)}


def load_history() -> list:
    """Rebuild the chat view from the saved conversation (Postgres or in-memory)."""
    try:
        state = get_agent().get_state({"configurable": {"thread_id": st.session_state.thread_id}})
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


# ---------- chat ----------

def run_agent(agent, thread_id: str, prompt: str, drive: str):
    """Runs in a worker thread, so it must not touch st.session_state (agent and thread_id are passed in)."""
    hint = f"\n\n(UI drive filter: {drive}: only)" if drive != "all" else ""
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": SETTINGS["agent"]["recursion_limit"],
    }
    try:
        out = agent.invoke({"messages": [HumanMessage(content=prompt + hint)]}, config)
    except GraphRecursionError:
        return "⚠️ The agent took too many steps. Try a more specific request.", []
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Agent error: {type(exc).__name__}: {exc}", []

    msgs = out["messages"]
    last_human = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
    new = msgs[last_human + 1:]
    results = [parse_tool_message(m) for m in new if isinstance(m, ToolMessage)]
    final = next((m.content for m in reversed(new) if isinstance(m, AIMessage) and m.content), "(no answer)")
    return final if isinstance(final, str) else str(final), results


def run_timed(prompt: str, drive: str):
    """Run the agent in a worker thread and show a live stopwatch until it finishes."""
    agent, thread_id = get_agent(), st.session_state.thread_id
    box = {}

    def work():
        try:
            box["out"] = run_agent(agent, thread_id, prompt, drive)
        except Exception as exc:  # noqa: BLE001
            box["out"] = (f"⚠️ Agent error: {type(exc).__name__}: {exc}", [])

    worker = threading.Thread(target=work, daemon=True)
    clock = st.empty()
    start = time.perf_counter()
    worker.start()
    while worker.is_alive():
        clock.caption(f"⏱️ Working… {time.perf_counter() - start:.1f} s")
        worker.join(0.2)
    elapsed = time.perf_counter() - start
    clock.empty()
    text, results = box["out"]
    return text, results, elapsed


def chat_mode(drive: str):
    st.caption("Try: “list C:\\Users\\CT_USER\\Desktop”, “find files named invoice”, “find all .pdf files in D:\\”, "
               "“search my Documents for 'budget'”, “find duplicates in my Documents”, “search the web for latest Streamlit release”, “show disk usage”, “run Get-Date”.")
    for i, item in enumerate(st.session_state.history):
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
            for j, res in enumerate(item["results"]):
                render_result(res, f"c{i}_{j}")
            if item.get("elapsed") is not None:
                st.caption(f"⏱️ Completed in {item['elapsed']:.1f} s")

    prompt = st.chat_input("Ask about your files…")
    if not prompt:
        return
    st.session_state.history.append({"role": "user", "content": prompt, "results": []})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        text, results, elapsed = run_timed(prompt, drive)
        st.markdown(text)
        idx = len(st.session_state.history)
        for j, res in enumerate(results):
            render_result(res, f"c{idx}_{j}")
        st.caption(f"⏱️ Completed in {elapsed:.1f} s")
    st.session_state.history.append({"role": "assistant", "content": text, "results": results, "elapsed": elapsed})


# ---------- manual tools ----------

def manual_mode(drive: str):
    approved = permissions.approved_read_dirs()
    read_default = approved[0] if approved else HOME

    write_dirs = permissions.write_dirs()
    write_default = write_dirs[0] if write_dirs else HOME

    group = st.selectbox("Tool group", list(GROUPS), format_func=str.capitalize)
    name = st.selectbox("Tool", [t.name for t in GROUPS[group]])
    st.caption(TOOLS_BY_NAME[name].description.strip().splitlines()[0])

    with st.form(f"manual_{name}"):
        args = {}
        if name == "list_directory":
            args["path"] = st.text_input("Directory path", HOME)
            args["max_items"] = int(st.number_input("Max items", 1, 500, 200))
        elif name == "search_files":
            args["query"] = st.text_input("Filename (partial, case-insensitive; * and ? allowed)")
            args["directory"] = st.text_input("Limit to directory (optional, blank = whole selected drive)")
            args["drive"] = drive
            args["max_results"] = int(st.number_input("Max results", 1, 500, 100))
        elif name == "search_by_extension":
            args["extension"] = st.text_input("Extension(s)", ".pdf", help="e.g. .py or .txt,.csv")
            args["directory"] = st.text_input("Limit to directory (optional, blank = whole selected drive)")
            args["name_contains"] = st.text_input("Filename must contain (optional)")
            args["drive"] = drive
            args["max_results"] = int(st.number_input("Max results", 1, 500, 100))
        elif name == "search_by_content":
            args["query"] = st.text_input("Text to find (case-insensitive)")
            args["directory"] = st.text_input("Directory (must be inside an approved read folder)", read_default)
            args["extensions"] = st.text_input("Extensions (optional)", help="e.g. .py,.md")
            args["max_results"] = int(st.number_input("Max matches", 1, 500, 50))
        elif name == "read_file":
            args["path"] = st.text_input("Full file path (.pdf, .docx, .csv, .json, text; inside an approved read folder)",
                                         read_default)
            args["pages"] = st.text_input("PDF pages (optional)", help="e.g. 1-5")
            args["max_chars"] = int(st.number_input("Max characters", 500, 50000, 12000))
        elif name == "get_file_info":
            args["path"] = st.text_input("Full file path")
        elif name == "find_duplicates":
            args["directory"] = st.text_input("Directory (must be inside an approved read folder)", read_default)
            args["extensions"] = st.text_input("Extensions (optional)")
            args["min_size_kb"] = int(st.number_input("Minimum size (KB)", 0, 1_000_000, 1))
        elif name == "create_file":
            args["path"] = st.text_input("New file path (.txt, .md, .py, .json, ...)", os.path.join(write_default, "new_file.txt"))
            args["content"] = st.text_area("Content (optional)")
        elif name == "write_file":
            args["path"] = st.text_input("File path", os.path.join(write_default, "notes.txt"))
            args["content"] = st.text_area("Content")
            args["overwrite"] = st.checkbox("Overwrite if the file exists (needs approval)")
        elif name == "append_file":
            args["path"] = st.text_input("Existing file path")
            args["content"] = st.text_area("Text to add at the end")
            args["newline_before"] = st.checkbox("Start on a new line", True)
        elif name == "replace_text":
            args["path"] = st.text_input("Existing file path")
            args["old_text"] = st.text_input("Text to find (exact, case-sensitive)")
            args["new_text"] = st.text_input("Replace with")
            args["replace_all"] = st.checkbox("Replace all occurrences", True)
        elif name == "rename_file":
            args["path"] = st.text_input("File or folder path")
            args["new_name"] = st.text_input("New name only (no folder)")
        elif name in ("copy_file", "move_file"):
            args["source"] = st.text_input("Source file path")
            args["destination"] = st.text_input("Destination folder or full file path", write_default)
            args["overwrite"] = st.checkbox("Replace if the destination exists (needs approval)")
        elif name == "create_directory":
            args["path"] = st.text_input("New folder path", os.path.join(write_default, "new_folder"))
        elif name == "delete_file":
            args["path"] = st.text_input("File to delete (goes to the Recycle Bin)")
        elif name == "delete_directory":
            args["path"] = st.text_input("Folder to delete (goes to the Recycle Bin)")
            args["recursive"] = st.checkbox("Also delete everything inside it")
        elif name in ("get_system_info", "get_current_directory"):
            st.caption("No input needed.")
        elif name == "get_disk_usage":
            args["drive"] = drive
        elif name == "run_shell_command":
            args["command"] = st.text_area("PowerShell command (needs your Approve click)", height=100,
                                           help="e.g. Get-Date  or  ipconfig  or  Get-ComputerInfo")
            args["working_directory"] = st.text_input("Working folder (optional, blank = your home folder)")
            args["timeout_seconds"] = int(st.number_input("Timeout (seconds, 0 = default)", 0, 120, 0))
        elif name == "open_file_location":
            args["path"] = st.text_input("File or folder path (shown in Explorer)")
        elif name == "open_file":
            args["path"] = st.text_input("File to open in its default application (needs your Approve click)")
        elif name == "read_clipboard":
            args["max_chars"] = int(st.number_input("Max characters", 100, 20000, 5000))
            st.caption("Needs your Approve click. The text is shown only to you unless you ask the assistant to fetch it.")
        elif name == "create_note":
            args["title"] = st.text_input("Title")
            args["content"] = st.text_area("Note text")
            args["format"] = st.selectbox("Format", ["md", "txt"])
            args["folder"] = st.text_input("Folder (optional, blank = notes folder from config.yaml)")
        elif name == "extract_text":
            args["path"] = st.text_input("Full file path (pdf, docx, pptx, xlsx, csv, json, html, text, image)", read_default)
            args["engine"] = st.selectbox("Engine", ["local", "whisperer"],
                                          help="whisperer UPLOADS the file to LLMWhisperer (off by default, needs approval)")
            args["pages"] = st.text_input("PDF pages (optional)", help="e.g. 1-5")
            args["max_chars"] = int(st.number_input("Max characters", 500, 50000, 12000))
        elif name == "convert_document":
            args["source"] = st.text_input("Source file path")
            args["target_format"] = st.text_input("Convert to (extension)", help="e.g. pdf, docx, md, txt, csv, json, xlsx, png, jpg, webp")
            args["destination"] = st.text_input("Destination folder or file (optional)")
            args["overwrite"] = st.checkbox("Replace if the destination exists (needs approval)")
        elif name == "generate_report":
            args["kind"] = st.selectbox("Report about", ["search", "operations"])
            args["title"] = st.text_input("Title (optional)")
            args["query"] = st.text_input("File name contains (search)")
            args["extensions"] = st.text_input("Extensions (search, optional)", help="e.g. .pdf,.docx")
            args["directory"] = st.text_input("Directory (search, optional)")
            args["drive"] = drive
            args["days"] = int(st.number_input("Days back (operations)", 0, 3650, 7))
            args["tool_filter"] = st.text_input("Only this tool (operations, optional)")
            args["format"] = st.selectbox("Format", ["md", "html"])
        elif name == "file_operation_history":
            args["limit"] = int(st.number_input("Entries", 1, 500, 50))
            args["tool"] = st.text_input("Only this tool (optional)")
            args["status"] = st.selectbox("Status", ["", "done", "failed", "pending", "approved", "rejected", "expired", "external_upload"])
            args["days"] = int(st.number_input("Last N days (0 = all)", 0, 3650, 0))
        elif name == "get_approved_result":
            args["request_id"] = st.text_input("Request id (pending_id)")
        elif name == "web_search":
            args["query"] = st.text_input("Search query")
            args["topic"] = st.selectbox("Topic", ["general", "news"])
            args["max_results"] = int(st.number_input("Max results", 1, 10, 5))
        elif name == "research_report":
            args["topic"] = st.text_input("Topic to research")
            args["pages"] = int(st.number_input("Pages (3-8)", 3, 8, 4))
            args["output"] = st.selectbox("Output", ["pdf", "md"])
            st.caption("Runs several web searches and writes the report section by section. Takes a few minutes.")
        run = st.form_submit_button("Run tool", type="primary")

    if run:
        started = time.perf_counter()
        with st.spinner(f"Running {name}…"):
            try:
                result = json.loads(TOOLS_BY_NAME[name].invoke(args))
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "tool": name, "error": f"Invalid input: {exc}"}
        st.session_state.manual_elapsed = time.perf_counter() - started
        st.session_state.manual = result
        st.session_state.manual_n = st.session_state.get("manual_n", 0) + 1

    if st.session_state.manual:
        st.divider()
        st.caption(f"⏱️ Tool call took {st.session_state.get('manual_elapsed', 0):.2f} s")
        render_result(st.session_state.manual, f"manual{st.session_state.get('manual_n', 0)}")


# ---------- layout ----------

init_state()

with st.sidebar:
    st.title("🗂️ Personal AI Agent")
    mode = st.radio("Mode", ["Chat", "Manual tools"])
    drive = st.selectbox("Drive", ["all", "C", "D"],
                         format_func=lambda d: "All (C: + D:)" if d == "all" else f"{d}:")
    if permissions.allow_all_reads():
        st.warning("Reading is allowed on all of C: and D: (allow_all_reads is on). "
                   "System folders and sensitive file names are still blocked.")
    else:
        st.markdown("**Approved read folders**")
        for folder in permissions.approved_read_dirs() or ["(none configured)"]:
            st.code(folder, language=None)
    st.caption("Edit in config.yaml or APPROVED_READ_DIRS in .env, then restart.")

    if not permissions.modify_enabled():
        st.info("File changes are turned off (modify.enabled).")
    elif permissions.allow_all_writes():
        st.error("Changes are allowed on all of C: and D: (allow_all_writes is on).")
    else:
        st.markdown("**Write folders**")
        for folder in permissions.write_dirs() or ["(none configured)"]:
            st.code(folder, language=None)
    if permissions.modify_enabled():
        st.caption("✅ Risky changes need your Approve click" if guard.confirmations_required()
                   else "⚠️ confirm_changes is off: changes run immediately")

    if permissions.computer_enabled():
        st.caption("✅ Shell commands and open_file need your Approve click" if permissions.computer_confirm_required()
                   else "⚠️ computer.confirm_actions is off: shell commands run immediately")
    else:
        st.info("Computer tools are turned off (computer.enabled).")

    if SETTINGS.get("whisperer", {}).get("enabled"):
        st.warning("☁️ LLMWhisperer is ON: engine='whisperer' uploads files to the cloud (each upload needs your Approve click)."
                   if os.getenv("LLMWHISPERER_API_KEY") else "LLMWhisperer is on but LLMWHISPERER_API_KEY is missing in .env.")
    st.markdown(f"**Model:** `{SETTINGS['llm']['model']}`")
    st.caption("Web search: ✅ Tavily key found" if os.getenv("TAVILY_API_KEY") else "Web search: ⚠️ TAVILY_API_KEY missing in .env")
    if st.button("Check model connection"):
        level, message = check_ollama()
        {"ok": st.success, "warn": st.warning, "error": st.error}[level](message)

    if st.button("🗑️ Clear chat history"):
        clear_chat()
        st.rerun()

try:
    get_agent()
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not build the agent: {type(exc).__name__}: {exc}")
    st.stop()

if st.session_state.history is None:
    st.session_state.history = load_history()

with st.sidebar:
    st.caption(f"Memory: {memory_status['detail']}")

if mode == "Chat":
    chat_mode(drive)
else:
    manual_mode(drive)