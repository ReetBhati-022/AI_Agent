"""Every filesystem permission decision lives here.

Three separate permissions:
  * Discovery (names, metadata): any path on an allowed drive that is not excluded.
  * Content reading: only inside approved_read_dirs (or anywhere if allow_all_reads).
  * Modifying (create/write/rename/move/delete): only inside modify.write_dirs (or anywhere if allow_all_writes),
    never inside the app's own folder, and never on links, protected folders or blocked names/extensions.
  * Computer tools (run_shell_command, open_file): command deny-list, scrubbed environment, extension allow-list.
The LLM only reaches the disk through tools that call these checks.
"""
import fnmatch
import os
import re
from functools import lru_cache

from security.settings import ROOT, get_settings

IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # NTFS junction
IO_REPARSE_TAG_SYMLINK = 0xA000000C


class PermissionDenied(Exception):
    """Raised when a request violates the security policy."""


def _fs() -> dict:
    return get_settings()["filesystem"]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def allowed_drives() -> list:
    return [str(d).strip().upper()[:1] for d in _fs()["allowed_drives"]]


def drive_roots(drive: str = "all") -> list:
    drives = allowed_drives()
    if drive and drive.strip().lower() != "all":
        letter = drive.strip().upper()[:1]
        if letter not in drives:
            raise PermissionDenied(f"Drive {letter}: is not allowed. Allowed: {', '.join(d + ':' for d in drives)}")
        drives = [letter]
    roots = [f"{d}:\\" for d in drives if os.path.isdir(f"{d}:\\")]
    if not roots:
        raise PermissionDenied("None of the allowed drives are accessible on this machine.")
    return roots


def resolve_path(raw: str) -> str:
    """Validate and fully resolve a path (.. collapsed, symlinks/junctions followed)."""
    if not isinstance(raw, str) or not raw.strip():
        raise PermissionDenied("Path is empty.")
    raw = raw.strip().strip('"').strip("'")
    if "\x00" in raw:
        raise PermissionDenied("Invalid characters in path.")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise PermissionDenied("Network (UNC) and device paths are not allowed.")
    if not os.path.isabs(raw):
        raise PermissionDenied("Use a full absolute path, for example C:\\Users\\name\\Documents.")

    resolved = os.path.realpath(raw)
    drive = os.path.splitdrive(resolved)[0]
    if resolved.startswith("\\\\") or drive[:1].upper() not in allowed_drives():
        raise PermissionDenied(f"Path resolves outside the allowed drives: {resolved}")
    return resolved


@lru_cache(maxsize=1)
def _exclusions():
    fs = _fs()
    paths = tuple(_norm(p) for p in fs["excluded_paths"])
    names = {str(n).lower() for n in fs["excluded_names"]}
    return paths, names


def is_excluded(path: str) -> bool:
    paths, names = _exclusions()
    norm = _norm(path)
    if any(norm == p or norm.startswith(p + os.sep) for p in paths):
        return True
    return any(part.lower() in names for part in norm.split(os.sep))


def check_search_path(raw: str) -> str:
    """Permission for names/metadata only (never contents)."""
    path = resolve_path(raw)
    if is_excluded(path):
        raise PermissionDenied(f"This location is excluded by the security configuration: {path}")
    return path


@lru_cache(maxsize=1)
def approved_read_dirs() -> tuple:
    dirs = []
    for d in _fs()["approved_read_dirs"]:
        if os.path.isabs(d) and not d.startswith("\\\\"):
            dirs.append(os.path.realpath(d))
    return tuple(dirs)


def is_within(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([_norm(path), _norm(parent)]) == _norm(parent)
    except ValueError:  # different drives
        return False


def allow_all_reads() -> bool:
    """When true, file contents may be read anywhere on the allowed drives (exclusions + blocked names still apply)."""
    return bool(_fs().get("allow_all_reads", False))


def is_approved_for_reading(path: str) -> bool:
    if allow_all_reads():
        return True  # path was already restricted to allowed drives and non-excluded folders by the caller
    return any(is_within(path, a) for a in approved_read_dirs())


def check_read_dir(raw: str) -> str:
    """Permission to read file contents below a directory."""
    path = check_search_path(raw)
    if not os.path.isdir(path):
        raise PermissionDenied(f"Not a directory: {path}")
    if not is_approved_for_reading(path):
        approved = "; ".join(approved_read_dirs()) or "(none configured)"
        raise PermissionDenied(
            f"Content access denied: {path} is not inside an approved read directory. "
            f"Approved: {approved}. Add folders in config.yaml (approved_read_dirs) or APPROVED_READ_DIRS in .env."
        )
    return path


def check_read_file(raw: str) -> str:
    """Permission to read the contents of ONE file (must be inside an approved read directory)."""
    path = check_search_path(raw)
    if not os.path.isfile(path):
        raise PermissionDenied(f"Not a file: {path}")
    if not is_approved_for_reading(path):
        approved = "; ".join(approved_read_dirs()) or "(none configured)"
        raise PermissionDenied(
            f"Content access denied: {path} is not inside an approved read directory. "
            f"Approved: {approved}. Add folders in config.yaml (approved_read_dirs) or APPROVED_READ_DIRS in .env."
        )
    return check_readable_file(path)


def is_blocked_filename(name: str) -> bool:
    patterns = get_settings()["content"]["blocked_name_patterns"]
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def check_readable_file(path: str) -> str:
    """Per-file check right before opening: re-resolves to catch symlink/junction escapes."""
    real = os.path.realpath(path)
    if is_excluded(real) or not is_approved_for_reading(real):
        raise PermissionDenied(f"File is outside the approved read directories: {real}")
    if is_blocked_filename(os.path.basename(real)):
        raise PermissionDenied(f"File name matches a blocked sensitive pattern: {os.path.basename(real)}")
    return real


def entry_is_link(entry) -> bool:
    """True for symlinks and NTFS junctions (they are never followed during walks)."""
    try:
        if entry.is_symlink():
            return True
        tag = getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0)
        return tag in (IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK)
    except OSError:
        return True


# ---------------------------------------------------------------- modify (write) permissions

INVALID_NAME_CHARS = set('<>:"/\\|?*') | {chr(i) for i in range(32)}
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _modify_cfg() -> dict:
    return get_settings().get("modify", {})


def modify_enabled() -> bool:
    return bool(_modify_cfg().get("enabled", False))


def allow_all_writes() -> bool:
    return bool(_modify_cfg().get("allow_all_writes", False))


@lru_cache(maxsize=1)
def write_dirs() -> tuple:
    dirs = []
    for d in _modify_cfg().get("write_dirs", []):
        if os.path.isabs(d) and not d.startswith("\\\\"):
            dirs.append(os.path.realpath(d))
    return tuple(dirs)


def is_link_path(path: str) -> bool:
    """True for symlinks and NTFS junctions."""
    try:
        if os.path.islink(path):
            return True
        tag = getattr(os.lstat(path), "st_reparse_tag", 0)
        return tag in (IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK)
    except OSError:
        return False


def check_name(name: str):
    """A single file/folder name must be valid on Windows."""
    if not name or name in (".", ".."):
        raise PermissionDenied("Invalid file or folder name.")
    if len(name) > 255:
        raise PermissionDenied("Name is longer than 255 characters.")
    bad = sorted(c for c in set(name) if c in INVALID_NAME_CHARS and c.isprintable())
    if bad or any(ord(c) < 32 for c in name):
        raise PermissionDenied(f"Name contains characters Windows does not allow: {' '.join(bad) or '(control characters)'}")
    if name.endswith((" ", ".")):
        raise PermissionDenied("Names cannot end with a space or a dot.")
    if name.split(".")[0].lower() in RESERVED_NAMES:
        raise PermissionDenied(f"'{name}' is a reserved Windows device name.")


def check_extension(name: str, text_only: bool = False):
    ext = os.path.splitext(name)[1].lower()
    cfg = get_settings()
    blocked = {e.lower() for e in _modify_cfg().get("blocked_extensions", [])}
    if ext in blocked:
        raise PermissionDenied(f"Files of type '{ext}' (programs/scripts/shortcuts) cannot be created or renamed to by this app.")
    if text_only:
        allowed = {e.lower() for e in cfg["content"]["supported_extensions"]}
        if ext not in allowed:
            raise PermissionDenied(f"Only text files can be written. Supported types: {', '.join(sorted(allowed))}")


def check_write_path(raw: str, check_ext: bool = False, text_only: bool = False) -> str:
    """Validate a path that is about to be created, changed, renamed, moved or deleted.

    The parent folder is fully resolved (so '..' and symlinked parents cannot escape); the final name is
    kept as-is so that links are never followed. Returns the absolute target path."""
    if not modify_enabled():
        raise PermissionDenied("File modification is turned off (modify.enabled: false in config.yaml).")
    if not isinstance(raw, str) or not raw.strip():
        raise PermissionDenied("Path is empty.")
    raw = raw.strip().strip('"').strip("'")
    if "\x00" in raw:
        raise PermissionDenied("Invalid characters in path.")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise PermissionDenied("Network (UNC) and device paths are not allowed.")
    if not os.path.isabs(raw):
        raise PermissionDenied("Use a full absolute path, for example C:\\Users\\name\\Documents\\notes.txt")

    parent, name = os.path.split(os.path.normpath(raw))
    if not name:
        raise PermissionDenied("A drive or folder root cannot be modified.")
    check_name(name)
    target = os.path.join(resolve_path(parent), name)

    if is_excluded(target):
        raise PermissionDenied(f"This location is excluded by the security configuration: {target}")
    if is_within(target, str(ROOT)):
        raise PermissionDenied("The agent's own program folder is protected and cannot be modified.")
    if not allow_all_writes() and not any(is_within(target, w) for w in write_dirs()):
        allowed = "; ".join(write_dirs()) or "(none configured)"
        raise PermissionDenied(
            f"Changes are not allowed here: {target}. Allowed write folders: {allowed}. "
            "Add folders under modify.write_dirs in config.yaml (or set allow_all_writes)."
        )
    if os.path.lexists(target) and is_link_path(target):
        raise PermissionDenied("Symbolic links and junctions are never modified.")
    if is_blocked_filename(name):
        raise PermissionDenied(f"File name matches a blocked sensitive pattern: {name}")
    if check_ext or text_only:
        check_extension(name, text_only=text_only)
    return target


def check_not_protected(path: str):
    """Folders that may never be deleted or renamed: drive roots, the user profile and its standard folders,
    the configured write folders themselves, and the app's own folder (and its parents)."""
    norm = _norm(path)
    home = os.path.realpath(os.path.expanduser("~"))
    protected = {_norm(home)}
    for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "AppData", "OneDrive"):
        protected.add(_norm(os.path.join(home, sub)))
    protected.update(_norm(w) for w in write_dirs())
    drive, tail = os.path.splitdrive(norm)
    if norm in protected or tail in ("", os.sep):
        raise PermissionDenied(f"This is a protected folder and cannot be deleted or renamed: {path}")
    if is_within(str(ROOT), path):
        raise PermissionDenied("The agent's own program folder (or a parent of it) is protected.")


# ---------------------------------------------------------------- computer (shell / open) permissions

SAFE_ENV_NAMES = {
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "USERNAME",
    "USERDOMAIN", "COMPUTERNAME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "ALLUSERSPROFILE", "PUBLIC", "OS", "PSMODULEPATH",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS",
}


def _computer_cfg() -> dict:
    return get_settings().get("computer", {})


def computer_enabled() -> bool:
    return bool(_computer_cfg().get("enabled", False))


def computer_confirm_required() -> bool:
    """Separate from modify.confirm_changes, so turning that off never makes commands run without Approve."""
    return bool(_computer_cfg().get("confirm_actions", True))


def require_computer_enabled():
    if not computer_enabled():
        raise PermissionDenied("Computer command tools are turned off (computer.enabled: false in config.yaml).")


@lru_cache(maxsize=1)
def _command_patterns() -> tuple:
    return tuple(re.compile(p, re.IGNORECASE) for p in _computer_cfg().get("blocked_command_patterns", []))


def check_shell_command(command: str) -> str:
    """Backstop deny-list for shell commands. The real gate is the person's Approve click, which shows the command."""
    require_computer_enabled()
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise PermissionDenied("Command is empty or contains invalid characters.")
    command = command.strip()
    limit = int(_computer_cfg().get("max_command_chars", 1000))
    if len(command) > limit:
        raise PermissionDenied(f"Command is longer than {limit} characters.")
    for pattern in _command_patterns():
        if pattern.search(command):
            raise PermissionDenied(f"Command blocked by the security policy (rule: {pattern.pattern}). "
                                   "Use the file tools for file work; rules are in config.yaml (computer.blocked_command_patterns).")
    for token in re.split(r"[\s\"'`\\/,;=|&()]+", command):
        if token and is_blocked_filename(token):
            raise PermissionDenied(f"Command mentions a blocked sensitive file name: {token}")
    if str(ROOT).lower().replace("/", "\\") in command.lower().replace("/", "\\"):
        raise PermissionDenied("Commands may not touch the agent's own program folder.")
    return command


def check_shell_cwd(raw: str) -> str:
    """Working folder for a shell command: an existing folder on an allowed drive, not the app's own folder."""
    require_computer_enabled()
    path = check_search_path(raw.strip() if isinstance(raw, str) and raw.strip() else os.path.expanduser("~"))
    if not os.path.isdir(path):
        raise PermissionDenied(f"Not a directory: {path}")
    if is_within(path, str(ROOT)):
        raise PermissionDenied("Commands may not run inside the agent's own program folder.")
    return path


def shell_env() -> dict:
    """Minimal environment for child processes: API keys and passwords from .env are never passed on."""
    return {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_NAMES}


def check_open_file(raw: str) -> str:
    """A file may be opened in its default application only if its type is on the allow-list."""
    require_computer_enabled()
    path = check_search_path(raw)
    if not os.path.isfile(path):
        raise PermissionDenied(f"Not a file: {path}")
    name = os.path.basename(path)
    if is_blocked_filename(name):
        raise PermissionDenied(f"File name matches a blocked sensitive pattern: {name}")
    ext = os.path.splitext(name)[1].lower()
    allowed = {e.lower() for e in _computer_cfg().get("open_allowed_extensions", [])}
    if ext not in allowed:
        raise PermissionDenied(f"Files of type '{ext or '(none)'}' cannot be opened by this app (programs, scripts and "
                               f"unknown types are blocked). Allowed: {', '.join(sorted(allowed)) or '(none)'}")
    return path