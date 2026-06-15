"""Dual logger: rich console output + rotating file logs."""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

# ── Globals ─────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"

_console = Console()
_file_logger: logging.Logger | None = None
_console_suppressed: bool = False  # when True, print_* functions + RichHandler go silent


# ── Console suppression (for live display) ──────────────────────────

def suppress_console():
    """Suppress all console output — log to file only.
    Used by the live download panel to prevent output interference."""
    global _console_suppressed
    _console_suppressed = True
    if _file_logger is not None:
        for h in _file_logger.handlers:
            if isinstance(h, RichHandler):
                h.setLevel(logging.CRITICAL + 1)  # effectively off


def restore_console():
    """Restore console output after suppress_console()."""
    global _console_suppressed
    _console_suppressed = False
    if _file_logger is not None:
        for h in _file_logger.handlers:
            if isinstance(h, RichHandler):
                h.setLevel(logging.INFO)


# ── Initialization ──────────────────────────────────────────────────

def init_logger(name: str = "simp-console") -> logging.Logger:
    """Initialize the file logger. Returns the Python logger for compatibility."""
    global _file_logger
    if _file_logger is not None:
        return _file_logger

    os.makedirs(LOG_DIR, exist_ok=True)

    # Build a root logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # ── File handler (rotating, 5MB × 3 files) ──
    log_file = LOG_DIR / f"simp-console_{datetime.now().strftime('%Y%m%d')}.log"
    fh = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    # ── Rich handler (console) ──
    rh = RichHandler(
        console=_console,
        show_time=False,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    rh.setLevel(logging.INFO)
    logger.addHandler(rh)

    _file_logger = logger
    return logger


def get_logger() -> logging.Logger:
    """Return the global logger (init must be called first)."""
    assert _file_logger is not None, "Call init_logger() first"
    return _file_logger


# ── Rich helpers ────────────────────────────────────────────────────

def print_header(title: str, subtitle: str = ""):
    """Print a stylised header banner."""
    if _console_suppressed:
        return
    _console.print()
    _console.rule(f"[bold cyan]{title}[/]")
    if subtitle:
        _console.print(f"[dim]{subtitle}[/]")
    _console.print()


def print_success(msg: str):
    if _console_suppressed:
        return
    _console.print(f"[bold green]\u2713[/] {msg}")


def print_error(msg: str):
    if _console_suppressed:
        return
    _console.print(f"[bold red]\u2717[/] {msg}")


def print_warning(msg: str):
    if _console_suppressed:
        return
    _console.print(f"[bold yellow]\u26a0[/] {msg}")


def print_info(msg: str):
    if _console_suppressed:
        return
    _console.print(f"[cyan]\u2139[/] {msg}")


def print_table(title: str, columns: list[str], rows: list[list]):
    """Print a rich table."""
    if _console_suppressed:
        return
    table = Table(title=title, box=box.ROUNDED, title_style="bold")
    for col in columns:
        table.add_column(col, style="cyan")
    for row in rows:
        table.add_row(*[str(c) for c in row])
    _console.print(table)


def print_panel(text: str, title: str = "", style: str = "cyan"):
    if _console_suppressed:
        return
    _console.print(Panel(text, title=title, border_style=style))


def print_divider():
    if _console_suppressed:
        return
    _console.rule()


# ── Direct log function (for callbacks from core modules) ──────────

def log_callback(msg: str, level: str = "INFO"):
    """Callback that core modules can use to pipe logs to our system."""
    logger = get_logger()
    level = level.upper()
    if level == "ERROR":
        logger.error(msg)
    elif level == "WARN" or level == "WARNING":
        logger.warning(msg)
    elif level == "DEBUG":
        logger.debug(msg)
    else:
        logger.info(msg)


def console(msg: str):
    """Print plain text to console without logging level prefix."""
    if not _console_suppressed:
        _console.print(msg)


# ── Download progress callback (handles downloader's kwarg protocol) ──

def make_download_progress_callback(model_name: str = ""):
    """Return a progress callback compatible with the downloader's kwarg-based protocol.

    Handles event patterns like:
      ('start', total_posts=..., total_urls=...)
      ('resolving', total=...)
      ('resolve_progress', resolved=..., total=..., eta=..., …)
      ('file', filename=..., overall_total=..., overall_completed=..., …)
      ('resolved', ok=..., failed=...)
      ('complete', completed=..., failed=..., total_bytes=...)
    """
    last_pct = [0]

    def _cb(*args, **kwargs):
        nonlocal last_pct
        if not args:
            return
        event = args[0]
        logger = get_logger()

        if event == 'start':
            posts = kwargs.get('total_posts', '?')
            urls = kwargs.get('total_urls', '?')
            logger.info(f"Downloading {model_name}: {posts} posts, {urls} URLs")

        elif event == 'resolving':
            total = kwargs.get('total', 0)
            logger.info(f"Resolving {total} URLs...")

        elif event == 'resolve_progress':
            resolved = kwargs.get('resolved', 0)
            total = kwargs.get('total', 0)
            eta = kwargs.get('eta', 0)
            pct = int(resolved / total * 100) if total else 0
            if pct >= last_pct[0] + 10 or resolved == total:
                eta_str = f" (ETA: {eta}s)" if eta else ""
                logger.info(f"Resolving: {resolved}/{total} ({pct}%){eta_str}")
                last_pct[0] = pct

        elif event == 'file':
            completed = kwargs.get('overall_completed', 0)
            total = kwargs.get('overall_total', 0)
            pct = int(completed / total * 100) if total else 0
            if pct >= last_pct[0] + 5 or completed == total:
                filename = kwargs.get('filename', '')
                eta = kwargs.get('overall_eta', 0)
                eta_str = f" (ETA: {eta}s)" if eta else ""
                logger.info(f"Download: {completed}/{total} ({pct}%) — {filename}{eta_str}")
                last_pct[0] = pct

        elif event == 'resolved':
            ok = kwargs.get('ok', 0)
            failed = kwargs.get('failed', 0)
            logger.info(f"Resolved: {ok} OK, {failed} FAIL")

        elif event == 'complete':
            completed = kwargs.get('completed', 0)
            failed = kwargs.get('failed', 0)
            total_bytes = kwargs.get('total_bytes', 0)
            msg = f"Download complete: {completed} files"
            if failed:
                msg += f", {failed} failed"
            if total_bytes:
                from core.registry import format_size
                msg += f" ({format_size(total_bytes)})"
            logger.info(msg)

        else:
            # Fallback for unknown events
            logger.info(f"Download event: {event}")

    last_pct[0] = 0  # reset
    return _cb
