"""create_note: new .txt / .md notes in the notes folder (always a NEW file, never overwrites)."""
import os
import re
from datetime import datetime

from langchain_core.tools import tool

from security import permissions
from security.settings import get_settings
from tools.common import fail, guarded, ok
from security import guard
from tools.documents.files.file_edit import _check_content, _ensure_parent, _write_atomic
from security.guard import row


def slugify(title: str, fallback: str = "note") -> str:
    """Safe Windows file-name stem from a title (letters, digits, dash, underscore)."""
    text = re.sub(r"[^\w\- ]+", "", (title or "").strip(), flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text).strip("-_.")[:60]
    return text or fallback


def unique_path(folder: str, stem: str, ext: str) -> str:
    """folder\\stem.ext, or stem-2.ext, stem-3.ext ... so an existing file is never touched."""
    candidate = os.path.join(folder, f"{stem}{ext}")
    n = 2
    while os.path.lexists(candidate):
        candidate = os.path.join(folder, f"{stem}-{n}{ext}")
        n += 1
        if n > 999:
            raise permissions.PermissionDenied("Too many notes with the same title.")
    return candidate


@tool
@guarded("create_note")
def create_note(title: str, content: str = "", format: str = "md", folder: str = "") -> str:
    """Create a NEW note file (.md Markdown or .txt) from a `title` and `content`. The file name is the date plus the title, and an existing note is never overwritten. `format` is 'md' or 'txt'. `folder` is optional (absolute path inside an allowed write folder); the default is the configured notes folder."""
    cfg = get_settings().get("notes", {})
    fmt = (format or "md").strip().lower().lstrip(".")
    if fmt not in ("md", "txt"):
        return fail("create_note", "Format must be 'md' or 'txt'.")
    title = " ".join((title or "").split())  # one line, no injected markdown lines
    if not title:
        return fail("create_note", "A note needs a title.")
    if len(title) > 200:
        return fail("create_note", "Title is longer than 200 characters.")
    body = _check_content(content or "")
    if len(body) > int(cfg.get("max_chars", 200000)):
        return fail("create_note", f"Note is longer than {int(cfg.get('max_chars', 200000)):,} characters.")

    target_dir = (folder or "").strip() or str(cfg.get("dir", ""))
    if not target_dir:
        return fail("create_note", "No notes folder configured (notes.dir in config.yaml).")

    now = datetime.now()
    stem = f"{now:%Y-%m-%d}-{slugify(title)}"
    # validate the folder + name before doing anything (raises PermissionDenied with a clear message)
    probe = permissions.check_write_path(os.path.join(target_dir, f"{stem}.{fmt}"), text_only=True)
    folder_real = os.path.dirname(probe)

    if fmt == "md":
        text = f"# {title}\n\n_{now:%Y-%m-%d %H:%M}_\n\n{body}".rstrip() + "\n"
    else:
        text = f"{title}\n{now:%Y-%m-%d %H:%M}\n\n{body}".rstrip() + "\n"
    _check_content(text)

    def action():
        _ensure_parent(permissions.check_write_path(os.path.join(folder_real, f"{stem}.{fmt}"), text_only=True), True)
        final = permissions.check_write_path(unique_path(folder_real, stem, f".{fmt}"), text_only=True)
        _write_atomic(final, text, exclusive=True)
        return ok("create_note", [row("note created", final, f"{len(text):,} characters")], ["Note saved."])

    return guard.submit("create_note", f"Create note in {folder_real}: {title}", False, action, folder_real)
