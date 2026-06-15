"""Individual action wrappers for each pipeline step."""

import os
import json
import sys
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import questionary

from .logger import (
    print_info, print_success, print_error, print_warning,
    print_header, print_divider, log_callback
)
from .settings import AppSettings

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Core imports
sys.path.insert(0, str(PROJECT_DIR))
from core.crawler import crawl_thread, validate_cookie_json
from core.extractor import extract_from_folder, merge_results, load_json
from core.models import ScanResult
from core.exporter import export_all_formats, export_json as _export_json, import_json as _import_json
from core.sorter import sort_model_folder
from core.renamer import rename_files
from core.downloader import run_download
from core.registry import ModelRegistry


REGISTRY_PATH = PROJECT_DIR / "models_registry.json"


# ── Helpers ─────────────────────────────────────────────────────────

def _get_registry():
    return ModelRegistry(str(REGISTRY_PATH))


def _write_action_log(action: str, model_name: str, entry: str):
    """Write to a per-action log file, prepending so newest entry is at top."""
    from pathlib import Path
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{action}_{model_name}.log"
    from datetime import datetime
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {entry}\n"
    if log_file.exists():
        old = log_file.read_text(encoding='utf-8')
        log_file.write_text(line + old, encoding='utf-8')
    else:
        log_file.write_text(line, encoding='utf-8')


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_relative(path: str) -> str:
    if not os.path.isabs(path):
        return str(PROJECT_DIR / path)
    return path


def _is_gallery_url(url: str) -> bool:
    """Check if a URL is a gallery/album link that may contain multiple files."""
    path = urlparse(url).path.lower()
    return any([
        '/a/' in path,      # bunkr albums
        '/d/' in path,      # gofile folders
        '/album/' in path,  # generic
        '/gallery/' in path,
        '/v/' in path,      # bunkr video album
        '/f/' in path,      # bunkr folder
    ])


def _show_failed_urls(result: dict, max_show: int = 15):
    """Display failed URLs with reasons from a download result dict."""
    failed_urls = result.get('failed_urls') or result.get('failed_urls', [])
    if not failed_urls:
        return
    failed_urls = list(failed_urls)
    print_warning(f"Failed downloads ({len(failed_urls)}):")
    for entry in failed_urls[:max_show]:
        if isinstance(entry, (list, tuple)) and len(entry) >= 5:
            url, _model, page, post, reason = entry[:5]
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            url, reason = entry[0], entry[1]
        else:
            url = str(entry)[:80]
            reason = ""
        gallery_note = " [gallery/album]" if _is_gallery_url(url) else ""
        print_info(f"  {url[:100]}  [red]({reason}){gallery_note}[/]")
    if len(failed_urls) > max_show:
        print_info(f"  ... and {len(failed_urls) - max_show} more (see failed_downloads_*.txt)")


# ── Crawler ─────────────────────────────────────────────────────────

def action_crawl(settings: AppSettings) -> dict:
    """Crawl a SimpCity thread."""
    print_info("SimpCity Thread Crawler")

    if not settings.cookies_json:
        print_error("No cookies saved. Set them in Settings → Manage Cookies first.")
        return {"error": "No cookies"}

    # Validate cookies
    validation = validate_cookie_json(settings.cookies_json)
    if isinstance(validation, str):
        print_error(f"Invalid cookies: {validation}")
        return {"error": validation}

    url = questionary.text("Thread URL:").ask()
    if not url or not url.strip():
        print_error("No URL provided.")
        return {"error": "No URL"}

    max_pages = questionary.text(
        "Max pages to fetch",
        default="50",
    ).ask()
    try:
        max_pages = int(max_pages.strip()) if max_pages else 50
    except ValueError:
        max_pages = 50

    input_dir = _resolve_relative(settings.input_dir)
    _ensure_dir(input_dir)

    print_info(f"Crawling: {url}")
    print_info(f"Input directory: {input_dir}")

    cancel_event = threading.Event()

    result = crawl_thread(
        url=url.strip(),
        cookies_json=settings.cookies_json,
        input_dir=input_dir,
        request_delay=settings.request_delay,
        user_agent=settings.user_agent,
        max_pages=max_pages,
        cancel_check=lambda: cancel_event.is_set(),
        log_callback=lambda msg: log_callback(msg, "CRAWL"),
    )

    if "error" in result:
        print_error(f"Crawl failed: {result['error']}")
    else:
        print_success(
            f"Crawl complete: {result.get('pages', 0)}/{result.get('total', 0)} pages fetched"
        )
        if result.get("errors"):
            for err in result["errors"]:
                print_warning(f"  {err}")

    return result


# ── Extraction ──────────────────────────────────────────────────────

def scan_input_files(folder_path: str) -> list[str]:
    """Recursively find all HTML files — same as webapp's scan_input_files."""
    files = []
    if not os.path.isdir(folder_path):
        return files
    for fname in os.listdir(folder_path):
        if fname.endswith('.html'):
            files.append(os.path.join(folder_path, fname))
    for root, _dirs, fnames in os.walk(folder_path):
        for f in fnames:
            if f.endswith('.html') and os.path.join(root, f) not in files:
                files.append(os.path.join(root, f))
    return files


def action_select_html_files(settings: AppSettings) -> list[str]:
    """Select HTML files with archive-group and loose-file display (like webapp).

    Files in subdirectories are grouped as 'archived' sets (📁 folder).
    Files directly in the input root are 'loose' and shown individually.
    """
    input_dir = _resolve_relative(settings.input_dir)
    if not os.path.isdir(input_dir):
        print_warning(f"Input directory does not exist: {input_dir}")
        create = questionary.confirm("Create it now?", default=True).ask()
        if create:
            os.makedirs(input_dir, exist_ok=True)
        else:
            return []

    all_html = scan_input_files(input_dir)
    if not all_html:
        print_warning(f"No HTML files found in {input_dir}")
        print_info("Tip: Use 'Crawl SimpCity Thread' first, or place HTML files manually.")
        return []

    # Group: archived (subdirectory) vs loose (root)
    loose = []
    archived: dict[str, list[str]] = {}  # dir_name -> [file_paths]
    for fp in all_html:
        rel = os.path.relpath(fp, input_dir)
        if os.sep in rel:
            arch_dir = rel.split(os.sep)[0]
            archived.setdefault(arch_dir, []).append(fp)
        else:
            loose.append(fp)

    # Build choices: archive folders first, then loose files
    choices = []
    for arch_name in sorted(archived.keys()):
        afiles = archived[arch_name]
        n = len(afiles)
        choices.append(questionary.Choice(
            title=f"📁 {arch_name}/  ({n} file{'s' if n > 1 else ''})  [archive]",
            value=("archive", arch_name, afiles),
        ))

    for fp in loose:
        fname = os.path.relpath(fp, input_dir)
        size = os.path.getsize(fp)
        size_str = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        choices.append(questionary.Choice(
            title=f"  {fname}  ({size_str})",
            value=("file", fname, fp),
        ))

    if not choices:
        print_warning(f"No HTML files found in {input_dir}")
        return []

    print_info(f"Found {len(all_html)} HTML file(s) in {input_dir}")
    print_info("  📁 Archive folders = files in subdirectories (click to toggle all)")
    print_info("  Other files = loose files in input root\n")

    # Multi-step selection using questionary checkbox
    selected_paths: set[str] = set()
    cycle_limit = 0

    while cycle_limit < 10:
        cycle_limit += 1
        sel_choices = []

        # Show current selection state for each item
        for arch_name in sorted(archived.keys()):
            afiles = archived[arch_name]
            n = len(afiles)
            all_sel = all(f in selected_paths for f in afiles)
            some_sel = any(f in selected_paths for f in afiles)
            if all_sel:
                status = "☑"
            elif some_sel:
                status = "⊞"
            else:
                status = "☐"
            sel_choices.append(questionary.Choice(
                title=f"{status}  📁 {arch_name}/ ({n} file{'s' if n > 1 else ''})",
                value=("archive", arch_name, afiles),
            ))

        for fp in loose:
            fname = os.path.relpath(fp, input_dir)
            checked = "☑" if fp in selected_paths else "☐"
            sel_choices.append(questionary.Choice(
                title=f"{checked}  {fname}",
                value=("file", fname, fp),
            ))

        sel_choices.append(questionary.Separator())
        sel_choices.append(questionary.Choice(
            title="✅  Done selecting — proceed to extraction",
            value="__done__",
        ))
        sel_choices.append(questionary.Choice(
            title="☐  Deselect all",
            value="__clear__",
        ))

        pick = questionary.select(
            f"Select files ({len(selected_paths)} currently selected):",
            choices=sel_choices,
            instruction="(click to toggle)",
        ).ask()

        if not pick or pick == "__done__":
            break
        elif pick == "__clear__":
            selected_paths.clear()
            continue

        kind, name, data = pick
        if kind == "archive":
            afiles = data
            all_selected = all(f in selected_paths for f in afiles)
            if all_selected:
                for f in afiles:
                    selected_paths.discard(f)
            else:
                selected_paths.update(afiles)
        elif kind == "file":
            fp = data
            if fp in selected_paths:
                selected_paths.discard(fp)
            else:
                selected_paths.add(fp)

    result = list(selected_paths)
    if not result:
        all_files = questionary.confirm(
            "No files selected. Use all files in the input directory?",
            default=True,
        ).ask()
        if all_files:
            result = all_html

    return result


def action_extract(settings: AppSettings, file_paths: list[str] | None = None) -> ScanResult | None:
    """Extract URLs from selected HTML files."""
    print_info("Extracting URLs from HTML files...")

    if not file_paths:
        file_paths = action_select_html_files(settings)
        if not file_paths:
            print_error("No HTML files selected.")
            return None

    patterns_path = str(PROJECT_DIR / "url_patterns.json")
    skip_path = str(PROJECT_DIR / "skip_url.json")
    patterns = load_json(patterns_path, None)
    skip_patterns = load_json(skip_path, None)

    results = []
    for fpath in file_paths:
        try:
            from core.extractor import extract_from_html
            sr = extract_from_html(fpath, patterns, skip_patterns, settings.extraction_mode)
            results.append(sr)
        except Exception as e:
            print_error(f"Failed to extract from {os.path.basename(fpath)}: {e}")

    if not results:
        print_error("No URLs extracted from any file.")
        return None

    # Ask for model name
    default_name = results[0].model_name if results[0].model_name else ""
    import re as _re
    # Clean up default: strip .html extension and trailing page patterns
    default_name = _re.sub(r'\.html?$', '', default_name, flags=_re.IGNORECASE)
    default_name = _re.sub(r'\s*[-–|]\s*Page\s*\d+.*$', '', default_name, flags=_re.IGNORECASE).strip()
    model_name = questionary.text(
        "Model name for this extraction",
        default=default_name,
    ).ask()
    if not model_name or not model_name.strip():
        model_name = default_name or "unknown"
    model_name = model_name.strip()

    # Archive: move processed HTML files into subfolder (like webapp)
    archive_files = questionary.confirm(
        "Archive processed HTML files into subfolder?",
        default=True,
    ).ask()

    if archive_files:
        input_dir = _resolve_relative(settings.input_dir)
        arch_dir = os.path.join(input_dir, model_name)
        os.makedirs(arch_dir, exist_ok=True)
        import shutil
        for fp in file_paths:
            try:
                shutil.move(fp, os.path.join(arch_dir, os.path.basename(fp)))
            except Exception as e:
                log_callback(f"Failed to archive {os.path.basename(fp)}: {e}", "WARN")
        print_info(f"Archived {len(file_paths)} file(s) to {arch_dir}/")

    merged = merge_results(results, model_name)

    # Export results
    output_dir = _resolve_relative(settings.models_data_dir)
    paths = export_all_formats(
        merged, output_dir,
        model_name=merged.model_name,
        forum_source=merged.forum_source,
        mode=settings.extraction_mode,
    )

    # Register in registry
    registry = _get_registry()
    registry.register_extraction(
        model_name=merged.model_name,
        forum=merged.forum_source,
        total_urls=merged.total_urls,
        total_posts=merged.total_posts,
        file_paths=paths,
        mode=settings.extraction_mode,
    )

    print_success(f"Extraction complete: {merged.total_urls} URLs across {merged.total_posts} posts")
    print_info(f"JSON saved: {paths.get('json', 'N/A')}")
    print_info(f"All URLs TXT: {paths.get('all_urls', 'N/A')}")

    return merged


# ── Download Media Files ──────────────────────────────────────────────

def action_download_media(settings: AppSettings) -> dict:
    """Download URLs for a model."""
    registry = _get_registry()
    models = registry.get_all()

    if not models:
        print_error("No models in registry. Run extraction first.")
        return {"error": "No models"}

    choices = []
    for m in models:
        name = m.get("model_name", "unknown")
        urls = m.get("total_urls", 0)
        extracted = m.get("extracted_at", "")
        label = f"{name}  ({urls} URLs, extracted: {extracted})"
        choices.append(questionary.Choice(title=label, value=name))

    choices.append(questionary.Separator())
    choices.append(questionary.Choice(title="← Back", value=None))

    model_name = questionary.select(
        "Select model to download:",
        choices=choices,
    ).ask()

    if not model_name:
        return {"error": "Cancelled"}

    # Find the JSON file for this model
    entry = registry.get(model_name)
    if not entry:
        print_error(f"Model '{model_name}' not found in registry.")
        return {"error": "Model not found"}

    # Get latest mode's json path
    modes = entry.get("modes", {})
    json_path = ""
    for mode_name in reversed(list(modes.keys())):
        fp = modes[mode_name].get("file_paths", {}).get("json", "")
        if fp and os.path.exists(fp):
            json_path = fp
            break

    if not json_path:
        # Fallback to legacy file_paths
        json_path = entry.get("file_paths", {}).get("json", "")

    if not json_path or not os.path.exists(json_path):
        # Search for the file manually
        models_data = _resolve_relative(settings.models_data_dir)
        for root, dirs, files in os.walk(models_data):
            for f in files:
                if model_name in f and f.endswith("_posts.json"):
                    json_path = os.path.join(root, f)
                    break
            if json_path:
                break

    if not json_path or not os.path.exists(json_path):
        print_error(f"Cannot find posts JSON for '{model_name}'.")
        print_info("Run extraction first, or select import.")
        return {"error": "JSON not found"}

    output_dir = _resolve_relative(settings.output_dir)
    _ensure_dir(output_dir)

    print_info(f"Downloading: {model_name}")
    print_info(f"JSON source: {json_path}")
    print_info(f"Output: {output_dir}")

    cancel_event = threading.Event()

    download_dir = os.path.join(output_dir, model_name)
    _ensure_dir(download_dir)

    # Run with live display panel
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
        pass  # Non-critical — queue display won't show URLs but everything else works
    event_cb, file_progress_cb = panel.make_event_callback()

    _result_box = []

    def _run():
        r = run_download(
            posts_json_path=json_path,
            model_name=model_name,
            output_base=output_dir,
            max_speed_bps=settings.max_speed_mbps * 1024 * 1024 if settings.max_speed_mbps > 0 else 0,
            log_callback=log_callback,
            max_concurrent=settings.max_concurrent_downloads,
            cancel_event=cancel_event,
            progress_callback=event_cb,
            file_progress_callback=file_progress_cb,
            pixeldrain_api_key=settings.pixeldrain_api_key,
            pixeldrain_cookies_json=settings.pixeldrain_cookies_json,
            models_data_dir=str(PROJECT_DIR / settings.models_data_dir) if hasattr(settings, 'models_data_dir') else None,
        )
        _result_box.append(r)

    dl_thread = threading.Thread(target=_run, daemon=True)
    dl_thread.start()

    with panel:
        while dl_thread.is_alive():
            dl_thread.join(timeout=0.25)
            panel.refresh()

    result = _result_box[0] if _result_box else None

    if result:
        success = result.get('completed', result.get('total_files', 0))
        skipped = result.get('skipped', result.get('skipped_count', 0))
        failed = result.get('failed', result.get('failed_count', 0))
        total_bytes = result.get('total_bytes', 0)
        total_done = success + skipped

        msg = f"Download complete: {total_done} files OK"
        if skipped:
            msg += f" ({skipped} skipped)"
        print_success(msg)
        if failed:
            print_warning(f"  Failed: {failed}")
        if total_bytes:
            from core.registry import format_size
            print_info(f"  Total size: {format_size(total_bytes)}")

        # Show failed URL details
        _show_failed_urls(result)

        # Register download
        registry.register_download(
            model_name=model_name,
            total_urls=success + failed,
            total_files=success,
            total_bytes=total_bytes,
            failed_count=failed,
            skipped_count=skipped,
        )

    return result.__dict__ if hasattr(result, '__dict__') else (result or {})


# ── Sort & Dedupe ───────────────────────────────────────────────────

def action_sort(settings: AppSettings) -> dict:
    """Sort downloaded files with deduplication."""
    registry = _get_registry()
    models = registry.get_all()

    if not models:
        print_error("No models in registry. Run extraction and download first.")
        return {"error": "No models"}

    choices = []
    for m in models:
        name = m.get("model_name", "unknown")
        dl_info = m.get("download", {})
        dl_status = f"downloaded: {dl_info.get('total_files', '?')} files" if dl_info else "no download info"
        sorted_status = " [sorted]" if m.get("sorted") else ""
        label = f"{name}  ({dl_status}){sorted_status}"
        choices.append(questionary.Choice(title=label, value=name))

    choices.append(questionary.Separator())
    choices.append(questionary.Choice(title="← Back", value=None))

    model_name = questionary.select(
        "Select model to sort:",
        choices=choices,
    ).ask()

    if not model_name:
        return {"error": "Cancelled"}

    output_dir = _resolve_relative(settings.output_dir)
    sort_dir = settings.sort_dir  # This is an absolute path typically (~/Downloads)

    if not os.path.isdir(os.path.join(output_dir, model_name)):
        print_warning(f"Output folder not found: {output_dir}/{model_name}")
        other_dir = questionary.text(
            f"Enter the source folder path for '{model_name}', or leave empty to cancel:",
        ).ask()
        if other_dir is None:
            return {"error": "Cancelled"}
        if other_dir and os.path.isdir(other_dir.strip()):
            output_dir = str(Path(other_dir.strip()).parent)
            model_name = Path(other_dir.strip()).name
        else:
            print_error("Folder not found.")
            return {"error": "Folder not found"}

    action_mode = questionary.select(
        "Sort mode:",
        choices=[
            questionary.Choice("Copy — keep originals (safe, space needed)", "copy"),
            questionary.Choice("Move — delete originals (saves space)", "move"),
            questionary.Separator(),
            questionary.Choice("← Back", None),
        ],
        default="copy",
    ).ask()
    if not action_mode:
        return {"error": "Cancelled"}

    dedup = questionary.confirm(
        "Enable quality-aware deduplication?",
        default=True,
    ).ask()

    print_info(f"Sorting '{model_name}'...")

    # Progress callback
    last_pct = [0]

    def progress_cb(processed, total, fname):
        pct = int(processed / total * 100) if total else 0
        if pct >= last_pct[0] + 10 or processed == total:
            _write_action_log("sort", model_name, f"{processed}/{total} ({pct}%) — {fname}")
            last_pct[0] = pct

    stats = sort_model_folder(
        model_name=model_name,
        source_base=output_dir,
        dest_base=sort_dir,
        action=action_mode,
        dedup=dedup,
        progress_callback=progress_cb,
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
    if stats.get("already_exist"):
        print_info(f"  Already existed: {stats['already_exist']}")

    # Mark as sorted
    entry = registry.get(model_name)
    if entry:
        entry["sorted"] = True
        entry["sorted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        registry.save()

    return stats


# ── Rename ──────────────────────────────────────────────────────────

def action_rename(settings: AppSettings) -> dict:
    """Rename files in a sorted model folder."""
    sort_dir = settings.sort_dir

    if not os.path.isdir(sort_dir):
        print_error(f"Sort directory not found: {sort_dir}")
        return {"error": "Sort dir not found"}

    # Find model folders in sort dir
    models = [d for d in os.listdir(sort_dir)
              if os.path.isdir(os.path.join(sort_dir, d))]

    if not models:
        print_warning(f"No model folders found in {sort_dir}")
        return {"error": "No model folders"}

    rename_choices = [questionary.Choice(title=m, value=m) for m in models]
    rename_choices.append(questionary.Separator())
    rename_choices.append(questionary.Choice(title="← Back", value=None))

    model_name = questionary.select(
        "Select model folder to rename files in:",
        choices=rename_choices,
    ).ask()

    if not model_name:
        return {"error": "Cancelled"}

    model_path = os.path.join(sort_dir, model_name)

    # Find subfolders (images/, videos/) or files directly
    subdirs = [d for d in os.listdir(model_path)
               if os.path.isdir(os.path.join(model_path, d))]

    if not subdirs:
        # Rename files directly in model folder
        stats = _rename_in_folder(model_path, model_name)
    else:
        target_choices = [questionary.Choice(title=sd, value=sd) for sd in subdirs]
        target_choices.append(questionary.Choice(title="All files", value="__all__"))
        target_choices.append(questionary.Separator())
        target_choices.append(questionary.Choice(title="← Back", value=None))

        target = questionary.select(
            "Which folder to rename?",
            choices=target_choices,
        ).ask()

        if not target:
            return {"error": "Cancelled"}

        if target == "__all__":
            stats_total = {"renamed_count": 0, "skipped_count": 0, "errors": []}
            for sd in subdirs:
                s = _rename_in_folder(os.path.join(model_path, sd), model_name)
                stats_total["renamed_count"] += s["renamed_count"]
                stats_total["skipped_count"] += s["skipped_count"]
                stats_total["errors"].extend(s["errors"])
            stats = stats_total
        else:
            stats = _rename_in_folder(os.path.join(model_path, target), model_name)

    print_success(f"Renamed: {stats.get('renamed_count', 0)} files")
    if stats.get("skipped_count"):
        print_info(f"Skipped (already renamed): {stats['skipped_count']}")
    if stats.get("errors"):
        for err in stats["errors"]:
            print_warning(err)

    return stats


def _rename_in_folder(folder: str, base_name: str) -> dict:
    """Rename files in a single folder."""
    from core.renamer import rename_files as _rename
    print_info(f"Renaming files in: {folder}")
    print_info(f"Base name: {base_name}")

    def progress_cb(filename, action, new_name=""):
        if action == "rename":
            _write_action_log("rename", base_name, f"{filename} → {new_name}")
        elif action == "skip":
            pass  # Too noisy to log skips

    return _rename(folder, base_name, progress_callback=progress_cb)


# ── Export / Import ─────────────────────────────────────────────────

def action_export(settings: AppSettings) -> bool:
    """Export a model's scan results."""
    registry = _get_registry()
    models = registry.get_all()

    if not models:
        print_error("No models in registry.")
        return False

    choices = []
    for m in models:
        name = m.get("model_name", "unknown")
        urls = m.get("total_urls", 0)
        label = f"{name}  ({urls} URLs)"
        choices.append(questionary.Choice(title=label, value=name))

    choices.append(questionary.Separator())
    choices.append(questionary.Choice(title="← Back", value=None))

    model_name = questionary.select(
        "Select model to export:",
        choices=choices,
    ).ask()
    if not model_name:
        return False

    entry = registry.get(model_name)
    modes = entry.get("modes", {})

    # Find available paths
    all_paths = {}
    for mode_name, mode_data in modes.items():
        paths = mode_data.get("file_paths", {})
        if paths:
            all_paths[mode_name] = paths

    if not all_paths:
        print_error(f"No export files found for '{model_name}'.")
        return False

    choices = []
    for mode_name, paths in all_paths.items():
        url_file = paths.get("all_urls", "")
        json_file = paths.get("json", "")
        label = f"[{mode_name}] "
        if url_file:
            label += f"TXT ({os.path.getsize(url_file) / 1024:.0f} KB)" if os.path.exists(url_file) else "TXT"
        if json_file:
            label += f" + JSON ({os.path.getsize(json_file) / 1024:.0f} KB)" if os.path.exists(json_file) else " + JSON"
        choices.append(questionary.Choice(title=label, value=mode_name))

    mode = questionary.select(
        "Select mode to export:",
        choices=choices,
    ).ask()
    if not mode or mode not in all_paths:
        return False

    paths = all_paths[mode]
    dest = questionary.text(
        "Export destination directory (leave empty to cancel):",
        default=str(Path.home() / "Desktop"),
    ).ask()
    if not dest:
        return False
    if not dest.strip():
        return False

    _ensure_dir(dest)
    copies = []

    for key, src in paths.items():
        if src and os.path.exists(src):
            fname = os.path.basename(src)
            dst = os.path.join(dest, fname)
            import shutil
            shutil.copy2(src, dst)
            copies.append(dst)

    if copies:
        print_success(f"Exported {len(copies)} file(s) to {dest}")
        for c in copies:
            print_info(f"  {c}")
        return True
    else:
        print_error("No files found to export.")
        return False


def action_import_file(settings: AppSettings) -> ScanResult | None:
    """Import a JSON scan results file."""
    filepath = questionary.path(
        "Path to JSON file to import (Esc to cancel):",
    ).ask()

    if not filepath or not filepath.strip():
        return None

    filepath = filepath.strip()
    if not os.path.exists(filepath):
        print_error(f"File not found: {filepath}")
        return None

    if not filepath.endswith('.json'):
        print_error("File must be a .json file.")
        return None

    result = _import_json(filepath)

    if result is None:
        print_error("Failed to import JSON. File may be corrupted or invalid format.")
        return None

    # Register in registry
    registry = _get_registry()
    registry.register_extraction(
        model_name=result.model_name,
        forum=result.forum_source,
        total_urls=result.total_urls,
        total_posts=result.total_posts,
        file_paths={"imported": filepath},
        mode="imported",
    )

    print_success(f"Imported: {result.model_name}")
    print_info(f"  {result.total_urls} URLs across {result.total_posts} posts")
    return result


# ── Browse Model Folder ─────────────────────────────────────────────

def action_browse_model_folder(settings: AppSettings):
    """Browse the sorted/downloaded model folder."""
    sort_dir = settings.sort_dir
    output_dir = _resolve_relative(settings.output_dir)

    print_info(f"Sort directory: {sort_dir}")
    print_info(f"Output directory: {output_dir}")

    choice = questionary.select(
        "Browse which directory?",
        choices=[
            f"Sort Directory (sorted model folders) \u2014 {sort_dir}",
            f"Downloads Output \u2014 {output_dir}",
            questionary.Separator(),
            "← Back to menu",
        ],
    ).ask()

    if not choice or "Back" in choice:
        return

    if "Sort Directory" in choice:
        base = sort_dir
    else:
        base = output_dir

    if not os.path.isdir(base):
        print_error(f"Directory does not exist: {base}")
        return

    browse_directory(base)


def browse_directory(path: str, indent: int = 0):
    """Interactive directory browser using questionary."""
    try:
        items = sorted(os.listdir(path))
    except PermissionError:
        print_error(f"Permission denied: {path}")
        return

    dirs = [d for d in items if os.path.isdir(os.path.join(path, d))]
    files = [f for f in items if os.path.isfile(os.path.join(path, f))]

    # Build choices
    choices = []
    for d in dirs:
        dp = os.path.join(path, d)
        try:
            count = len(os.listdir(dp))
        except PermissionError:
            count = 0
        choices.append(questionary.Choice(
            title=f"📁 {d}/  ({count} items)",
            value=("dir", d),
        ))

    # Add file info
    for f in files[:30]:  # Limit to 30 files to avoid UI overload
        fp = os.path.join(path, f)
        size = os.path.getsize(fp)
        size_str = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        choices.append(questionary.Choice(
            title=f"  {f}  ({size_str})",
            value=("file", f),
        ))

    if indent == 0:
        choices.append(questionary.Choice(title="← Back to menu", value=("back", "")))
    else:
        choices.append(questionary.Choice(title="← Go up", value=("up", "")))

    selection = questionary.select(
        f"📂 {os.path.basename(path) if path != '/' else '/'}",
        choices=choices,
    ).ask()

    if not selection:
        return

    kind, name = selection
    if kind == "back":
        return
    elif kind == "up":
        parent = str(Path(path).parent)
        browse_directory(parent)
    elif kind == "dir":
        browse_directory(os.path.join(path, name))
    elif kind == "file":
        fp = os.path.join(path, name)
        print_info(f"File: {fp}")
        ext = os.path.splitext(name)[1].lower()
        if ext in (".txt", ".json", ".log", ".md"):
            from core.registry import format_size
            size = os.path.getsize(fp)
            print_info(f"Size: {format_size(size)}")
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(2000)
                print_panel(content[:1500], title=name)
            except Exception as e:
                print_error(f"Cannot read: {e}")
        else:
            print_info(f"Type: {ext or 'unknown'}")
        questionary.press_any_key_to_continue("Press any key to continue...").ask()
        browse_directory(path)


# ── Model Registry View ─────────────────────────────────────────────

def action_view_registry(settings: AppSettings):
    """View the model registry."""
    registry = _get_registry()
    models = registry.get_all()

    if not models:
        print_info("No models registered yet.")
        return

    rows = []
    for m in models:
        name = m.get("model_name", "?")
        forum = m.get("forum", "")
        urls = m.get("total_urls", 0)
        posts = m.get("total_posts", 0)
        extracted = m.get("extracted_at", "")
        sorted_flag = "✓" if m.get("sorted") else "✗"
        dl = m.get("download", {})
        dl_status = f"{dl.get('total_files', 0)} files" if dl else "—"
        rows.append([name, forum, str(urls), str(posts), extracted, sorted_flag, dl_status])

    print_table(
        "Model Registry",
        ["Model", "Forum", "URLs", "Posts", "Extracted", "Sorted", "Downloaded"],
        rows,
    )
    print_info(f"Total: {len(models)} model(s)")
    questionary.press_any_key_to_continue("Press any key to continue...").ask()


# ── Load JSON file directly ─────────────────────────────────────────

def action_select_json_file(settings: AppSettings) -> ScanResult | None:
    """Select an existing JSON file from models_data directory."""
    models_data = _resolve_relative(settings.models_data_dir)
    if not os.path.isdir(models_data):
        print_warning(f"Models data directory not found: {models_data}")
        return None

    json_files = []
    for root, dirs, files in os.walk(models_data):
        for f in files:
            if f.endswith("_posts.json"):
                json_files.append(os.path.join(root, f))

    if not json_files:
        print_warning(f"No JSON files found in {models_data}")
        return None

    choices = []
    for jf in sorted(json_files):
        rel = os.path.relpath(jf, models_data)
        result = _import_json(jf)
        if result:
            label = f"{rel}  ({result.model_name}, {result.total_urls} URLs)"
        else:
            label = f"{rel}  (could not parse)"
        choices.append(questionary.Choice(title=label, value=jf))

    selected = questionary.select(
        "Select a JSON file to load:",
        choices=choices + [questionary.Choice(title="← Back", value=None)],
    ).ask()

    if not selected:
        return None

    result = _import_json(selected)
    if result:
        print_success(f"Loaded: {result.model_name} — {result.total_urls} URLs")
        return result
    else:
        print_error("Failed to load JSON file.")
        return None
