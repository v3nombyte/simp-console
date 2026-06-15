"""Main menu system for Simp Console."""

import os
import sys
import subprocess
from pathlib import Path

import questionary

from .logger import (
    print_header, print_info, print_success, print_error,
    print_warning, console, print_divider, print_panel,
)
from .settings import AppSettings, edit_settings, manage_cookies
from .workflow import run_full_workflow
from . import actions as act

PROJECT_DIR = Path(__file__).resolve().parent.parent
RESTART_MARKER = PROJECT_DIR / ".restart.rqd"
VERSION = "0.0.1"


# ── Update Check ────────────────────────────────────────────────────

def _run_git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command, return stdout stripped. Auto-fixes 'dubious ownership'."""
    try:
        result = subprocess.run(
            ['git'] + args,
            capture_output=True, text=True,
            cwd=cwd or str(PROJECT_DIR),
            timeout=15,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Auto-fix: dubious ownership (repo owned by different user on USB/NFS)
            if stderr.startswith("fatal: detected dubious ownership"):
                repo_path = cwd or str(PROJECT_DIR)
                subprocess.run(
                    ['git', 'config', '--global', '--add', 'safe.directory', repo_path],
                    capture_output=True, timeout=10,
                )
                # Retry once
                result = subprocess.run(
                    ['git'] + args, capture_output=True, text=True,
                    cwd=repo_path, timeout=15,
                )
            # On retry failure, log the error but return empty
            if result.returncode != 0:
                return ""
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def check_for_updates(branch: str | None = None) -> dict:
    """Check if a newer commit exists on the remote branch.

    If branch is None, checks the currently checked-out branch.
    The branch parameter overrides everything for one-off checks.
    Used by the menu's 'Check for Updates' action.
    """
    state = {
        'checked': False,
        'update_available': False,
        'local_sha': '',
        'remote_sha': '',
        'local_branch': '',
        'error': '',
        'new_commits': '',
    }

    print_info("Checking for updates...")

    if not os.path.isdir(os.path.join(PROJECT_DIR, '.git')):
        state['error'] = "Not a git repository (no .git directory)"
        print_warning(state['error'])
        return state

    local_sha = _run_git(['rev-parse', 'HEAD'])
    current_branch = _run_git(['rev-parse', '--abbrev-ref', 'HEAD'])

    if not local_sha:
        state['error'] = "Cannot determine git state"
        print_error(state['error'])
        return state

    target_branch = branch or current_branch or 'main'
    state['local_sha'] = local_sha
    state['local_branch'] = current_branch or target_branch

    # Fetch silently
    _run_git(['fetch', 'origin', target_branch])

    remote_sha = _run_git(['rev-parse', f'origin/{target_branch}'])
    if not remote_sha:
        state['error'] = f"Cannot reach remote (origin/{target_branch})"
        print_warning(state['error'])
        return state

    state['remote_sha'] = remote_sha
    state['checked'] = True

    if local_sha != remote_sha:
        state['update_available'] = True
        log = _run_git(['log', f'{local_sha}..{remote_sha}', '--oneline', '--no-decorate'])
        state['new_commits'] = log or ''
        print_info(f"Update available on branch '{target_branch}'")
        if log:
            console(f"\n[dim]New commits:[/]\n{log}\n")
    else:
        print_success(f"Already up to date (branch: {target_branch}, {remote_sha[:8]})")

    return state


def print_update_notice(state: dict):
    """Print a prominent startup update notification."""
    if state.get('error'):
        return
    if state.get('update_available'):
        branch = state.get('local_branch', '?')
        commits = state.get('new_commits', '')
        count = len(commits.splitlines()) if commits else 0
        panel_text = (
            f"[bold yellow]\u27f3 Update available[/]\n\n"
            f"  Branch: [bold]{branch}[/]\n"
            f"  Commits: {count}\n\n"
            f"  Run [bold cyan]Check for Updates[/] from the menu to apply."
        )
        console("")
        print_panel(panel_text, title="\u26a0 UPDATE", style="yellow")
        console("")


def perform_update(state: dict) -> bool:
    """Pull the latest changes. Writes .restart.rqd for clean execv restart."""
    if not state.get('update_available'):
        print_info("No update to apply.")
        return True

    branch = state.get('target_branch') or state.get('local_branch', 'main')

    if not questionary.confirm(
        f"Pull latest changes from origin/{branch}?",
        default=True,
    ).ask():
        return False

    print_info("Pulling updates...")
    result = subprocess.run(
        ['git', 'pull', 'origin', branch],
        capture_output=True, text=True,
        cwd=str(PROJECT_DIR), timeout=30,
    )

    if result.returncode == 0:
        print_success("Update complete!")
        if result.stdout:
            console(result.stdout.strip())
        # Write restart marker — start.py/main.py will execv on next exit
        RESTART_MARKER.write_text("restart")
        print_info("Restarting to apply update...")
        return True
    else:
        print_error(f"Update failed: {result.stderr}")
        return False


# ── Main Menu ───────────────────────────────────────────────────────

def show_menu(settings: AppSettings) -> str:
    """Show the main menu and return the selected action."""
    print_header(
        f"Simp Console v{VERSION}",
        "Terminal-based SimpScraper pipeline — automated media workflow",
    )

    # Quick status
    registry = act._get_registry()
    models = registry.get_all()
    status_parts = []
    if models:
        status_parts.append(f"{len(models)} model(s) registered")
    if settings.cookies_json:
        status_parts.append("cookies saved")
    if status_parts:
        console(f"[dim]{' | '.join(status_parts)}[/]\n")

    choices = [
        questionary.Separator("── PIPELINE ──"),
        "🚀  Start Full Workflow (Crawl/Import → Download → Sort → Rename)",
        questionary.Separator("── CRAWLING ──"),
        "🌐  Crawl SimpCity Thread",
        questionary.Separator("── EXTRACTION ──"),
        "📄  Extract URLs from HTML Files (archive / loose)",
        "📂  Select JSON File (Load Scan Results)",
        "📥  Import JSON File from Disk",
        questionary.Separator("── PROCESSING ──"),
        "⬇️  Download Media Files",
        "📦  Sort & Dedupe",
        "🏷️   Rename Files",
        questionary.Separator("── MANAGEMENT ──"),
        "📋  View Model Registry",
        "📂  Browse Model Folder",
        "📤  Export Results",
        questionary.Separator("── SETTINGS ──"),
        "⚙️   Settings",
        "🍪  Manage Cookies",
        questionary.Separator("── SYSTEM ──"),
        "🔄  Check for Updates",
        "❌  Exit",
    ]

    return questionary.select(
        "Main Menu — What would you like to do?",
        choices=choices,
        qmark="▶",
        pointer="→",
    ).ask()


def run():
    """Main entry point for the menu system."""
    os.chdir(str(PROJECT_DIR))
    settings = AppSettings.load()

    # Initialise the logger
    from .logger import init_logger
    init_logger()
    print_info(f"Simp Console v{VERSION} — Logs: {PROJECT_DIR / 'logs/'}")

    # Auto-check for updates using saved branch
    _update_state = check_for_updates(branch=settings.update_branch)
    print_update_notice(_update_state)

    # Create standard dirs if they don't exist
    for d in ['input', 'models_data', 'output', 'logs']:
        os.makedirs(PROJECT_DIR / d, exist_ok=True)

    while True:
        choice = show_menu(settings)

        if choice is None or "Exit" in choice:
            print_info("Goodbye!")
            return

        print_divider()

        # ── Pipeline ──
        if "Full Workflow" in choice:
            run_full_workflow(settings)

        # ── Crawling ──
        elif "Crawl" in choice:
            act.action_crawl(settings)

        # ── Extraction ──
        elif "Extract URLs from HTML" in choice:
            html_files = act.action_select_html_files(settings)
            if html_files:
                act.action_extract(settings, html_files)

        elif "JSON File" in choice and "Import" not in choice and "Select" in choice:
            act.action_select_json_file(settings)

        elif "Import JSON" in choice:
            act.action_import_file(settings)

        # ── Processing ──
        elif "Download Media" in choice:
            act.action_download_media(settings)

        elif "Sort" in choice:
            act.action_sort(settings)

        elif "Rename" in choice:
            act.action_rename(settings)

        # ── Management ──
        elif "Registry" in choice:
            act.action_view_registry(settings)

        elif "Browse" in choice:
            act.action_browse_model_folder(settings)

        elif "Export" in choice:
            act.action_export(settings)

        # ── Settings ──
        elif "Settings" in choice and "Cookie" not in choice:
            settings = edit_settings(settings)

        elif "Cookie" in choice:
            settings = manage_cookies(settings)

        # ── System ──
        elif "Update" in choice:
            branch = settings.update_branch
            state = check_for_updates(branch=branch)
            if state.get('update_available'):
                state['target_branch'] = branch
                if perform_update(state):
                    break
            print_info("No updates available.")

        if choice and "Exit" not in choice:
            input("\nPress Enter to return to menu...")
