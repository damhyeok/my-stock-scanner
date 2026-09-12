"""Update the checkout before loading any analysis code (standard library only)."""

import os
from pathlib import Path
import subprocess
import sys


REEXEC_MARKER = "STOCK_SCANNER_BOOTSTRAP_PID"


def prepare_job(project_dir):
    """Pull once, then replace this process so even cloud_job.py is freshly read.

    Consume the PID-bound marker immediately: child jobs must update independently.
    A failed update stops the job instead of executing a potentially mixed checkout.
    """
    if os.environ.pop(REEXEC_MARKER, None) == str(os.getpid()):
        return True
    if len(sys.argv) < 2 or any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        return False
    root = Path(project_dir)
    # Same lock order as normal jobs. Do not modify a checkout while another
    # data-writing analysis is using it. File handles are closed before exec.
    with (root / ".cloud_data.lock").open("a") as data_lock:
        with (root / ".cloud_git.lock").open("a") as git_lock:
            if os.name != "nt":
                import fcntl

                fcntl.flock(data_lock.fileno(), fcntl.LOCK_EX)
                fcntl.flock(git_lock.fileno(), fcntl.LOCK_EX)
            subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                cwd=root, check=True, text=True,
            )
    environment = os.environ.copy()
    environment[REEXEC_MARKER] = str(os.getpid())
    os.execve(sys.executable, [sys.executable, str(root / "cloud_job.py"), *sys.argv[1:]], environment)
    raise RuntimeError("Process replacement unexpectedly returned")
