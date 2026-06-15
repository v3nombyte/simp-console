"""Live download panel for Simp Console.

Uses rich.live.Live to display real-time download progress:
  - Header: model name, URLs, files, size, speed, ETA
  - Body: up to 10 rows — active files with progress bars + queued upcoming URLs
  - Footer: OK/Failed/Skipped counters
"""

import os
import sys
import time
import threading
from typing import Optional
from urllib.parse import urlparse

from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box

from .logger import print_info, print_success


def _parse_host(url: str) -> str:
    """Extract a short host name from a URL."""
    host = urlparse(url).netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    host = host.replace('socialmediagirls', 'smg')
    host = host.replace('forums.', '')
    for suffix in ('.com', '.cr', '.su', '.ru', '.me', '.sk', '.pk', '.ws',
                   '.bz', '.cat', '.fi', '.red', '.si', '.black', '.ac',
                   '.ci', '.ph', '.ax', '.ps', '.to', '.st', '.cc', '.tv'):
        host = host.replace(suffix, '')
    return host[:15]


def _format_time(seconds: float) -> str:
    if seconds <= 0:
        return "\u2014"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def _format_size(bytes_val: int) -> str:
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 ** 2:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 ** 3:
        return f"{bytes_val / 1024 ** 2:.1f} MB"
    else:
        return f"{bytes_val / 1024 ** 3:.2f} GB"


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 ** 2:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    else:
        return f"{bytes_per_sec / 1024 ** 2:.1f} MB/s"


def _truncate_filename(name: str, max_len: int = 34) -> str:
    """Truncate filename keeping the extension visible."""
    if len(name) <= max_len:
        return name
    base, dot_ext = os.path.splitext(name)
    ext = dot_ext[:6]  # keep up to .extension (6 chars)
    available = max_len - len(ext) - 3  # -3 for "..."
    if available < 4:
        # Very short — just truncate hard
        return name[:max_len - 3] + "..."
    return base[:available] + "..." + ext


class LiveDownloadPanel:
    """Rich live display for download progress."""

    def __init__(self, model_name: str, total_urls: int = 0):
        self.model_name = model_name
        self._total_urls = total_urls
        self._urls_done = 0
        self._resolved_count = 0  # separate counter for resolve phase
        self._ok_count = 0        # files actually downloaded
        self._skip_count = 0      # files already existed
        self._fail_count = 0
        self._total_bytes = 0
        self._speed = 0.0
        self._eta = 0
        self._errors: list[str] = []
        self._paused = False
        # Active files: list of dicts — {url, filename, host, downloaded, total_bytes, speed, status}
        self._active: list[dict] = []
        # All known URLs (for queue display) — populated via set_urls()
        self._all_urls: list[str] = []
        self._queued_idx = 0  # how many from _all_urls have started
        self._lock = threading.Lock()
        self._live: Optional[Live] = None
        self._start_time = time.time()
        self._complete = False
        self._current_task = "Initializing..."

    def set_urls(self, url_list: list[str]):
        """Pre-populate the full list of known URLs for queue display."""
        with self._lock:
            self._all_urls = list(url_list)
            self._total_urls = len(url_list)

    def update_meta(self, **kwargs):
        with self._lock:
            if 'total_urls' in kwargs:
                self._total_urls = kwargs['total_urls']
            if 'resolved' in kwargs:
                self._resolved_count = kwargs['resolved']

    def file_start(self, url: str, filename: str, host: str):
        """Signal that a file has started downloading — move from queue."""
        with self._lock:
            # Remove from queued index (if found in all_urls)
            if url and self._all_urls:
                try:
                    idx = self._all_urls.index(url, self._queued_idx)
                    # Remove from queue by inserting a marker that will be skipped
                    self._all_urls[idx] = None
                except ValueError:
                    pass
                self._queued_idx += 1

            # Check for duplicate already in active
            for f in self._active:
                if f.get('url') == url or (not url and f.get('filename') == filename):
                    f['status'] = 'downloading'
                    if host:
                        f['host'] = host
                    return

            self._active.append({
                'url': url,
                'filename': filename,
                'host': host,
                'downloaded': 0,
                'total_bytes': 0,
                'speed': 0.0,
                'status': 'downloading',
            })

    def file_progress(self, url: str, filename: str, downloaded: int,
                      total_bytes: int, speed: float):
        """Update progress for an active file."""
        with self._lock:
            matched = False
            for f in self._active:
                if (url and f.get('url') == url) or (not url and f.get('filename') == filename):
                    f['downloaded'] = downloaded
                    f['total_bytes'] = total_bytes
                    f['speed'] = speed
                    if f['status'] == 'queued':
                        f['status'] = 'downloading'
                    matched = True
                    break
            if not matched:
                # Auto-add as new active entry
                self._active.append({
                    'url': url, 'filename': filename, 'host': '',
                    'downloaded': downloaded, 'total_bytes': total_bytes,
                    'speed': speed, 'status': 'downloading',
                })

            # Update overall speed and ETA from active files
            total_speed = sum(f.get('speed', 0) for f in self._active)
            self._speed = total_speed

    def file_done(self, url: str, filename: str, ok: bool, size: int = 0,
                  error: str = "", host: str = "", skipped: bool = False):
        """Mark a file as completed and remove from active list."""
        with self._lock:
            if skipped:
                self._skip_count += 1
            elif ok:
                self._ok_count += 1
            else:
                self._fail_count += 1
                if error:
                    self._errors.append(f"{filename}: {error}")

            self._total_bytes += size
            self._urls_done += 1

            # Remove from queue so it doesn't show as queued anymore
            if url and self._all_urls:
                try:
                    idx = self._all_urls.index(url)
                    self._all_urls[idx] = None
                except ValueError:
                    pass

            # Remove from active
            if url:
                self._active = [f for f in self._active if f.get('url') != url]
            else:
                self._active = [f for f in self._active if f.get('filename') != filename]

    def file_skipped(self, url: str, filename: str):
        with self._lock:
            self._skip_count += 1
            self._urls_done += 1
            # Remove from queue
            if url and self._all_urls:
                try:
                    idx = self._all_urls.index(url)
                    self._all_urls[idx] = None
                except ValueError:
                    pass
            if url:
                self._active = [f for f in self._active if f.get('url') != url]
            else:
                self._active = [f for f in self._active if f.get('filename') != filename]

    def set_complete(self):
        with self._lock:
            self._complete = True

    def _render_active_row(self, idx: int, f: dict) -> list:
        filename = f.get('filename', '?')
        host = f.get('host', '') or _parse_host(f.get('url', ''))
        downloaded = f.get('downloaded', 0)
        total = f.get('total_bytes', 0)
        speed = f.get('speed', 0.0)

        status = f.get('status', 'queued')
        if status == 'queued':
            return [
                str(idx),
                _truncate_filename(filename, 34),
                host[:12],
                "queued",
                "\u2014",
                "\u2014",
            ]

        if total > 0:
            size_str = f"{_format_size(downloaded)} / {_format_size(total)}"
            pct = int(downloaded / total * 100)
        else:
            size_str = _format_size(downloaded)
            pct = -1

        bar_width = 14
        if pct >= 0:
            filled = int(pct / 100 * bar_width)
            bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
            pct_str = f"{pct:3d}%"
        else:
            bar = "\u2591" * bar_width
            pct_str = " ?%"

        speed_str = _format_speed(speed) if speed > 0 else "\u2014"

        return [
            str(idx),
            _truncate_filename(filename, 34),
            host[:12],
            f"{bar} {pct_str}",
            size_str,
            speed_str,
        ]

    def _make_layout(self) -> Layout:
        with self._lock:
            elapsed = time.time() - self._start_time
            elapsed_str = _format_time(elapsed)

            # ── Compact Header ──
            total_urls_str = str(self._total_urls) if self._total_urls else "?"
            pct = int(self._urls_done / self._total_urls * 100) if self._total_urls > 0 else 0

            task_str = self._current_task or ""

            # Line 1: title + current task
            h1 = f"[bold cyan]\u2b07 {self.model_name}[/]  [dim]| {task_str}[/]"

            # Line 2: URL count / OK+Failed+Skip / size / speed / ETA / elapsed
            parts = [
                f"[bold]{self._urls_done}[/]/{total_urls_str} ({pct}%)",
            ]
            ok_str = f"[green]{self._ok_count} OK[/]"
            if self._skip_count:
                ok_str += f"  [yellow]{self._skip_count} Skipped[/]"
            if self._fail_count:
                ok_str += f"  [red]{self._fail_count} Failed[/]"
            parts.append(ok_str)
            parts.append(f"{_format_size(self._total_bytes)}")
            if self._speed > 0:
                parts.append(f"[cyan]{_format_speed(self._speed)}[/]")
            if self._eta > 0:
                parts.append(f"ETA {_format_time(self._eta)}")
            parts.append(f"[dim]\u23f1 {elapsed_str}[/]")

            h2 = "  \u2502  ".join(parts)

            # ── Body: active files + queued upcoming ──
            body_table = Table(
                box=box.SIMPLE,
                show_header=True,
                header_style="bold",
                padding=(0, 1),
                collapse_padding=True,
            )
            body_table.add_column("#", width=3, justify="right")
            body_table.add_column("File", width=38, no_wrap=True)
            body_table.add_column("Host", width=13, no_wrap=True)
            body_table.add_column("Progress", width=22, no_wrap=True)
            body_table.add_column("Size", width=22, no_wrap=True)
            body_table.add_column("Speed", width=10, no_wrap=True)

            # Build combined list: active first, then queued from _all_urls
            queued_urls = []
            if self._all_urls:
                for u in self._all_urls[self._queued_idx:]:
                    if u is not None:
                        queued_urls.append(u)
                    if len(queued_urls) >= 10:
                        break

            combined_rows = list(self._active[:10])  # active (currently downloading)
            # Fill remaining slots with queued entries
            slots_left = 10 - len(combined_rows)
            for u in queued_urls[:slots_left]:
                fname = os.path.basename(urlparse(u).path) or u.split('/')[-1][:40]
                if not fname:
                    fname = u[:40]
                combined_rows.append({
                    'url': u,
                    'filename': fname,
                    'host': _parse_host(u),
                    'downloaded': 0,
                    'total_bytes': 0,
                    'speed': 0.0,
                    'status': 'queued',
                })

            if combined_rows:
                for idx, f in enumerate(combined_rows, 1):
                    row = self._render_active_row(idx, f)
                    body_table.add_row(*row)
            else:
                if self._complete:
                    body_table.add_row("\u2014", "All downloads finished", "", "", "", "")
                elif self._all_urls:
                    body_table.add_row("\u2014", "Preparing...", "", "", "", "")
                else:
                    body_table.add_row("\u2014", "Waiting for downloads...", "", "", "", "")

            # ── Footer just errors + keyboard hints (no completed files) ──
            footer_parts = []
            if self._errors and len(self._errors) <= 3:
                for err in self._errors[-3:]:
                    self._errors = []  # clear after showing
                    footer_parts.append(f"  [red]![/] {err}")
            footer_parts.append("")

            # ── Host queue breakdown ──
            host_queue_lines = []
            if self._all_urls and self._queued_idx < len(self._all_urls):
                host_counts: dict[str, int] = {}
                for u in self._all_urls[self._queued_idx:]:
                    if u is None:
                        continue
                    host = _parse_host(u)
                    if not host:
                        host = "unknown"
                    host_counts[host] = host_counts.get(host, 0) + 1
                if host_counts:
                    host_queue_lines.append("")
                    host_queue_lines.append("  [bold]Host Queue:[/]")
                    host_queue_lines.append(f"  {'Host':<20} {'Files':>5}")
                    host_queue_lines.append(f"  {'-'*20} {'-'*5}")
                    for h, cnt in sorted(host_counts.items(), key=lambda x: -x[1]):
                        host_queue_lines.append(f"  {h:<20} {cnt:>5}")
                    host_queue_lines.append("")

            footer_parts.append("  [dim][C] Cancel  [Ctrl+C] Back to menu[/]")

            footer_text = "\n".join(footer_parts + host_queue_lines) if footer_parts else ""

            # ── Assemble ──
            header_render = Text.from_markup(f"{h1}\n{h2}")

            # Use Layout for the complete panel
            layout = Layout()
            layout.split_column(
                Layout(Panel(header_render, box=box.ROUNDED, border_style="cyan",
                             title="Download Progress", title_align="left"),
                       size=4),
                Layout(body_table, size=min(3 + len(combined_rows), 13)),
                Layout(Panel(Text.from_markup(footer_text) if footer_text else Text(""),
                             box=box.SIMPLE, border_style="gray50")),
            )
            return layout

    def __enter__(self):
        from .logger import suppress_console
        suppress_console()
        # Suppress direct print() calls from core.downloader._log
        import core.downloader as _dl_mod
        self._original_dl_log = _dl_mod._log
        def _silent_log(msg, level='INFO'):
            if _dl_mod._log_callback:
                _dl_mod._log_callback(msg, level)
        _dl_mod._log = _silent_log
        self._live = Live(
            self._make_layout(),
            refresh_per_second=4,
            screen=False,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *args):
        from .logger import restore_console
        # Restore downloader's original _log
        import core.downloader as _dl_mod
        if hasattr(self, '_original_dl_log'):
            _dl_mod._log = self._original_dl_log
        self.set_complete()
        if self._live:
            self._live.refresh()
            self._live.__exit__(*args)
        restore_console()

    def refresh(self):
        if self._live:
            self._live.update(self._make_layout())

    def make_event_callback(self):
        """Return a progress_callback function compatible with the downloader's event protocol.

        Also returns a file_progress_callback for per-file progress.
        """
        panel = self

        def event_cb(*args, **kwargs):
            if not args:
                return
            event = args[0]

            if event == 'start':
                total_urls = kwargs.get('total_urls', 0)
                total_posts = kwargs.get('total_posts', 0)
                with panel._lock:
                    panel._total_urls = total_urls
                    panel._current_task = f"Starting \u2014 {total_posts} posts, {total_urls} URLs"
                panel.refresh()

            elif event == 'resolving':
                total = kwargs.get('total', 0)
                with panel._lock:
                    panel._current_task = f"Resolving {total} URLs..."
                panel.refresh()

            elif event == 'resolve_progress':
                resolved = kwargs.get('resolved', 0)
                total = kwargs.get('total', 0)
                oc = kwargs.get('overall_completed', 0)
                ot = kwargs.get('overall_total', total)
                eta = kwargs.get('overall_eta', 0)
                with panel._lock:
                    panel._total_urls = total
                    panel._resolved_count = resolved
                    if oc > 0:
                        panel._current_task = f"Downloading [{oc}/{ot}] \u2014 resolving {resolved}/{total}"
                    elif eta:
                        panel._current_task = f"Resolving URLs ({resolved}/{total}, ETA {_format_time(eta)})"
                    else:
                        panel._current_task = f"Resolving URLs ({resolved}/{total})"
                panel.refresh()

            elif event == 'file':
                filename = kwargs.get('filename', '')
                strategy = kwargs.get('strategy', '')
                completed = kwargs.get('overall_completed', 0)
                total = kwargs.get('overall_total', 0)
                eta = kwargs.get('overall_eta', 0)
                host = kwargs.get('host', '')
                ok = kwargs.get('ok', True)
                size = kwargs.get('size', 0)
                url = kwargs.get('url', '')
                error = kwargs.get('error', '')
                skipped = kwargs.get('skipped', False)
                with panel._lock:
                    panel._eta = eta
                    panel._current_task = f"Downloading [{completed}/{total}]" + (f" \u2014 {_truncate_filename(filename, 34)}" if filename else "")
                # Update counters via file_done — now with proper skip handling
                panel.file_done(url, filename, ok, size=size, error=error,
                                host=host, skipped=skipped)
                panel.refresh()

            elif event == 'resolved':
                okc = kwargs.get('ok', 0)
                fld = kwargs.get('failed', 0)
                with panel._lock:
                    panel._current_task = f"Resolved: {okc} OK, {fld} failed"
                panel.refresh()

            elif event == 'complete':
                panel.set_complete()
                with panel._lock:
                    okc = kwargs.get('completed', panel._ok_count)
                    fld = kwargs.get('failed', panel._fail_count)
                    skp = kwargs.get('skipped', panel._skip_count)
                    parts = [f"Complete \u2014 {okc} OK"]
                    if skp:
                        parts.append(f"{skp} skipped")
                    if fld:
                        parts.append(f"{fld} failed")
                    panel._current_task = ", ".join(parts)
                panel.refresh()

        def file_progress_cb(dl_url, filename, downloaded, total_bytes, speed):
            """Called from download thread for each chunk."""
            panel.file_progress(dl_url, filename, downloaded, total_bytes, speed)
            if speed > 0:
                with panel._lock:
                    panel._speed = speed
            # Throttle visual refresh (not state update) to keep UI responsive
            if downloaded % (512 * 1024) < 65536 or downloaded == total_bytes or downloaded == 0:
                panel.refresh()

        return event_cb, file_progress_cb


def run_with_live_display(model_name: str, download_func, **dl_kwargs):
    """Run a download with the live display panel.

    Args:
        model_name: Name of the model being downloaded
        download_func: Callable that runs the download (e.g. run_download)
        **dl_kwargs: Passed to download_func (except file_progress_callback)
    """
    panel = LiveDownloadPanel(model_name)
    event_cb, file_progress_cb = panel.make_event_callback()

    dl_kwargs['progress_callback'] = event_cb
    dl_kwargs['file_progress_callback'] = file_progress_cb

    def _run():
        try:
            result = download_func(**dl_kwargs)
            return result
        finally:
            panel.set_complete()

    dl_thread = threading.Thread(target=_run, daemon=True)
    dl_thread.start()

    with panel:
        while dl_thread.is_alive():
            dl_thread.join(timeout=0.25)
            panel.refresh()

    dl_thread.join(timeout=0.5)

    # Show final summary
    total_ok = panel._ok_count + panel._skip_count  # downloaded + already existed
    summary_parts = [
        f"\n[bold]Download complete[/]: [green]{total_ok} OK[/]"
    ]
    if panel._skip_count:
        summary_parts.append(f"([yellow]{panel._skip_count} skipped[/])")
    if panel._fail_count:
        summary_parts.append(f"[red]{panel._fail_count} failed[/]")
    summary_parts.append(f" \u2014  {_format_size(panel._total_bytes)} total")

    print_success(" ".join(summary_parts))

    return True
