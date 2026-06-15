#!/usr/bin/env python3
"""Simp Console — Terminal-based SimpScraper pipeline (cross-platform).

Entry point with venv detection and auto-restart after update.
Usage:
    python main.py

Works on Linux and Windows. On Windows, restart uses subprocess.Popen
since os.execv is not available.
"""

import sys
from pathlib import Path

from _platform import venv_python_path, restart_app, setup_signal_handlers

PROJECT_DIR = Path(__file__).resolve().parent
RESTART_MARKER = PROJECT_DIR / ".restart.rqd"


def main():
    venv_python = venv_python_path(PROJECT_DIR)

    if not PROJECT_DIR.joinpath("venv").is_dir():
        print("No virtual environment found — run:  python install.py")
        return 1

    # Re-exec inside venv if needed
    if not sys.executable.startswith(str(venv_python.parent.resolve())):
        if not venv_python.exists():
            print(f"Virtual environment is corrupt (missing {venv_python})")
            print("Re-run:  python install.py")
            return 1
        print(f"Using virtual environment: {venv_python.parent}")
        restart_app(venv_python, __file__)
        return 0

    setup_signal_handlers()

    RESTART_MARKER.unlink(missing_ok=True)

    import os
    os.chdir(str(PROJECT_DIR))

    from cli.menu import run
    try:
        run()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        return 0

    # Restart after update
    if RESTART_MARKER.exists():
        RESTART_MARKER.unlink(missing_ok=True)
        print("\nRestarting to apply update...")
        restart_app(venv_python, __file__)

    return 0


if __name__ == "__main__":
    sys.exit(main())
