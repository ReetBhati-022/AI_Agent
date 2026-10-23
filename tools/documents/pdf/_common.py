"""Shared helpers for all PDF tools (organize/ and optimize/).

Rules: the original file is never changed (the result is a NEW file); reading goes through check_read_file,
writing through check_write_path; a new file is created at once, REPLACING one needs overwrite=true + Approve.
A ToolError is a mistake the person can fix; it becomes a clean JSON error through @guarded.
"""
import os
import re
import uuid

from security import guard, permissions
from security.guard import row
from security.settings import get_settings
from tools.common import human_size, ok

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


class ToolError(permissions.PermissionDenied):
    """Bad input (page range, wrong file type, ...). Subclass of PermissionDenied so @guarded reports it cleanly."""


def cfg(key: str, default):
    return (get_settings().get("pdf_tools") or {}).get(key, default)


def check_enabled():
    if not cfg("enabled", True):
        raise ToolError("PDF tools are turned off (pdf_tools.enabled: false in config.yaml).")


# ------------------------------------------------------------------ input
def split_paths(text) -> list:
    """One path per line (or separated by ';'); quotes around a path are removed."""
    return [p.strip().strip("\"'") for p in re.split(r"[\r\n;]+", text or "") if p.strip()]


def read_source(raw: str, exts=(".pdf",), label: str = "a PDF") -> str:
    """Permission + type + size check of one input file. Returns the real path."""
    real = permissions.check_read_file(raw)
    ext = os.path.splitext(real)[1].lower()
    if ext not in exts:
        raise ToolError(f"Not {label} file ({ext or 'no extension'}): {real}")
    if os.path.getsize(real) > cfg("max_source_mb", 100) * 1024 * 1024:
        raise ToolError(f"{os.path.basename(real)} is over the pdf_tools.max_source_mb limit.")
    return real


def one(source: str):
    """Input list for tools that take a single PDF."""
    return lambda: [read_source(source)]


def open_pdf(real: str):
    """Open a PDF (damaged files are repaired on open by MuPDF). Password-protected files are refused."""
    import pymupdf  # lazy: the app starts even if pymupdf is missing

    try:
        doc = pymupdf.open(real)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Could not open {os.path.basename(real)} as a PDF ({exc}).") from exc
    name = os.path.basename(real)
    if doc.needs_pass:
        raise ToolError(f"{name} is password-protected. Remove the password first.")
    if doc.page_count == 0:
        raise ToolError(f"{name} has no pages.")
    if doc.page_count > cfg("max_pages", 2000):
        raise ToolError(f"{name} has {doc.page_count} pages, over the pdf_tools.max_pages limit.")
    return doc


def parse_pages(spec: str, total: int, repeat: bool = False) -> list:
    """'1-3,5,8-' -> 0-based page indexes in the order written. '7-' = to the end, '-3' = first 3, 'last',
    'all', '5-2' counts down. Duplicates are dropped unless repeat=True."""
    spec = (spec or "").strip().lower()
    if spec == "all":
        return list(range(total))
    out = []
    for part in re.split(r"[,;\s]+", spec):
        if not part:
            continue
        m = re.fullmatch(r"(\d+|last)?(-)?(\d+|last)?", part)
        if not m or not (m.group(1) or m.group(3)):
            raise ToolError(f"Cannot read '{part}' in the page list. Use numbers and ranges like 1-3,5,8-")
        a, dash, b = m.groups()
        num = lambda t, d: total if t == "last" else int(t) if t else d  # noqa: E731
        first = num(a, 1)
        last = num(b, total) if dash else first
        for n in (first, last):
            if not 1 <= n <= total:
                raise ToolError(f"Page {n} does not exist - this PDF has {total} page(s).")
        step = 1 if first <= last else -1
        out += [n - 1 for n in range(first, last + step, step) if repeat or (n - 1) not in out]
    if not out:
        raise ToolError("Say which pages (for example 1-3,5,8-).")
    return out


# ------------------------------------------------------------------ output
def pick_output(first: str, destination: str, suffix: str) -> str:
    """Blank -> next to the first input as '<name><suffix>.pdf'; '...pdf' -> that file; otherwise a folder."""
    name = os.path.splitext(os.path.basename(first))[0] + suffix + ".pdf"
    dest = (destination or "").strip().strip("\"'")
    if not dest:
        return os.path.join(os.path.dirname(first), name)
    return dest if dest.lower().endswith(".pdf") else os.path.join(dest, name)


def check_output(raw: str, overwrite: bool) -> tuple:
    """Permission-check one output path -> (final_path, already_exists)."""
    final = permissions.check_write_path(raw, check_ext=True)
    exists = os.path.lexists(final)
    if exists and (os.path.isdir(final) or not overwrite):
        raise ToolError(f"Already exists: {final}. Pass overwrite=true (the user must approve) or choose another destination.")
    return final, exists


def save_pdf(doc, final: str, **opts) -> int:
    """Write via a temp file so a failure never leaves half a PDF. Returns the size."""
    os.makedirs(os.path.dirname(final), exist_ok=True)
    tmp = os.path.join(os.path.dirname(final), f".{uuid.uuid4().hex[:8]}.tmp.pdf")
    try:
        doc.save(tmp, **({"garbage": 3, "deflate": True} | opts))
        os.replace(tmp, final)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return os.path.getsize(final)


def submit(tool: str, desc: str, exists: bool, run, path: str) -> str:
    """Run now, or (when it would replace a file) wait for the user's Approve click."""
    return guard.submit(tool, desc, exists, run, path)


def make_pdf(tool, inputs, destination, overwrite, suffix, build, verb, save=None, check_smaller=False) -> str:
    """The flow every 'PDF(s) in -> one new PDF out' tool shares.

    inputs()      -> list of checked input paths (raises ToolError)
    build(paths)  -> (PyMuPDF document to save, short detail text)
    """
    check_enabled()

    def plan():
        reals = inputs()
        final, exists = check_output(pick_output(reals[0], destination, suffix), overwrite)
        if any(os.path.normcase(os.path.abspath(final)) == os.path.normcase(os.path.abspath(r)) for r in reals):
            raise ToolError("The destination is one of the source files. Choose another name or folder.")
        return reals, final, exists

    def run():
        reals, final, was = plan()  # checked again at run time (also after an Approve click)
        doc, detail = build(reals)
        try:
            size = save_pdf(doc, final, **(save or {}))
        finally:
            doc.close()
        notes = ["The original file(s) were not changed."]
        if len(reals) == 1:
            before = os.path.getsize(reals[0])
            detail += f"; {human_size(before)} -> {human_size(size)}"
            if check_smaller and size >= before:
                notes.append("The file did not get smaller (it was already compact). Try a stronger level.")
        else:
            detail += f"; {human_size(size)}"
        return ok(tool, [row("overwritten" if was else verb, final, detail)], notes)

    reals, final, exists = plan()
    desc = f"{verb}: {', '.join(os.path.basename(r) for r in reals[:3])} -> {final}" + (" (REPLACING the existing file)" if exists else "")
    return submit(tool, desc, exists, run, final)
