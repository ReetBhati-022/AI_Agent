"""Text-file editing tools: create_file, write_file, append_file, replace_text.

Only text types (see content.supported_extensions) can be written. Content is always plain data.
Every path goes through permissions.check_write_path (twice: when called and again when approved).
"""
import os
import uuid

from langchain_core.tools import tool

from security import permissions
from security.settings import get_settings
from tools.common import fail, guarded, human_size, ok
from security import guard
from security.guard import row


def _cfg() -> dict:
    return get_settings()["modify"]


def _check_content(content) -> str:
    if not isinstance(content, str):
        raise permissions.PermissionDenied("Content must be text.")
    if len(content) > _cfg()["max_write_chars"]:
        raise permissions.PermissionDenied(f"Content is longer than {_cfg()['max_write_chars']:,} characters.")
    if "\x00" in content:
        raise permissions.PermissionDenied("Content contains binary (NUL) characters.")
    return content


def _write_atomic(target: str, text: str, exclusive: bool):
    """Write via a temp file in the same folder, then swap it in, so a crash never leaves a half-written file."""
    if exclusive:
        with open(target, "x", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return
    tmp = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_text_for_edit(target: str) -> str:
    """Strict UTF-8 read of an existing text file (refuses binary/other encodings so nothing gets corrupted)."""
    if not os.path.isfile(target):
        raise permissions.PermissionDenied(f"File not found: {target}")
    limit = _cfg()["max_edit_file_size_mb"] * 1024 * 1024
    size = os.path.getsize(target)
    if size > limit:
        raise permissions.PermissionDenied(f"File is {human_size(size)}, over the {_cfg()['max_edit_file_size_mb']} MB edit limit.")
    with open(target, "rb") as fh:
        data = fh.read()
    if b"\x00" in data[:4096]:
        raise permissions.PermissionDenied("This looks like a binary file; only UTF-8 text files can be edited.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise permissions.PermissionDenied("File is not valid UTF-8 text, so it was left untouched.")


def _ensure_parent(target: str, create_parents: bool):
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        if not create_parents:
            raise permissions.PermissionDenied(f"Folder does not exist: {parent}")
        os.makedirs(parent, exist_ok=True)


@tool
@guarded("create_file")
def create_file(path: str, content: str = "", create_parents: bool = True) -> str:
    """Create a NEW text file (txt, md, py, csv, json, ...) with optional starting `content`. Fails if the file already exists. `path` must be an absolute path inside an allowed write folder. Missing parent folders are created when `create_parents` is true."""
    target = permissions.check_write_path(path, text_only=True)
    text = _check_content(content)
    if os.path.lexists(target):
        return fail("create_file", f"Already exists: {target}. Use write_file with overwrite=true to replace it.")

    def action():
        t = permissions.check_write_path(path, text_only=True)
        _ensure_parent(t, create_parents)
        _write_atomic(t, text, exclusive=True)
        return ok("create_file", [row("created", t, f"{len(text):,} characters")], ["File created."])

    return guard.submit("create_file", f"Create file {target}", False, action, target)


@tool
@guarded("write_file")
def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """Write `content` to a text file, replacing everything in it. Creates the file if it does not exist. If it already exists you must pass overwrite=true, and the user is asked to approve in the app before anything is replaced."""
    target = permissions.check_write_path(path, text_only=True)
    text = _check_content(content)
    exists = os.path.lexists(target)
    if exists and not os.path.isfile(target):
        return fail("write_file", f"That is a folder, not a file: {target}")
    if exists and not overwrite:
        return fail("write_file", f"File already exists: {target}. Pass overwrite=true to replace its content, or use append_file / replace_text.")

    def action():
        t = permissions.check_write_path(path, text_only=True)
        _ensure_parent(t, True)
        _write_atomic(t, text, exclusive=False)
        return ok("write_file", [row("overwritten" if exists else "created", t, f"{len(text):,} characters")], ["File written."])

    desc = f"Overwrite {target} with {len(text):,} characters (old content is lost)" if exists else f"Create file {target}"
    return guard.submit("write_file", desc, exists, action, target)


@tool
@guarded("append_file")
def append_file(path: str, content: str, newline_before: bool = True) -> str:
    """Add `content` to the END of an existing UTF-8 text file without touching the rest. With `newline_before` a line break is inserted first if the file does not end with one. The user must approve in the app."""
    target = permissions.check_write_path(path, text_only=True)
    text = _check_content(content)
    if not text:
        return fail("append_file", "Nothing to append (content is empty).")
    _read_text_for_edit(target)  # validates: exists, text, size limit

    def action():
        t = permissions.check_write_path(path, text_only=True)
        _read_text_for_edit(t)
        prefix = ""
        if newline_before and os.path.getsize(t) > 0:
            with open(t, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) not in (b"\n", b"\r"):
                    prefix = "\n"
        with open(t, "a", encoding="utf-8", newline="") as fh:
            fh.write(prefix + text)
        return ok("append_file", [row("appended", t, f"{len(text):,} characters added")], ["Text appended."])

    return guard.submit("append_file", f"Append {len(text):,} characters to the end of {target}", True, action, target)


@tool
@guarded("replace_text")
def replace_text(path: str, old_text: str, new_text: str, replace_all: bool = True) -> str:
    """Replace `old_text` with `new_text` inside an existing UTF-8 text file (exact, case-sensitive match). With replace_all=false only the first match changes. Fails if `old_text` is not found. The user must approve in the app."""
    target = permissions.check_write_path(path, text_only=True)
    new_text = _check_content(new_text)
    if not isinstance(old_text, str) or not old_text:
        return fail("replace_text", "old_text must not be empty.")
    count = _read_text_for_edit(target).count(old_text)
    if count == 0:
        return fail("replace_text", "The text to replace was not found in the file (matching is exact and case-sensitive).")
    changes = count if replace_all else 1

    def action():
        t = permissions.check_write_path(path, text_only=True)
        current = _read_text_for_edit(t)
        n = current.count(old_text)
        if n == 0:
            return fail("replace_text", "The file changed since the request; the text was not found any more.")
        updated = current.replace(old_text, new_text) if replace_all else current.replace(old_text, new_text, 1)
        _write_atomic(t, updated, exclusive=False)
        return ok("replace_text", [row("replaced", t, f"{n if replace_all else 1} occurrence(s) replaced")], ["Text replaced."])

    short = lambda s: (s[:60] + "…") if len(s) > 60 else s  # noqa: E731
    desc = f"In {target}: replace {changes} occurrence(s) of “{short(old_text)}” with “{short(new_text)}”"
    return guard.submit("replace_text", desc, True, action, target)
