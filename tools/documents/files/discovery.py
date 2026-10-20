"""Directory listing and filename search. Never opens file contents."""
import fnmatch
import os
import time

from langchain_core.tools import tool

from security import permissions
from tools.common import (clamp_results, fail, file_row, guarded, human_size, iso, limits, ok,
                          parse_extensions)
from tools.documents.files.walker import Walker


def _roots(directory: str, drive: str) -> list:
    if directory and directory.strip():
        path = permissions.check_search_path(directory)
        if not os.path.isdir(path):
            raise permissions.PermissionDenied(f"Not a directory: {path}")
        return [path]
    return permissions.drive_roots(drive)


def _collect(roots: list, matches, limit: int, include_dirs: bool = False):
    deadline = time.monotonic() + limits()["search_timeout_seconds"]
    rows, skipped, timed_out, truncated = [], 0, False, False
    for root in roots:
        walker = Walker(root, deadline)
        for entry in walker.files(include_dirs):
            if not matches(entry.name):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            row = file_row(entry.path, stat)
            if include_dirs:
                is_dir = entry.is_dir(follow_symlinks=False)
                row["type"] = "folder" if is_dir else "file"
                if is_dir:
                    row["size"] = ""
                    row["extension"] = ""
            rows.append(row)
            if len(rows) >= limit:
                truncated = True
                break
        skipped += walker.skipped_dirs
        timed_out = timed_out or walker.timed_out
        if truncated:
            break
    notes = []
    if truncated:
        notes.append(f"Stopped at {limit} results; narrow the search for more.")
    if timed_out:
        notes.append("Search time limit reached; results may be incomplete.")
    if skipped:
        notes.append(f"{skipped} inaccessible folder(s) skipped.")
    return rows, notes


def _is_dir(entry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


@tool
@guarded("list_directory")
def list_directory(path: str, max_items: int = 200) -> str:
    """List the files and folders directly inside one directory (not recursive). Returns name, type, size, modified time and full path. `path` must be an absolute Windows path such as C:\\Users\\name\\Desktop."""
    folder = permissions.check_search_path(path)
    if not os.path.isdir(folder):
        return fail("list_directory", f"Not a directory: {folder}")
    limit = clamp_results(max_items, 200)

    with os.scandir(folder) as it:
        entries = sorted(it, key=lambda e: (not _is_dir(e), e.name.lower()))

    rows, hidden = [], 0
    for entry in entries:
        if permissions.is_excluded(entry.path):
            hidden += 1
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if permissions.entry_is_link(entry):
            kind, size = "link (not followed)", ""
        elif _is_dir(entry):
            kind, size = "folder", ""
        else:
            kind, size = "file", human_size(stat.st_size)
        rows.append({"name": entry.name, "type": kind, "size": size,
                     "modified": iso(stat.st_mtime), "path": entry.path})
        if len(rows) >= limit:
            break

    notes = []
    if len(entries) - hidden > len(rows):
        notes.append(f"Showing first {len(rows)} of {len(entries) - hidden} items.")
    if hidden:
        notes.append(f"{hidden} excluded item(s) hidden.")
    return ok("list_directory", rows, notes, directory=folder)


@tool
@guarded("search_files")
def search_files(query: str, drive: str = "all", directory: str = "", max_results: int = 100) -> str:
    """Search file AND folder NAMES (not contents) by partial, case-insensitive match; * and ? wildcards also work. `drive` is 'C', 'D' or 'all'. Optionally limit to one absolute `directory`. Returns name, extension, size, modified time and path."""
    q = (query or "").strip().lower()
    if not q:
        return fail("search_files", "Provide a filename or part of one.")
    if any(c in q for c in "*?"):
        matches = lambda name: fnmatch.fnmatch(name.lower(), q)  # noqa: E731
    else:
        matches = lambda name: q in name.lower()  # noqa: E731
    roots = _roots(directory, drive)
    rows, notes = _collect(roots, matches, clamp_results(max_results), include_dirs=True)
    return ok("search_files", rows, notes, searched=roots)


@tool
@guarded("search_by_extension")
def search_by_extension(extension: str, directory: str = "", drive: str = "all",
                        name_contains: str = "", max_results: int = 100) -> str:
    """Find files by extension, e.g. '.py', 'pdf' or '.txt,.csv' (comma separated). Optionally limit to one absolute `directory` (otherwise searches the chosen `drive`: 'C', 'D' or 'all') and optionally require `name_contains` in the filename."""
    wanted = parse_extensions(extension)
    if not wanted:
        return fail("search_by_extension", "Provide at least one extension, for example .py or .pdf.")
    part = (name_contains or "").strip().lower()

    def matches(name: str) -> bool:
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name.strip(".") else ""
        return ext in wanted and (not part or part in name.lower())

    roots = _roots(directory, drive)
    rows, notes = _collect(roots, matches, clamp_results(max_results))
    return ok("search_by_extension", rows, notes, searched=roots, extensions=sorted(wanted))                                                                                 
                                                 