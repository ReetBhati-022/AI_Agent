"""File/folder management tools: rename_file, copy_file, move_file, create_directory, delete_file, delete_directory.

Deleting sends items to the Recycle Bin by default (modify.delete_mode: trash), so they can be restored.
"""
import os
import shutil
import uuid

from langchain_core.tools import tool

from security import permissions
from security.settings import get_settings
from tools.common import fail, guarded, human_size, ok
from security import guard
from security.guard import row


def _cfg() -> dict:
    return get_settings()["modify"]


def _resolve_destination(source_name: str, destination: str, **kw) -> str:
    """`destination` may be a full file path or an existing folder (then the source name is kept)."""
    dest = permissions.check_write_path(destination, **kw)
    if os.path.isdir(dest):
        dest = permissions.check_write_path(os.path.join(dest, source_name), **kw)
    return dest


def _copy_via_temp(src: str, dest: str):
    tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _dir_summary(path: str, cap: int = 20000):
    files = dirs = total = 0
    truncated = False
    for _root, dnames, fnames in os.walk(path):
        dirs += len(dnames)
        for name in fnames:
            files += 1
            try:
                total += os.path.getsize(os.path.join(_root, name))
            except OSError:
                pass
        if files + dirs > cap:
            truncated = True
            break
    return files, dirs, total, truncated


def _remove(target: str, is_dir: bool):
    """Recycle Bin by default; permanent only when modify.delete_mode is 'permanent'."""
    if _cfg().get("delete_mode", "trash") == "permanent":
        shutil.rmtree(target) if is_dir else os.remove(target)
        return "permanently deleted"
    try:
        from send2trash import send2trash
    except ImportError:
        raise permissions.PermissionDenied(
            "Package 'send2trash' is not installed, so nothing was deleted (it is needed for the Recycle Bin). "
            "Run: python -m pip install send2trash")
    send2trash(target)
    return "moved to the Recycle Bin"


@tool
@guarded("rename_file")
def rename_file(path: str, new_name: str) -> str:
    """Rename ONE file or folder inside its current folder. `new_name` is just a name (no folder path). Fails if the new name already exists. The user must approve in the app."""
    target = permissions.check_write_path(path)
    if not os.path.lexists(target):
        return fail("rename_file", f"Not found: {target}")
    is_dir = os.path.isdir(target)
    if is_dir:
        permissions.check_not_protected(target)
    if not isinstance(new_name, str) or any(c in new_name for c in "\\/"):
        return fail("rename_file", "new_name must be a plain name without folder separators. Use move_file to change folders.")
    new_name = new_name.strip()
    new_path = permissions.check_write_path(os.path.join(os.path.dirname(target), new_name), check_ext=not is_dir)
    if os.path.lexists(new_path) and os.path.normcase(new_path) != os.path.normcase(target):
        return fail("rename_file", f"A file or folder with that name already exists: {new_path}")

    def action():
        src = permissions.check_write_path(path)
        dst = permissions.check_write_path(os.path.join(os.path.dirname(src), new_name), check_ext=not os.path.isdir(src))
        if os.path.lexists(dst) and os.path.normcase(dst) != os.path.normcase(src):
            return fail("rename_file", f"A file or folder with that name already exists: {dst}")
        os.rename(src, dst)
        return ok("rename_file", [row("renamed", dst, f"was {os.path.basename(src)}")], ["Renamed."])

    kind = "folder" if is_dir else "file"
    return guard.submit("rename_file", f"Rename {kind} {target} to “{new_name}”", True, action, target)


@tool
@guarded("copy_file")
def copy_file(source: str, destination: str, overwrite: bool = False) -> str:
    """Copy ONE file. `source` must be readable (approved read folder) and `destination` must be inside an allowed write folder; it can be a full file path or an existing folder (the file name is kept). Fails if the destination exists unless overwrite=true (then the user must approve in the app)."""
    src = permissions.check_read_file(source)
    limit = _cfg()["max_copy_size_mb"] * 1024 * 1024
    size = os.path.getsize(src)
    if size > limit:
        return fail("copy_file", f"File is {human_size(size)}, over the {_cfg()['max_copy_size_mb']} MB copy limit.")
    name = os.path.basename(src)
    dest = _resolve_destination(name, destination, check_ext=True)
    if os.path.normcase(dest) == os.path.normcase(src):
        return fail("copy_file", "Source and destination are the same file.")
    exists = os.path.lexists(dest)
    if exists and os.path.isdir(dest):
        return fail("copy_file", f"Destination is a folder: {dest}")
    if exists and not overwrite:
        return fail("copy_file", f"Destination already exists: {dest}. Pass overwrite=true to replace it.")

    def action():
        s = permissions.check_read_file(source)
        d = _resolve_destination(os.path.basename(s), destination, check_ext=True)
        parent = os.path.dirname(d)
        if not os.path.isdir(parent):
            raise permissions.PermissionDenied(f"Destination folder does not exist: {parent}")
        if os.path.lexists(d) and not overwrite:
            return fail("copy_file", f"Destination already exists: {d}")
        _copy_via_temp(s, d)
        return ok("copy_file", [row("copied", d, f"{human_size(size)} from {s}")], ["File copied. The original is unchanged."])

    desc = f"Copy {src} to {dest}" + (" (replaces the existing file)" if exists else "")
    return guard.submit("copy_file", desc, exists, action, dest)


@tool
@guarded("move_file")
def move_file(source: str, destination: str, overwrite: bool = False) -> str:
    """Move ONE file to another folder (or to a new full path). Both source and destination must be inside allowed write folders. `destination` can be an existing folder (the file name is kept). Fails if the destination exists unless overwrite=true. The user must approve in the app."""
    src = permissions.check_write_path(source)
    if not os.path.isfile(src):
        return fail("move_file", f"Not a file (only files can be moved with this tool): {src}")
    dest = _resolve_destination(os.path.basename(src), destination, check_ext=True)
    if os.path.normcase(dest) == os.path.normcase(src):
        return fail("move_file", "Source and destination are the same.")
    exists = os.path.lexists(dest)
    if exists and os.path.isdir(dest):
        return fail("move_file", f"Destination is a folder: {dest}")
    if exists and not overwrite:
        return fail("move_file", f"Destination already exists: {dest}. Pass overwrite=true to replace it.")

    def action():
        s = permissions.check_write_path(source)
        d = _resolve_destination(os.path.basename(s), destination, check_ext=True)
        if not os.path.isfile(s):
            return fail("move_file", f"Source no longer exists: {s}")
        parent = os.path.dirname(d)
        if not os.path.isdir(parent):
            raise permissions.PermissionDenied(f"Destination folder does not exist: {parent}")
        if os.path.lexists(d) and not overwrite:
            return fail("move_file", f"Destination already exists: {d}")
        try:
            os.replace(s, d)
        except OSError:  # different drive: copy, then remove the original
            _copy_via_temp(s, d)
            os.remove(s)
        return ok("move_file", [row("moved", d, f"from {s}")], ["File moved."])

    desc = f"Move {src} to {dest}" + (" (replaces the existing file)" if exists else "")
    return guard.submit("move_file", desc, True, action, dest)


@tool
@guarded("create_directory")
def create_directory(path: str) -> str:
    """Create a new folder (parent folders are created too). Does nothing if it already exists. `path` must be inside an allowed write folder."""
    target = permissions.check_write_path(path)
    if os.path.isdir(target):
        return ok("create_directory", [row("exists", target, "folder already exists")], ["Folder already exists."])
    if os.path.lexists(target):
        return fail("create_directory", f"A file with that name already exists: {target}")

    def action():
        t = permissions.check_write_path(path)
        os.makedirs(t, exist_ok=True)
        return ok("create_directory", [row("created", t)], ["Folder created."])

    return guard.submit("create_directory", f"Create folder {target}", False, action, target)


@tool
@guarded("delete_file")
def delete_file(path: str) -> str:
    """Delete ONE file (it goes to the Recycle Bin so it can be restored). Folders are not deleted by this tool. The user must approve in the app."""
    target = permissions.check_write_path(path)
    if not os.path.lexists(target):
        return fail("delete_file", f"Not found: {target}")
    if os.path.isdir(target):
        return fail("delete_file", f"That is a folder. Use delete_directory: {target}")
    size = os.path.getsize(target)

    def action():
        t = permissions.check_write_path(path)
        if not os.path.isfile(t):
            return fail("delete_file", f"Not a file any more: {t}")
        how = _remove(t, False)
        return ok("delete_file", [row("deleted", t, how)], [f"File {how}."])

    where = "to the Recycle Bin" if _cfg().get("delete_mode", "trash") != "permanent" else "PERMANENTLY"
    return guard.submit("delete_file", f"Delete {target} ({human_size(size)}) {where}", True, action, target)


@tool
@guarded("delete_directory")
def delete_directory(path: str, recursive: bool = False) -> str:
    """Delete ONE folder (it goes to the Recycle Bin). An empty folder is removed directly; a folder with content needs recursive=true. Drive roots, the user profile, standard folders and the write folders themselves are protected. The user must approve in the app."""
    target = permissions.check_write_path(path)
    if not os.path.lexists(target):
        return fail("delete_directory", f"Not found: {target}")
    if not os.path.isdir(target):
        return fail("delete_directory", f"That is a file. Use delete_file: {target}")
    permissions.check_not_protected(target)
    files, dirs, total, truncated = _dir_summary(target)
    if (files or dirs) and not recursive:
        return fail("delete_directory", f"Folder is not empty ({files} file(s), {dirs} sub-folder(s)). Pass recursive=true to delete it with everything inside.")

    def action():
        t = permissions.check_write_path(path)
        permissions.check_not_protected(t)
        if not os.path.isdir(t):
            return fail("delete_directory", f"Not a folder any more: {t}")
        if os.listdir(t) and not recursive:
            return fail("delete_directory", "Folder is no longer empty.")
        how = _remove(t, True)
        return ok("delete_directory", [row("deleted", t, how)], [f"Folder {how}."])

    where = "to the Recycle Bin" if _cfg().get("delete_mode", "trash") != "permanent" else "PERMANENTLY"
    more = "+" if truncated else ""
    desc = f"Delete folder {target} with {files}{more} file(s), {dirs}{more} sub-folder(s), {human_size(total)} {where}"
    return guard.submit("delete_directory", desc, True, action, target)
