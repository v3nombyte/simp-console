"""Full automated workflow orchestrator.

Walks the user through the complete pipeline:
  1. Choose input source (crawl, HTML files, JSON file, upload JSON)
  2. Extract URLs (if crawl or HTML)
  3. Download automatically
  4. Sort & Dedupe automatically
  5. Rename automatically
"""

import os
import sys
import json
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import questionary

from .logger import (
    print_info, print_success, print_error, print_warning,
    print_header, print_divider, log_callback,
)
from .settings import AppSettings
from .actions import (
    action_crawl, action_extract, action_download_media, action_sort, action_rename,
    action_select_html_files, action_select_json_file, action_import_file,
)
from . import actions as act

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from core.models import ScanResult
from core.extractor import merge_results
from core.registry import ModelRegistry

REGISTRY_PATH = PROJECT_DIR / "models_registry.json"


def run_full_workflow(settings: AppSettings):
    """Run the complete pipeline from start to finish."""
    print_header("🚀 Full Automated Workflow")
    print_info("This will walk you through the complete pipeline.")
    print_info("Let's start by choosing your input source.\n")

    # ── Step 1: Input Source ─────────────────────────────────────────
    source = questionary.select(
        "Select input source:",
        choices=[
            "🌐 Crawl SimpCity Thread (auto-fetch HTML pages)",
            "📄 Select HTML files from input folder (extract URLs)",
            "📂 Select existing JSON file (load previous scan results)",
            "📥 Upload / import a JSON file from disk",
            "← Back to main menu",
        ],
    ).ask()

    if not source or "Back" in source:
        return

    scan_result: ScanResult | None = None
    model_name = ""

    if "Crawl" in source:
        # Step 1a: Crawl
        crawl_result = action_crawl(settings)
        if "error" in crawl_result:
            print_error("Crawl failed — workflow aborted.")
            return

        # Step 1b: Extract from crawled HTML
        input_dir = act._resolve_relative(settings.input_dir)
        html_files = [
            os.path.join(input_dir, f)
            for f in sorted(os.listdir(input_dir))
            if f.endswith(".html")
        ]
        if not html_files:
            print_error("No HTML files found after crawl — workflow aborted.")
            return

        print_info(f"Extracting URLs from {len(html_files)} HTML file(s)...")
        scan_result = action_extract(settings, html_files)

    elif "HTML files" in source:
        # Step 1: Extract from selected HTML files
        html_files = action_select_html_files(settings)
        if not html_files:
            print_info("No files selected — workflow aborted.")
            return
        scan_result = action_extract(settings, html_files)

    elif "existing JSON" in source:
        # Load from existing JSON file
        scan_result = action_select_json_file(settings)
        if not scan_result:
            return

    elif "Upload" in source or "import" in source.lower():
        # Import JSON from arbitrary path
        scan_result = action_import_file(settings)
        if not scan_result:
            return

    if scan_result is None:
        print_error("No results to work with — workflow aborted.")
        return

    model_name = scan_result.model_name

    # ── Verify and confirm ─────────────────────────────────────────
    print_divider()
    print_info(f"Model: [bold]{model_name}[/]")
    print_info(f"URLs extracted: {scan_result.total_urls}")
    print_info(f"Posts: {scan_result.total_posts}")
    print_divider()

    if not questionary.confirm(
        "Proceed with download?",
        default=True,
    ).ask():
        print_info("Workflow paused. You can resume from the main menu.")
        return

    # ── Step 2: Download ───────────────────────────────────────────
    print_header("⬇️  Download Phase")

    # We need to ensure the posts JSON was exported
    registry = ModelRegistry(str(REGISTRY_PATH))
    entry = registry.get(model_name)
    json_path = ""
    if entry:
        modes = entry.get("modes", {})
        for mode_name in reversed(list(modes.keys())):
            fp = modes[mode_name].get("file_paths", {}).get("json", "")
            if fp and os.path.exists(fp):
                json_path = fp
                break

    if not json_path:
        print_error("No posts JSON file found for this model.")
        print_info("Try running extraction again from the main menu.")
        return

    output_dir = act._resolve_relative(settings.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    from core.downloader import run_download

    from .live_display import LiveDownloadPanel
    panel = LiveDownloadPanel(model_name)
    # Pre-load all known URLs for queue display
    try:
        with open(json_path) as _jf:
            _jdata = json.load(_jf)
        _all_urls = []
        for _p in _jdata.get('posts', []):
            _all_urls.extend(_p.get('urls', []))
        panel.set_urls(_all_urls)
    except Exception:
        pass
    event_cb, file_progress_cb = panel.make_event_callback()
    cancel_event = threading.Event()

    _dl_result_box = []

    def _run():
        r = run_download(
            posts_json_path=json_path,
            model_name=model_name,
            output_base=output_dir,
            max_speed_bps=0,
            log_callback=log_callback,
            max_concurrent=settings.max_concurrent_downloads,
            cancel_event=cancel_event,
            progress_callback=event_cb,
            file_progress_callback=file_progress_cb,
            streaming_resolve=settings.streaming_resolve,
        )
        _dl_result_box.append(r)

    dl_thread = threading.Thread(target=_run, daemon=True)
    dl_thread.start()

    with panel:
        while dl_thread.is_alive():
            dl_thread.join(timeout=0.25)
            panel.refresh()

    dl_result = _dl_result_box[0] if _dl_result_box else None

    if dl_result:
        total_ok = dl_result.get('completed', dl_result.get('total_files', 0))
        total_skip = dl_result.get('skipped', dl_result.get('skipped_count', 0))
        total_fail = dl_result.get('failed', dl_result.get('failed_count', 0))
        total_bytes = dl_result.get('total_bytes', 0)
        total_done = total_ok + total_skip

        msg = f"Download complete: {total_done} files OK"
        if total_skip:
            msg += f" ({total_skip} skipped)"
        print_success(msg)

        # Show failed URL details
        act._show_failed_urls(dl_result)

        # Register download
        registry.register_download(
            model_name=model_name,
            total_urls=total_done + total_fail,
            total_files=total_done,
            total_bytes=total_bytes,
            failed_count=total_fail,
            skipped_count=total_skip,
        )

        if total_fail > 0:
            print_warning(f"  {total_fail} failed (can retry from menu)")
            retry_now = questionary.confirm(
                "Retry failed downloads now?", default=True,
            ).ask()
            if retry_now:
                failed = getattr(dl_result, 'failed_urls', [])
                if failed:
                    from core.downloader import retry_failed_urls
                    retry_result = retry_failed_urls(
                        failed, model_name, output_dir,
                        log_callback=log_callback,
                        cancel_event=cancel_event,
                    )
                    print_info(f"Retry: {retry_result.get('succeeded', 0)} OK")
                else:
                    print_info("No retry-able URLs found.")

    # ── Step 3: Sort & Dedupe ──────────────────────────────────────
    print_header("📦 Sort & Dedupe Phase")

    if not questionary.confirm(
        "Proceed with sorting and deduplication?",
        default=True,
    ).ask():
        print_info("Workflow paused. You can sort from the menu.")
        return

    # Check if output folder exists
    source_dir = os.path.join(output_dir, model_name)
    if not os.path.isdir(source_dir):
        print_warning(f"Output folder not found: {source_dir}")
        print_info("Skipping sort phase — run download first.")
        return

    sort_dir = settings.sort_dir
    sort_mode = questionary.select(
        "Sort mode:",
        choices=[
            questionary.Choice("Copy — keep originals (safe)", "copy"),
            questionary.Choice("Move — delete originals (saves space)", "move"),
            questionary.Separator(),
            questionary.Choice("← Back", None),
        ],
        default="copy",
    ).ask()
    if not sort_mode:
        print_info("Sort cancelled.")
        return

    dedup = questionary.confirm(
        "Enable quality-aware deduplication?",
        default=True,
    ).ask()

    def sort_progress(processed, total, fname):
        pct = int(processed / total * 100) if total else 0
        if pct % 25 == 0 or processed == total:
            log_callback(f"Sort progress: {processed}/{total} ({pct}%)", "SORT")

    from core.sorter import sort_model_folder
    stats = sort_model_folder(
        model_name=model_name,
        source_base=output_dir,
        dest_base=sort_dir,
        action=sort_mode,
        dedup=dedup,
        progress_callback=sort_progress,
    )

    if stats.get("errors"):
        for err in stats["errors"]:
            print_warning(err)

    print_success(
        f"Sort complete — {stats.get('total_files', 0)} total files, "
        f"{stats.get('total_processed', 0)} processed"
    )
    print_info(
        f"  Images: {stats.get('images_total', 0)}  "
        f"|  Videos: {stats.get('videos_total', 0)}  "
        f"|  Unknown: {stats.get('unknown_total', 0)}"
    )
    unknown_exts = stats.get('unknown_extensions')
    if unknown_exts:
        ext_list = sorted(unknown_exts, key=lambda x: (x != '.txt', x))
        print_info(f"  Unknown extensions: {', '.join(ext_list)}")
    if stats.get("duplicates_removed"):
        print_info(f"  Duplicates removed: {stats['duplicates_removed']}")

    # Mark sorted in registry
    entry = registry.get(model_name)
    if entry:
        entry["sorted"] = True
        entry["sorted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        registry.save()

    # ── Step 4: Rename ────────────────────────────────────────────
    print_header("🏷️  Rename Phase")

    if not questionary.confirm(
        "Proceed with renaming files?",
        default=True,
    ).ask():
        print_info("Workflow complete (rename skipped).")
        return

    # Find the sorted folder
    sorted_path = os.path.join(sort_dir, model_name)
    if not os.path.isdir(sorted_path):
        print_warning(f"Sorted folder not found: {sorted_path}")
        print_info("Rename skipped.")
        return

    # Ask for base name for renaming
    base_name = questionary.text(
        "Base name for renamed files (leave empty to cancel):",
        default=model_name,
    ).ask()
    if not base_name:
        print_info("Rename cancelled.")
        return
    base_name = base_name.strip() or model_name

    subdirs = [d for d in os.listdir(sorted_path)
               if os.path.isdir(os.path.join(sorted_path, d))]

    if subdirs:
        rename_targets = [questionary.Choice(title=sd, value=sd) for sd in subdirs]
        rename_targets.append(questionary.Choice(title="All (images & videos)", value="__all__"))
        rename_targets.append(questionary.Separator())
        rename_targets.append(questionary.Choice(title="← Back", value=None))

        target = questionary.select(
            "Which folder to rename?",
            choices=rename_targets,
        ).ask()

        if not target:
            print_info("Rename cancelled.")
            return

        if target == "__all__":
            for sd in subdirs:
                act._rename_in_folder(os.path.join(sorted_path, sd), base_name)
        else:
            act._rename_in_folder(os.path.join(sorted_path, target), base_name)
    else:
        act._rename_in_folder(sorted_path, base_name)

    print_success("✅ Full workflow complete!")
    print_info(f"Model: {model_name}")
    print_info(f"Sorted: {sorted_path}")
    print_info(f"Download output: {source_dir}")
