"""run_shell_command: ONE Windows PowerShell command, executed only after the person clicks Approve."""
import os
import shutil
import subprocess
import time

from langchain_core.tools import tool

from security import permissions
from security.settings import get_settings
from tools.common import fail, guarded, ok
from security import guard

CREATE_NO_WINDOW = 0x08000000
UTF8_PREFIX = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
               "$ProgressPreference = 'SilentlyContinue'; ")


def _cfg() -> dict:
    return get_settings()["computer"]


def _timeout(requested) -> int:
    default, ceiling = int(_cfg().get("shell_timeout_seconds", 30)), int(_cfg().get("shell_max_timeout_seconds", 120))
    try:
        value = int(requested)
    except (TypeError, ValueError):
        value = 0
    return max(1, min(value, ceiling)) if value > 0 else default


def _clip(data: bytes, limit: int):
    text = data.decode("utf-8-sig", errors="replace")
    return (text[:limit], True) if len(text) > limit else (text, False)


def _execute(command: str, cwd: str, timeout: int) -> str:
    exe = shutil.which("powershell") if os.name == "nt" else None
    if not exe:
        return fail("run_shell_command", "Windows PowerShell was not found (this only works on Windows).")
    argv = [exe, "-NoProfile", "-NonInteractive", "-Command", UTF8_PREFIX + command]
    started = time.time()
    proc = subprocess.Popen(argv, cwd=cwd, env=permissions.shell_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
        proc.communicate()
        return fail("run_shell_command", f"Command timed out after {timeout} s and was stopped.")

    limit = int(_cfg().get("max_output_chars", 20000))
    stdout, cut_out = _clip(out, limit)
    stderr, cut_err = _clip(err, limit)
    notes = [f"Exit code {proc.returncode}."]
    if cut_out or cut_err:
        notes.append(f"Output was cut to {limit} characters per stream.")
    row = {"command": command, "working_directory": cwd, "exit_code": proc.returncode,
           "duration": f"{time.time() - started:.1f} s"}
    return ok("run_shell_command", [row], notes, stdout=stdout, stderr=stderr, exit_code=proc.returncode)


@tool
@guarded("run_shell_command")
def run_shell_command(command: str, working_directory: str = "", timeout_seconds: int = 0) -> str:
    """Run ONE Windows PowerShell command and return its stdout, stderr and exit code. Use it only for information or system tasks the user explicitly asked for (never to read, change or delete files: use the file tools). `working_directory` is an absolute folder (default: the user's home folder). The command is NOT executed by this tool: the user must approve it in the app, so do not call it again after it reports a pending approval. Dangerous commands (delete, format, registry, shutdown, downloads, nested shells) are blocked."""
    cmd = permissions.check_shell_command(command)
    cwd = permissions.check_shell_cwd(working_directory)
    timeout = _timeout(timeout_seconds)

    def action():
        return _execute(permissions.check_shell_command(command), permissions.check_shell_cwd(working_directory),
                        timeout)

    return guard.submit("run_shell_command", f"Run PowerShell command in {cwd}: {cmd}", True, action, cwd,
                        required=permissions.computer_confirm_required())
