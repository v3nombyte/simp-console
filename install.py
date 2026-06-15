#!/usr/bin/env python3
"""Cross-platform install script for Simp Console.

Usage:
    python install.py

Creates a virtual environment, installs dependencies, and sets up Playwright.
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def note(msg: str):
    print(f"  • {msg}")


def run(cmd: list[str], desc: str = "") -> int:
    if desc:
        print(f"  [{desc}]")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def main() -> int:
    print("=== Simp Console Install ===\n")
    os.chdir(str(PROJECT_DIR))

    # ── 1. Virtual environment ──
    venv_path = PROJECT_DIR / "venv"
    bin_dir = venv_path / ("Scripts" if sys.platform == "win32" else "bin")
    pip_exe = bin_dir / ("pip.exe" if sys.platform == "win32" else "pip")
    python_exe = bin_dir / ("python.exe" if sys.platform == "win32" else "python")

    if venv_path.is_dir():
        print("[1/4] Virtual environment already exists — skipping")
    else:
        print("[1/4] Creating Python virtual environment...")
        venv.create(str(venv_path), with_pip=True)

    # ── 2. Install dependencies ──
    print("[2/4] Installing Python dependencies...")
    run([str(pip_exe), "install", "--upgrade", "pip", "-q"],
        desc="pip upgrade")

    req_file = PROJECT_DIR / "requirements.txt"
    if req_file.exists():
        run([str(pip_exe), "install", "-r", str(req_file), "-q"],
            desc="requirements.txt")
    else:
        note("No requirements.txt found — skipping")

    # ── 3. Playwright browsers ──
    print("[3/4] Installing Playwright Chromium browser...")
    playwright_result = subprocess.run(
        [str(python_exe), "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    if playwright_result.returncode != 0:
        note(f"Playwright install warnings (non-fatal): {playwright_result.stderr.strip()[-200:]}")

    # ── 4. Create default dirs ──
    print("[4/4] Creating default directories...")
    for d in ['input', 'models_data', 'output', 'logs']:
        (PROJECT_DIR / d).mkdir(exist_ok=True)
        note(f"  {d}/")

    print("\n" + "=" * 50)
    print("  Install complete!")
    print(f"  Run:  python {PROJECT_DIR / 'main.py'}")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
