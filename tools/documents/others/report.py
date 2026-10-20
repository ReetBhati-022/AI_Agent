"""generate_report: save a Markdown or HTML report of a file search or of the agent's own file operations."""
import html as html_lib
import json
import os
from collections import Counter
from datetime import datetime

from langchain_core.tools import tool

from security import permissions
from security.settings import get_settings
from tools.audit.history import history_rows
from tools.common import fail, guarded, ok
from tools.documents.others.notes import slugify, unique_path
from security import guard
from tools.documents.files.file_edit import _ensure_parent, _write_atomic
from security.guard import row
from tools.documents.files.discovery import search_by_extension, search_files

SEARCH_COLUMNS = [("name", "Name"), ("extension", "Type"), ("size", "Size"), ("modified", "Modified"), ("path", "Path")]
OPS_COLUMNS = [("time", "Time"), ("tool", "Tool"), ("status", "Status"), ("description", "Description"),
               ("path", "Path"), ("detail", "Detail")]


def _md_cell(value) -> str:
    text = str(value if value is not None else "").replace("\\", "\\\\").replace("|", "\\|")
    return " ".join(text.replace("`", "'").replace("<", "&lt;").replace(">", "&gt;").split())


def _md_table(columns: list, rows: list) -> str:
    head = "| " + " | ".join(label for _, label in columns) + " |"
    sep = "|" + " --- |" * len(columns)
    body = ["| " + " | ".join(_md_cell(r.get(key, "")) for key, _ in columns) + " |" for r in rows]
    return "\n".join([head, sep] + body)


def _html_table(columns: list, rows: list) -> str:
    head = "".join(f"<th>{html_lib.escape(label)}</th>" for _, label in columns)
    body = "".join("<tr>" + "".join(f"<td>{html_lib.escape(str(r.get(key, '')))}</td>" for key, _ in columns) + "</tr>"
                   for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _counts_table(title_a: str, counter: Counter) -> tuple:
    rows = [{"a": k or "(none)", "b": v} for k, v in counter.most_common(15)]
    return [("a", title_a), ("b", "Count")], rows


def _sections_search(rows: list) -> list:
    by_ext = Counter((r.get("extension") or "(folder / none)") for r in rows)
    newest = max((r.get("modified", "") for r in rows), default="")
    oldest = min((r.get("modified", "") for r in rows), default="")
    facts = [("Results", str(len(rows))), ("Newest change", newest or "-"), ("Oldest change", oldest or "-")]
    cols, summary_rows = _counts_table("File type", by_ext)
    return [("facts", facts), ("By file type", cols, summary_rows), ("Results", SEARCH_COLUMNS, rows)]


def _sections_ops(rows: list) -> list:
    status = Counter(r.get("status", "") for r in rows)
    tools_used = Counter(r.get("tool", "") for r in rows)
    facts = [("Entries", str(len(rows))), ("First", rows[-1]["time"] if rows else "-"), ("Last", rows[0]["time"] if rows else "-"),
             ("External uploads", str(status.get("external_upload", 0))), ("Failed", str(status.get("failed", 0)))]
    c1, r1 = _counts_table("Status", status)
    c2, r2 = _counts_table("Tool", tools_used)
    return [("facts", facts), ("By status", c1, r1), ("By tool", c2, r2), ("Actions (newest first)", OPS_COLUMNS, rows)]


def render_markdown(title: str, subtitle: str, sections: list) -> str:
    out = [f"# {_md_cell(title)}", "", f"_Generated {datetime.now():%Y-%m-%d %H:%M} by Personal AI Agent. {_md_cell(subtitle)}_", ""]
    for section in sections:
        if section[0] == "facts":
            out.extend(f"- **{_md_cell(k)}:** {_md_cell(v)}" for k, v in section[1])
            out.append("")
        else:
            out.extend([f"## {section[0]}", "", _md_table(section[1], section[2]) if section[2] else "_Nothing to show._", ""])
    return "\n".join(out).rstrip() + "\n"


def render_html(title: str, subtitle: str, sections: list) -> str:
    esc = html_lib.escape
    css = ("body{font-family:Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#1b1b1b}"
           "table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}th,td{border:1px solid #ccc;padding:4px 8px;"
           "text-align:left;font-size:13px;word-break:break-all}th{background:#f0f0f0}.meta{color:#666}")
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title><style>{css}</style></head><body>",
           f"<h1>{esc(title)}</h1><p class='meta'>Generated {datetime.now():%Y-%m-%d %H:%M} by Personal AI Agent. {esc(subtitle)}</p>"]
    for section in sections:
        if section[0] == "facts":
            out.append("<ul>" + "".join(f"<li><b>{esc(k)}:</b> {esc(v)}</li>" for k, v in section[1]) + "</ul>")
        else:
            out.append(f"<h2>{esc(section[0])}</h2>" + (_html_table(section[1], section[2]) if section[2] else "<p><i>Nothing to show.</i></p>"))
    out.append("</body></html>")
    return "".join(out)


@tool
@guarded("generate_report")
def generate_report(kind: str = "search", title: str = "", query: str = "", directory: str = "", extensions: str = "",
                    drive: str = "all", days: int = 7, tool_filter: str = "", format: str = "md", max_rows: int = 200) -> str:
    """Create a saved report file. kind='search': runs a file search and reports the hits. Use `query` (part of a file name) and/or `extensions` (e.g. '.pdf,.docx'), optionally `directory` or `drive` ('C', 'D', 'all'). kind='operations': reports the agent's own file/computer actions from the audit log for the last `days` days (optionally only `tool_filter`, e.g. 'delete_file'). `format` is 'md' or 'html'. The report is a NEW file in the configured reports folder (never overwrites)."""
    cfg = get_settings().get("reports", {})
    kind = (kind or "search").strip().lower()
    fmt = (format or "md").strip().lower().lstrip(".")
    if kind not in ("search", "operations"):
        return fail("generate_report", "kind must be 'search' or 'operations'.")
    if fmt not in ("md", "html"):
        return fail("generate_report", "format must be 'md' or 'html'.")
    try:
        limit = max(1, min(int(max_rows), int(cfg.get("max_rows", 500))))
    except (TypeError, ValueError):
        limit = 200

    if kind == "search":
        query, extensions = (query or "").strip(), (extensions or "").strip()
        if not query and not extensions:
            return fail("generate_report", "A search report needs `query` (part of a file name) and/or `extensions`.")
        if extensions:
            raw = search_by_extension.invoke({"extension": extensions, "directory": directory, "drive": drive,
                                              "name_contains": query, "max_results": limit})
        else:
            raw = search_files.invoke({"query": query, "drive": drive, "directory": directory, "max_results": limit})
        data = json.loads(raw)
        if not data.get("ok"):
            return fail("generate_report", f"The search failed: {data.get('error', 'unknown error')}")
        rows, notes = data.get("rows") or [], list(data.get("notes") or [])
        heading = " ".join((title or "").split()) or f"File search report: {query or extensions}"
        scope = directory.strip() or (f"drive {drive}" if drive and drive.lower() != "all" else "all allowed drives")
        subtitle = f"Search for name '{query or '*'}', types '{extensions or 'any'}', in {scope}."
        sections = _sections_search(rows)
    else:
        rows = history_rows(limit, tool_filter, "", days)
        notes = []
        heading = " ".join((title or "").split()) or "File operations report"
        try:
            span = f"last {int(days)} day(s)" if int(days) > 0 else "all time"
        except (TypeError, ValueError):
            span = "all time"
        subtitle = f"Audit log, {span}" + (f", tool '{tool_filter}'." if tool_filter else ".")
        sections = _sections_ops(rows)

    text = (render_markdown if fmt == "md" else render_html)(heading, subtitle, sections)
    if len(text) > get_settings()["modify"]["max_write_chars"]:
        return fail("generate_report", "The report would be too large. Narrow the search or lower max_rows.")

    folder = str(cfg.get("dir", "")).strip()
    if not folder:
        return fail("generate_report", "No reports folder configured (reports.dir in config.yaml).")
    now = datetime.now()
    stem = f"{now:%Y-%m-%d_%H%M%S}-report-{slugify(kind if not title else title, 'report')}"
    probe = permissions.check_write_path(os.path.join(folder, f"{stem}.{fmt}"), text_only=True)
    folder_real = os.path.dirname(probe)

    def action():
        _ensure_parent(permissions.check_write_path(os.path.join(folder_real, f"{stem}.{fmt}"), text_only=True), True)
        final = permissions.check_write_path(unique_path(folder_real, stem, f".{fmt}"), text_only=True)
        _write_atomic(final, text, exclusive=True)
        return ok("generate_report", [row("report created", final, f"{len(rows)} row(s), {fmt}")],
                  notes + ["Report saved."], preview=text[:6000], format=fmt)

    return guard.submit("generate_report", f"Create {fmt} report in {folder_real}", False, action, folder_real)
