"""File metadata and duplicate detection (read-only)."""
import hashlib
import os
import time
from collections import defaultdict

from langchain_core.tools import tool

from security import permissions
from tools.common import clamp_results, fail, guarded, human_size, iso, limits, ok, parse_extensions
from tools.documents.files.walker import Walker

QUICK_BYTES = 64 * 1024
CHUNK_BYTES = 1024 * 1024


@tool
@guarded("get_file_info")
def get_file_info(path: str) -> str:
    """Show metadata for ONE file: name, full path, extension, size and last modified date. Does not read the file's contents. `path` must be an absolute Windows path."""
    resolved = permissions.check_search_path(path)
    if not os.path.exists(resolved):
        return fail("get_file_info", f"File not found: {resolved}")
    if os.path.isdir(resolved):
        return fail("get_file_info", f"That is a folder, not a file. Use list_directory: {resolved}")
    stat = os.stat(resolved)
    name = os.path.basename(resolved)
    row = {
        "name": name,
        "full_path": resolved,
        "extension": os.path.splitext(name)[1].lower() or "(none)",
        "size": f"{human_size(stat.st_size)} ({stat.st_size:,} bytes)",
        "last_modified": iso(stat.st_mtime),
        "created": iso(stat.st_ctime),
        "content_readable": "yes (approved folder)" if permissions.is_approved_for_reading(resolved) else "no (outside approved folders)",
    }
    return ok("get_file_info", [row])


def _sha256(path: str, quick: bool):
    try:
        real = permissions.check_readable_file(path)
        digest = hashlib.sha256()
        with open(real, "rb") as fh:
            if quick:
                digest.update(fh.read(QUICK_BYTES))
            else:
                for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
                    digest.update(chunk)
        return digest.hexdigest()
    except (OSError, permissions.PermissionDenied):
        return None


def _split_by_hash(items: list, quick: bool) -> dict:
    buckets = defaultdict(list)
    for path, mtime in items:
        digest = _sha256(path, quick)
        if digest:
            buckets[digest].append((path, mtime))
    return {h: v for h, v in buckets.items() if len(v) > 1}


@tool
@guarded("find_duplicates")
def find_duplicates(directory: str, min_size_kb: int = 1, extensions: str = "") -> str:
    """Find duplicate files (identical SHA-256 hash) below one directory. Read-only: never deletes, moves or modifies anything. Hashing reads file bytes, so `directory` must be inside an approved read directory. Optional `extensions` filter (e.g. '.jpg,.pdf') and `min_size_kb`."""
    root = permissions.check_read_dir(directory)
    lim = limits()
    min_size = max(1, int(min_size_kb) * 1024) if min_size_kb else 1
    max_size = lim["max_duplicate_file_size_mb"] * 1024 * 1024
    wanted = parse_extensions(extensions)

    deadline = time.monotonic() + lim["duplicate_timeout_seconds"]
    walker = Walker(root, deadline)
    by_size, too_large = defaultdict(list), 0
    for entry in walker.files():
        if wanted:
            ext = ("." + entry.name.rsplit(".", 1)[-1].lower()) if "." in entry.name.strip(".") else ""
            if ext not in wanted:
                continue
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.st_size < min_size:
            continue
        if stat.st_size > max_size:
            too_large += 1
            continue
        if permissions.is_blocked_filename(entry.name):
            continue
        by_size[stat.st_size].append((entry.path, stat.st_mtime))

    groups, timed_out = [], walker.timed_out
    for size, items in by_size.items():
        if len(items) < 2:
            continue
        if time.monotonic() > deadline:
            timed_out = True
            break
        for same_start in _split_by_hash(items, quick=True).values():
            for digest, dupes in _split_by_hash(same_start, quick=False).items():
                groups.append({
                    "hash": digest,
                    "size": human_size(size),
                    "size_bytes": size,
                    "copies": len(dupes),
                    "wasted": human_size(size * (len(dupes) - 1)),
                    "wasted_bytes": size * (len(dupes) - 1),
                    "files": [{"path": p, "size": human_size(size), "modified": iso(m)} for p, m in dupes],
                })

    groups.sort(key=lambda g: g["wasted_bytes"], reverse=True)
    groups = groups[: clamp_results(100)]
    rows = [{"group": i, "size": g["size"], "modified": f["modified"], "path": f["path"]}
            for i, g in enumerate(groups, start=1) for f in g["files"]]

    notes = ["Read-only: nothing was deleted, moved or modified."]
    if timed_out:
        notes.append("Time limit reached; results may be incomplete.")
    if too_large:
        notes.append(f"{too_large} file(s) skipped (over {lim['max_duplicate_file_size_mb']} MB).")
    if walker.skipped_dirs:
        notes.append(f"{walker.skipped_dirs} inaccessible folder(s) skipped.")
    return ok("find_duplicates", rows, notes, groups=groups, directory=root)
