#!/usr/bin/env python3
"""Startup launcher for Simp Console (cross-platform).

Detects the virtual environment, re-executes inside it, then runs the menu.
After exit, checks for a restart marker and does a clean process replacement
(os.execv on Linux, subprocess.Popen on Windows).
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

    # If we're not running from the venv, re-exec inside it
    if not sys.executable.startswith(str(venv_python.parent.resolve())):
        if not venv_python.exists():
            print(f"Virtual environment is corrupt (missing {venv_python})")
            print("Re-run:  python install.py")
            return 1
        print(f"Using virtual environment: {venv_python.parent}")
        restart_app(venv_python, __file__)
        return 0

    setup_signal_handlers()

    # Remove any stale marker from a previous crash
    RESTART_MARKER.unlink(missing_ok=True)

    import os
    os.chdir(str(PROJECT_DIR))

    from cli.menu import run
    try:
        run()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        return 0

    # Single-shot restart after update
    if RESTART_MARKER.exists():
        RESTART_MARKER.unlink(missing_ok=True)
        print("\nRestarting to apply update...")
        restart_app(venv_python, __file__)

    return 0


if __name__ == "__main__":
    sys.exit(main())
