"""Computer info tools (read-only): get_system_info, get_disk_usage, get_current_directory."""
import os
import platform
import shutil
import sys

from langchain_core.tools import tool

from security import permissions
from security.settings import ROOT
from tools.common import guarded, human_size, ok


def _environment_name() -> str:
    conda = os.environ.get("CONDA_DEFAULT_ENV")
    venv = os.environ.get("VIRTUAL_ENV")
    return conda or (os.path.basename(venv) if venv else "(none)")


@tool
@guarded("get_system_info")
def get_system_info() -> str:
    """Show details about this computer and the Python environment the agent runs in: Windows version, architecture, CPU cores, Python version and the active conda/venv environment. Takes no input."""
    permissions.require_computer_enabled()
    row = {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "cpu_cores": os.cpu_count(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "environment": _environment_name(),
    }
    return ok("get_system_info", [row])


@tool
@guarded("get_disk_usage")
def get_disk_usage(drive: str = "all") -> str:
    """Show total, used and free space for the allowed drives (C: and D:). `drive` is 'all', 'C' or 'D'."""
    permissions.require_computer_enabled()
    rows, notes = [], []
    for root in permissions.drive_roots(drive):
        try:
            usage = shutil.disk_usage(root)
        except OSError as exc:
            notes.append(f"{root[:2]} could not be read: {exc}")
            continue
        rows.append({
            "drive": root[:2],
            "total": human_size(usage.total),
            "used": human_size(usage.used),
            "free": human_size(usage.free),
            "used_percent": f"{usage.used / usage.total * 100:.1f}%" if usage.total else "n/a",
        })
    return ok("get_disk_usage", rows, notes)


@tool
@guarded("get_current_directory")
def get_current_directory() -> str:
    """Show the agent's current working directory, the app's own folder and the user's home folder. Takes no input."""
    permissions.require_computer_enabled()
    rows = [
        {"name": "Current working directory", "path": os.getcwd()},
        {"name": "App folder", "path": str(ROOT)},
        {"name": "Home folder", "path": os.path.expanduser("~")},
    ]
    return ok("get_current_directory", rows)
