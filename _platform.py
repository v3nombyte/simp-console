"""Cross-platform helpers for venv detection and app restart.

Centralises the differences between Linux and Windows:
  - Virtual environment paths (bin/ vs Scripts/)
  - Process replacement (execv vs Popen+exit)
  - Signal handling (SIGTERM exists only on POSIX)
"""

import os
import subprocess
import sys
from pathlib import Path


def venv_python_path(project_dir: Path) -> Path:
    """Return the path to the venv's Python executable.

    Linux:   <project>/venv/bin/python
    Windows: <project>/venv\\Scripts\\python.exe
    """
    venv = project_dir / "venv"
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def restart_app(python_exe: Path, script: Path) -> None:
    """Replace the current process with a fresh interpreter.

    On Linux/macOS: uses os.execv() — same PID, zero nesting, never returns.
    On Windows:     spawns a subprocess and calls sys.exit() — os.execv is
                    not available on Windows.
    """
    if sys.platform == "win32":
        subprocess.Popen([str(python_exe), str(script)])
        sys.exit(0)
    else:
        os.execv(str(python_exe), [str(python_exe), str(script)])
        # Never reached


def setup_signal_handlers(handler=None):
    """Register clean-shutdown signal handlers.

    SIGINT (Ctrl+C) on both platforms. SIGTERM on POSIX only.
    """
    if handler is None:
        def handler(signum, frame):
            print("\nGoodbye!")
            sys.exit(0)

    try:
        signal = __import__('signal')
        signal.signal(signal.SIGINT, handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError):
        pass  # Some environments (Windows without signal module) skip this
