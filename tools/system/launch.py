"""open_file_location (shows a path in Explorer) and open_file (default application, needs Approve)."""
import os
import subprocess

from langchain_core.tools import tool

from security import permissions
from tools.common import fail, guarded, ok
from security import guard


def _windows_only(tool_name: str):
    if os.name != "nt":
        return fail(tool_name, "This only works when the app runs on Windows.")
    return None


@tool
@guarded("open_file_location")
def open_file_location(path: str) -> str:
    """Show a file or folder in Windows File Explorer (selects the file, or opens the folder). Does not open or run the file itself. `path` must be an absolute Windows path."""
    permissions.require_computer_enabled()
    real = permissions.check_search_path(path)
    if not os.path.exists(real):
        return fail("open_file_location", f"Path not found: {real}")
    blocked = _windows_only("open_file_location")
    if blocked:
        return blocked
    if os.path.isdir(real):
        subprocess.Popen(["explorer", real])
    else:
        subprocess.Popen(["explorer", f"/select,{real}"])
    return ok("open_file_location", [{"action": "shown in Explorer", "path": real}], ["Opened in File Explorer."])


@tool
@guarded("open_file")
def open_file(path: str) -> str:
    """Open ONE file in its default Windows application (for example a PDF in the PDF viewer). Only document, text, image and media types are allowed; programs and scripts never are. The user must approve in the app, so do not call it again after it reports a pending approval."""
    real = permissions.check_open_file(path)

    def action():
        target = permissions.check_open_file(path)
        blocked = _windows_only("open_file")
        if blocked:
            return blocked
        os.startfile(target)  # noqa: S606 - extension allow-list + approval gate above
        return ok("open_file", [{"action": "opened in default application", "path": target}],
                  ["Opened with the default application."])

    return guard.submit("open_file", f"Open {real} in its default application", True, action, real,
                        required=permissions.computer_confirm_required())
