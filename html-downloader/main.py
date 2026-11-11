#!/usr/bin/env python3
"""Rich interactive CLI for saved-HTML media extraction and downloads."""

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import questionary
from questionary import Choice
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from core.downloader import _host_output_folder, run_download
from core.extractor import extract_from_html


APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "settings.json"
HTML_SUFFIXES = {".html", ".htm"}
FAILURE_LOG_DIVIDER = "-" * 45
console = Console()
DEFAULT_SETTINGS = {
    "input_dir": str(APP_DIR / "input"),
    "models_data_dir": str(APP_DIR / "models_data"),
    "output_dir": str(APP_DIR / "output"),
    "max_concurrent": 3,
    "speed_limit_mbps": 0.0,
}
MENU_STYLE = questionary.Style([
    ("qmark", "fg:#00d7ff bold"),
    ("question", "bold"),
    ("answer", "fg:#00d787 bold"),
    ("pointer", "fg:#00d7ff bold"),
    ("highlighted", "fg:#00d7ff bold"),
    ("selected", "fg:#00d787"),
    ("separator", "fg:#666666"),
    ("instruction", "fg:#888888"),
    ("text", ""),
    ("disabled", "fg:#666666 italic"),
])


def _select_option(prompt: str, choices: list[Choice]) -> Any:
    return questionary.select(
        prompt,
        choices=choices,
        pointer="›",
        qmark="",
        instruction="Use the arrow keys and Enter",
        style=MENU_STYLE,
    ).ask()


def _html_files(inputs: list[str]) -> list[Path]:
    files: set[Path] = set()
    for item in inputs:
        path = Path(item).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Input does not exist: {path}")
        if path.is_file():
            if path.suffix.lower() not in HTML_SUFFIXES:
                raise ValueError(f"Expected an .html or .htm file: {path}")
            files.add(path)
        elif path.is_dir():
            files.update(
                child.resolve()
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in HTML_SUFFIXES
            )
        else:
            raise ValueError(f"Input is not a file or directory: {path}")
    return sorted(files)


def _safe_model_name(name: str, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name.upper() in {"CON", "PRN", "AUX", "NUL"}:
        name = fallback
    return name[:100]


def _load_settings() -> dict[str, Any]:
    settings = DEFAULT_SETTINGS.copy()
    if not SETTINGS_PATH.exists():
        return settings
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        console.print(f"[yellow]Could not load {SETTINGS_PATH}: {error}[/yellow]")
        return settings
    if not isinstance(data, dict):
        console.print(f"[yellow]Invalid settings format in {SETTINGS_PATH}; defaults loaded.[/yellow]")
        return settings

    for key in ("input_dir", "models_data_dir", "output_dir"):
        value = data.get(key, settings[key])
        if isinstance(value, str) and value.strip():
            settings[key] = value
    concurrent = data.get("max_concurrent", settings["max_concurrent"])
    if isinstance(concurrent, int) and not isinstance(concurrent, bool) and concurrent > 0:
        settings["max_concurrent"] = concurrent
    speed = data.get("speed_limit_mbps", settings["speed_limit_mbps"])
    if isinstance(speed, (int, float)) and math.isfinite(speed) and speed >= 0:
        settings["speed_limit_mbps"] = float(speed)
    return settings


def _save_settings(settings: dict[str, Any]) -> None:
    temporary_path = SETTINGS_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(SETTINGS_PATH)


def _resolve_setting_path(settings: dict[str, Any], key: str) -> Path:
    path = Path(settings[key]).expanduser()
    if not path.is_absolute():
        path = APP_DIR / path
    return path.resolve()


def _clear_terminal() -> None:
    console.file.write("\x1b[3J\x1b[2J\x1b[H")
    console.file.flush()


def _show_menu_header(section: str = "", clear: bool = True) -> None:
    if clear:
        _clear_terminal()
    subtitle = "Import saved pages, extract links, and download media"
    if section:
        subtitle = f"{section} | {subtitle}"
    console.print(
        Panel(
            "[bold bright_cyan]HTML MEDIA DOWNLOADER[/bold bright_cyan]\n"
            f"[dim]{subtitle}[/dim]",
            border_style="bright_cyan",
            box=box.DOUBLE,
        )
    )


def _posts_from_html(html_path: Path) -> tuple[str, dict[str, Any], int]:
    scan = extract_from_html(str(html_path), mode="normal")
    posts = [
        {
            "page": post.page,
            "post_index": post.post_index,
            "thread_post_number": post.thread_post_number,
            "urls": post.urls,
            "media_items": post.media_items or [
                {"url": url, "kind": "media", "index": index}
                for index, url in enumerate(post.urls, start=1)
            ],
            "post_text": post.post_text,
            "post_id": post.post_id,
            "author": post.author,
        }
        for post in scan.posts
    ]

    model_name = _safe_model_name(scan.model_name, html_path.stem)
    payload = {
        "mode": "normal",
        "model_name": model_name,
        "forum_source": scan.forum_source,
        "posts": posts,
    }
    return model_name, payload, sum(len(post["urls"]) for post in posts)


def _remove_excluded_media_urls(payload: dict[str, Any]) -> None:
    for post in payload.get("posts", []):
        if not isinstance(post, dict):
            continue
        urls = post.get("urls", [])
        if not isinstance(urls, list):
            continue
        media_items = post.get("media_items")
        if not isinstance(media_items, list):
            media_items = []
        item_by_url = {
            item.get("url"): item
            for item in media_items
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        }
        normalized_urls: list[Any] = []
        normalized_items: list[Any] = []
        seen_urls: set[str] = set()
        previous_item: dict[str, Any] | None = None
        for index, original_url in enumerate(urls, start=1):
            if not isinstance(original_url, str):
                continue

            parsed = urlparse(original_url)
            host = (parsed.hostname or "").lower()
            is_simp_cdn = bool(
                re.fullmatch(r"simp[1-6]\.cuckcapital\.cr", host)
            )
            is_md_variant = bool(
                is_simp_cdn
                and re.search(r"\.md(?=\.[^./]+$)", parsed.path, re.IGNORECASE)
            )
            normalized_url = original_url
            if is_simp_cdn:
                clean_path = re.sub(
                    r"\.md(?=\.[^./]+$)",
                    "",
                    parsed.path,
                    flags=re.IGNORECASE,
                )
                normalized_url = parsed._replace(path=clean_path).geturl()

            if re.search(
                r"simp[1-6]\.cuckcapital\.cr/simpo/data/avatars/",
                normalized_url,
                re.IGNORECASE,
            ):
                continue
            if re.search(r"\.md(?:\.|$|[?#])", normalized_url, re.IGNORECASE):
                continue

            if (
                is_simp_cdn
                and previous_item is not None
                and isinstance(previous_item.get("url"), str)
            ):
                previous_url = urlparse(previous_item["url"])
                if (
                    (previous_url.hostname or "").lower()
                    in {"goonbox.cr", "www.goonbox.cr"}
                    and previous_url.path.startswith("/img/")
                ):
                    target_key = (
                        "last_resort_urls" if is_md_variant else "fallback_urls"
                    )
                    fallback_urls = previous_item.setdefault(target_key, [])
                    other_tier = (
                        previous_item.get(
                            "fallback_urls"
                            if target_key == "last_resort_urls"
                            else "last_resort_urls",
                            [],
                        )
                    )
                    if normalized_url not in fallback_urls and normalized_url not in other_tier:
                        fallback_urls.append(normalized_url)
                    continue

            if normalized_url in seen_urls:
                continue
            item = item_by_url.get(original_url)
            if isinstance(item, dict):
                item["url"] = normalized_url
            else:
                item = {"url": normalized_url, "kind": "media", "index": index}
            seen_urls.add(normalized_url)
            normalized_urls.append(normalized_url)
            normalized_items.append(item)
            previous_item = item

        post["urls"] = normalized_urls
        post["media_items"] = normalized_items


def _post_folder(output_dir: Path, model_name: str, post: dict[str, Any]) -> Path:
    def positive_index(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    page_number = positive_index(post.get("page"))
    post_number = positive_index(
        post.get("thread_post_number") or post.get("post_index")
    )
    return (
        output_dir
        / model_name
        / f"Page {page_number:03d}"
        / f"Post {post_number:03d}"
    )


def _post_key(post: dict[str, Any]) -> tuple[int, int]:
    try:
        page = int(post.get("page", 0))
    except (TypeError, ValueError):
        page = 0
    try:
        post_index = int(
            post.get("thread_post_number") or post.get("post_index", 0)
        )
    except (TypeError, ValueError):
        post_index = 0
    return page, post_index


def _failure_content_with_post_text(content: str, post: dict[str, Any]) -> str:
    post_text = str(post.get("post_text", "")).strip()
    if post_text:
        return f"{content}\n\n{post_text}"
    return content


def _failure_host_label(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    service_names = (
        ("bunkr", "Bunkr"),
        ("cyberdrop", "CyberDrop"),
        ("cyberfile", "CyberFile"),
        ("pixeldrain", "PixelDrain"),
        ("gofile", "GoFile"),
        ("redgifs", "RedGifs"),
        ("goonbox", "GoonBox"),
        ("turbo", "Turbo"),
        ("mega", "Mega"),
        ("erome", "Erome"),
        ("imagebam", "ImageBam"),
        ("imagepond", "ImagePond"),
        ("pixhost", "Pixhost"),
        ("imgbox", "Imgbox"),
        ("imgur", "Imgur"),
        ("gyazo", "Gyazo"),
    )
    return next(
        (name for domain, name in service_names if domain in host),
        host or "Unknown host",
    )


def _is_attachment_media(item: dict[str, Any]) -> bool:
    if item.get("attachment") == "true":
        return True
    path = urlparse(str(item.get("url", ""))).path.lower()
    return re.search(r"attachments?", path) is not None


def _failure_reason_label(reason: str) -> str:
    reason = reason.strip()
    if reason.startswith("HTTP "):
        return reason.split(":", 1)[0]
    return reason or "Unknown error"


def _write_post_failure_logs(
    payload: dict[str, Any],
    stats: dict[str, Any],
    output_dir: Path,
    model_name: str,
) -> int:
    no_link_posts = 0
    failures_by_post: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for entry in stats.get("failed_urls", []):
        if not isinstance(entry, (tuple, list)) or len(entry) < 6:
            continue
        url, _model, _mode, page, post_index, reason = entry[:6]
        try:
            key = (int(page), int(post_index))
        except (TypeError, ValueError):
            continue
        failures_by_post.setdefault(key, []).append((str(url), str(reason)))

    for post in payload.get("posts", []):
        if not isinstance(post, dict):
            continue
        folder = _post_folder(output_dir, model_name, post)
        urls = post.get("urls", [])
        if not isinstance(urls, list):
            urls = []
        folder.mkdir(parents=True, exist_ok=True)
        failure_entries = failures_by_post.get(_post_key(post), [])

        if not urls:
            no_link_posts += 1
            content = _failure_content_with_post_text(
                "Download failed: no links found", post
            )
            for old_fail_path in folder.rglob("fails.txt"):
                old_fail_path.unlink()
            (folder / "fails.txt").write_text(content + "\n", encoding="utf-8")
            continue

        media_items = post.get("media_items", [])
        if not isinstance(media_items, list) or not media_items:
            media_items = [
                {"url": url, "kind": "media", "index": index}
                for index, url in enumerate(urls, start=1)
            ]
        media_by_url = {
            str(item.get("url")): item
            for item in media_items
            if isinstance(item, dict) and item.get("url") is not None
        }
        failed_media_urls = {
            url for url, _reason in failure_entries if url in media_by_url
        }
        unmapped_failure_urls = {
            url for url, _reason in failure_entries if url not in media_by_url
        }
        all_media_failed = (
            bool(media_by_url)
            and len(failed_media_urls) >= len(media_by_url)
            and not unmapped_failure_urls
        )
        all_failed_from_attachment_list = (
            all_media_failed
            and all(
                item.get("attachment") == "true"
                for item in media_by_url.values()
            )
        )
        failures_by_folder: dict[Path, list[tuple[str, str]]] = {}
        for url, reason in failure_entries:
            failure_folder = stats.get("failure_output_folders", {}).get(
                (url, *_post_key(post))
            )
            target = Path(failure_folder) if failure_folder else Path(
                _host_output_folder(
                    str(output_dir),
                    model_name,
                    *_post_key(post),
                    url,
                )
            )
            failures_by_folder.setdefault(target, []).append((url, reason))

        current_fail_paths: set[Path] = set()
        for target_folder, target_failures in failures_by_folder.items():
            target_folder.mkdir(parents=True, exist_ok=True)
            fail_path = target_folder / "fails.txt"
            current_fail_paths.add(fail_path)
            failures_by_host: dict[str, list[tuple[str, str]]] = {}
            attachment_failures: dict[str, set[str]] = {}
            for url, reason in target_failures:
                item = media_by_url.get(url, {})
                if _is_attachment_media(item):
                    attachment_failures.setdefault(
                        _failure_reason_label(reason), set()
                    ).add(url)
                else:
                    failures_by_host.setdefault(
                        _failure_host_label(url), []
                    ).append((url, reason))

            summary_lines = []
            for reason, failed_urls in sorted(attachment_failures.items()):
                count = len(failed_urls)
                noun = "attachment" if count == 1 else "attachments"
                summary_lines.append(
                    f"{count} {noun} failed to download [{reason}]"
                )

            for host, host_failures in sorted(failures_by_host.items()):
                media_labels = []
                source_counts: dict[str, set[str]] = {}
                for url, _reason in host_failures:
                    item = media_by_url.get(url, {})
                    kind = str(item.get("kind", "media")).lower()
                    if kind not in {"image", "video"}:
                        kind = "media"
                    source_counts.setdefault(kind, set()).add(url)
                    try:
                        media_index = int(item.get("index", 0))
                    except (TypeError, ValueError):
                        media_index = 0
                    if media_index > 0:
                        media_labels.append(f"{kind} {media_index}")

                if host in {"GoonBox", "Turbo"} and all_media_failed:
                    for kind, failed_urls in sorted(source_counts.items()):
                        plural_nouns = {
                            "image": "images",
                            "video": "videos",
                            "media": "media files",
                        }
                        noun = (
                            kind
                            if len(failed_urls) == 1
                            else plural_nouns.get(kind, f"{kind}s")
                        )
                        summary_lines.append(
                            f"Source: {host} - {len(failed_urls)} {noun}"
                        )
                elif media_labels:
                    summary_lines.append(
                        f"Source: {host} - {', '.join(sorted(set(media_labels)))}"
                        if host in {"GoonBox", "Turbo"}
                        else (
                            f"{host} download failed: "
                            f"{', '.join(sorted(set(media_labels)))}"
                        )
                    )
                else:
                    summary_lines.append(
                        f"{host} download failed: {len(host_failures)} media item(s)"
                    )

            if all_media_failed and all_failed_from_attachment_list:
                reason_labels = sorted(
                    {
                        _failure_reason_label(reason)
                        for _url, reason in target_failures
                    }
                )
                reason_suffix = (
                    f" [{'; '.join(reason_labels)}]" if reason_labels else ""
                )
                content = (
                    f"All {len(media_items)} media failed to download "
                    f"from attachment list{reason_suffix}."
                )
            else:
                content = "\n\n".join(summary_lines)
                if all_media_failed:
                    content = (
                        f"All {len(media_items)} media failed to download.\n\n"
                        f"{content}"
                    )
                else:
                    content = f"Failed to download\n\n{content}"

            if all_media_failed:
                post_text = str(post.get("post_text", "")).strip()
                if post_text:
                    content += (
                        f"\n{FAILURE_LOG_DIVIDER}\n\n"
                        f"{post_text}\n\n{FAILURE_LOG_DIVIDER}"
                    )

            detailed_failures = sorted(set(target_failures))
            if detailed_failures:
                detail_section = (
                    "Expanded error log\n"
                    f"{FAILURE_LOG_DIVIDER}\n"
                    + "\n\n".join(
                        f"URL: {url}\nError: {reason}"
                        for url, reason in detailed_failures
                    )
                    + f"\n\n{FAILURE_LOG_DIVIDER}"
                )
                content += (
                    f"\n\n{detail_section}"
                    if all_media_failed
                    else f"\n\n{FAILURE_LOG_DIVIDER}\n\n{detail_section}"
                )
            fail_path.write_text(content + "\n", encoding="utf-8")

        for old_fail_path in folder.rglob("fails.txt"):
            if old_fail_path not in current_fail_paths:
                old_fail_path.unlink()
    return no_link_posts


def _progress_callback(
    progress: Progress,
    resolve_task: int,
    download_task: int,
    url_count: int,
):
    def update(event: str, **details: Any) -> None:
        if event == "resolving":
            total = details.get("total", url_count)
            progress.update(resolve_task, total=total, completed=0)
            progress.update(download_task, total=total)
        elif event == "resolve_progress":
            progress.update(
                resolve_task,
                completed=details.get("resolved", 0),
                total=details.get("total", url_count),
            )
        elif event == "file":
            progress.update(
                download_task,
                completed=details.get("overall_completed", 0),
                total=details.get("overall_total", url_count),
            )
        elif event == "status":
            console.log(f"[cyan]{details.get('message', 'Working...')}[/cyan]")
        elif event == "complete":
            progress.update(
                download_task,
                completed=details.get("overall_completed", 0),
                total=details.get("overall_total", url_count),
            )
    return update


def _run_download(
    payload: dict[str, Any],
    model_name: str,
    output_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> dict[str, Any]:
    _remove_excluded_media_urls(payload)
    url_count = sum(len(post.get("urls", [])) for post in payload.get("posts", []))
    with tempfile.TemporaryDirectory(prefix="html-downloader-") as temp_dir:
        posts_json = Path(temp_dir) / "posts.json"
        posts_json.write_text(json.dumps(payload), encoding="utf-8")

        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:
            resolve_task = progress.add_task("Resolving links", total=url_count)
            download_task = progress.add_task("Downloading files", total=url_count)

            def on_log(message: str, level: str = "INFO") -> None:
                styles = {"INFO": "dim", "WARN": "yellow", "ERROR": "bold red"}
                console.log(Text.from_ansi(message), style=styles.get(level, "dim"))

            stats = run_download(
                str(posts_json),
                model_name,
                output_base=str(output_dir),
                max_speed_bps=max_speed_bps,
                progress_callback=_progress_callback(
                    progress, resolve_task, download_task, url_count
                ),
                log_callback=on_log,
                max_concurrent=max_concurrent,
                pixeldrain_api_key=os.environ.get("PIXELDRAIN_APIKEY", ""),
                pixeldrain_cookies_json=os.environ.get("PIXELDRAIN_COOKIES", ""),
                models_data_dir=temp_dir,
            )
    return stats


def _show_download_summary(
    title: str,
    stats: dict[str, Any],
    url_count: int,
    no_link_posts: int = 0,
) -> None:
    failed_count = stats["failed"] + no_link_posts
    status_style = "green" if failed_count == 0 else "yellow"
    console.print(
        Panel(
            f"[green]Downloaded[/green]  {stats['completed']}    "
            f"[yellow]Already present[/yellow]  {stats['skipped']}    "
            f"[red]Failed[/red]  {failed_count}\n"
            f"[dim]Links processed: {url_count}[/dim]",
            title=f"[{status_style}]{title}[/{status_style}]",
            border_style=status_style,
            box=box.ROUNDED,
        )
    )
def _extract_only(
    html_path: Path,
    models_data_dir: Path,
) -> tuple[Path | None, int]:
    model_name, payload, url_count = _posts_from_html(html_path)
    if not payload["posts"]:
        console.print(f"[yellow]No forum posts found in {html_path.name}.[/yellow]")
        return None, 0

    model_dir = models_data_dir / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    base_path = model_dir / f"{html_path.stem}.json"
    target_path = _next_available_path(base_path)

    with target_path.open("x", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, ensure_ascii=False)
    console.print(
        Panel(
            f"Extracted [bold green]{url_count}[/bold green] media links.\n"
            f"Saved JSON: [cyan]{target_path}[/cyan]",
            title="[green]Extraction complete[/green]",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    return target_path, url_count


def _download_html(
    html_path: Path,
    output_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> tuple[int, int]:
    console.print(
        Panel(
            f"[bold]{html_path.name}[/bold]\n"
            "[dim]Extracting media links from saved HTML[/dim]",
            title="[cyan]Processing saved page[/cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    model_name, payload, url_count = _posts_from_html(html_path)
    if not url_count:
        no_link_posts = _write_post_failure_logs(
            payload, {}, output_dir, model_name
        )
        console.print(
            f"[yellow]No supported media links found. Created fails.txt "
            f"for {no_link_posts} post(s).[/yellow]"
        )
        return no_link_posts, 0

    stats = _run_download(
        payload, model_name, output_dir, max_concurrent, max_speed_bps
    )
    no_link_posts = _write_post_failure_logs(
        payload, stats, output_dir, model_name
    )
    _show_download_summary(
        f"Page complete: {html_path.name}", stats, url_count, no_link_posts
    )
    return stats["failed"] + no_link_posts, url_count


def _download_json(
    json_path: Path,
    output_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> tuple[int, int, bool]:
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        console.print(
            Panel(
                str(error),
                title="[bold red]Could not read JSON[/bold red]",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        return 1, 0, False

    if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
        console.print(
            Panel(
                "Expected a posts JSON file containing a 'posts' list.",
                title="[bold red]Invalid JSON format[/bold red]",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        return 1, 0, False

    model_name = _safe_model_name(
        str(payload.get("model_name", json_path.stem)), json_path.stem
    )
    _remove_excluded_media_urls(payload)
    url_count = sum(
        len(post.get("urls", []))
        for post in payload["posts"]
        if isinstance(post, dict) and isinstance(post.get("urls", []), list)
    )
    if not url_count:
        no_link_posts = _write_post_failure_logs(
            payload, {}, output_dir, model_name
        )
        console.print(
            f"[yellow]This JSON contains no media URLs. Created fails.txt "
            f"for {no_link_posts} post(s).[/yellow]"
        )
        return no_link_posts, 0, True

    console.print(
        Panel(
            f"[bold]{json_path.name}[/bold]\n"
            f"[dim]Model: {model_name} · media links: {url_count}[/dim]",
            title="[cyan]Downloading from JSON[/cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    stats = _run_download(
        payload, model_name, output_dir, max_concurrent, max_speed_bps
    )
    no_link_posts = _write_post_failure_logs(
        payload, stats, output_dir, model_name
    )
    _show_download_summary(
        "JSON download complete", stats, url_count, no_link_posts
    )
    return stats["failed"] + no_link_posts, url_count, stats["failed"] == 0


def _next_available_path(path: Path) -> Path:
    target = path
    suffix = 2
    while target.exists():
        target = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        suffix += 1
    return target


def _archive_processed_json(
    json_path: Path,
    processed_data_dir: Path,
    model_name: str | None = None,
    subfolder: str | None = None,
) -> Path:
    if model_name is None:
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        model_name = _safe_model_name(
            str(payload.get("model_name", json_path.stem))
            if isinstance(payload, dict)
            else json_path.stem,
            json_path.stem,
        )
    else:
        model_name = _safe_model_name(model_name, json_path.stem)

    model_dir = processed_data_dir / model_name
    if subfolder:
        model_dir /= subfolder
    model_dir.mkdir(parents=True, exist_ok=True)
    target = _next_available_path(model_dir / json_path.name)
    return Path(shutil.move(str(json_path), str(target)))


def _move_processed_input(
    html_path: Path,
    processed_input_dir: Path,
    model_name: str,
) -> Path:
    model_dir = processed_input_dir / _safe_model_name(model_name, html_path.stem)
    if html_path.resolve().parent == model_dir.resolve():
        return html_path
    model_dir.mkdir(parents=True, exist_ok=True)
    target = _next_available_path(model_dir / html_path.name)
    return Path(shutil.move(str(html_path), str(target)))


def _model_queues(
    models_data_dir: Path,
) -> list[tuple[str, Path | None, list[Path]]]:
    model_files: dict[str, list[Path]] = {}
    model_dirs: dict[str, Path] = {}

    if models_data_dir.is_dir():
        for model_dir in sorted(
            path for path in models_data_dir.iterdir() if path.is_dir()
        ):
            files = _json_files(model_dir)
            if files:
                model_files.setdefault(model_dir.name, []).extend(files)
                model_dirs[model_dir.name] = model_dir

    queues = [
        (name, model_dirs[name], sorted(files))
        for name, files in sorted(model_files.items())
    ]

    loose_files = (
        sorted(path for path in models_data_dir.glob("*.json") if path.is_file())
        if models_data_dir.is_dir()
        else []
    )
    if loose_files:
        queues.append(("Unsorted legacy JSON", None, loose_files))
    return queues


def _process_model_queue(
    model_name: str,
    model_dir: Path | None,
    json_files: list[Path],
    output_dir: Path,
    processed_data_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> None:
    completed = 0
    retry_count = 0
    total_urls = 0

    for json_path in json_files:
        try:
            failed, urls, download_succeeded = _download_json(
                json_path, output_dir, max_concurrent, max_speed_bps
            )
        except OSError as error:
            console.print(f"[red]Could not process {json_path}: {error}[/red]")
            failed, urls, download_succeeded = 1, 0, False

        total_urls += urls
        if not download_succeeded:
            retry_dir = processed_data_dir / (
                model_dir.name if model_dir else model_name
            ) / "retries"
            if retry_dir in json_path.parents:
                retry_count += 1
                console.print(
                    f"[yellow]Keeping {json_path.name} in the retry queue "
                    f"because {failed} operation(s) failed.[/yellow]"
                )
                continue
            try:
                archived = _archive_processed_json(
                    json_path,
                    processed_data_dir,
                    model_dir.name if model_dir else None,
                    "retries",
                )
            except (OSError, json.JSONDecodeError, AttributeError) as error:
                console.print(
                    f"[red]Could not move failed scan to its retry queue "
                    f"{json_path}: {error}[/red]"
                )
                retry_count += 1
                continue
            retry_count += 1
            console.print(f"[yellow]Moved failed scan to retry queue: {archived}[/yellow]")
            continue

        try:
            archived = _archive_processed_json(
                json_path,
                processed_data_dir,
                model_dir.name if model_dir else None,
            )
        except (OSError, json.JSONDecodeError, AttributeError) as error:
            console.print(
                f"[red]Downloads finished, but could not archive "
                f"{json_path}: {error}[/red]"
            )
            retry_count += 1
            continue
        completed += 1
        console.print(f"[green]Archived completed scan to {archived}[/green]")

    console.print(
        f"\n[bold cyan]Model queue complete: {model_name}[/bold cyan]\n"
        f"Scans archived: {completed} · In retry queue: {retry_count} · "
        f"Media URLs: {total_urls}"
    )


def _ask_path(message: str, default: Path) -> str | None:
    return questionary.text(
        message,
        default=str(default),
        style=MENU_STYLE,
    ).ask()


def _process_html_inputs(
    source: str,
    extract_only: bool,
    output_dir: Path,
    models_data_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> None:
    try:
        html_paths = _html_files([source])
    except (FileNotFoundError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        return
    if not html_paths:
        console.print("[yellow]No .html or .htm files found at that path.[/yellow]")
        return

    total_urls = 0
    total_failed = 0
    for html_path in html_paths:
        try:
            if extract_only:
                saved_json, urls = _extract_only(html_path, models_data_dir)
                total_urls += urls
                if saved_json is not None:
                    try:
                        moved_html = _move_processed_input(
                            html_path,
                            APP_DIR / "Processed_input",
                            saved_json.parent.name,
                        )
                    except OSError as error:
                        console.print(
                            f"[red]Extracted JSON to {saved_json}, but could "
                            f"not move source HTML {html_path}: {error}[/red]"
                        )
                        total_failed += 1
                    else:
                        if moved_html == html_path:
                            console.print(
                                f"[dim]Processed HTML is already in "
                                f"{moved_html.parent}.[/dim]"
                            )
                        else:
                            console.print(
                                f"[green]Moved processed HTML to {moved_html}[/green]"
                            )
            else:
                failed, urls = _download_html(
                    html_path, output_dir, max_concurrent, max_speed_bps
                )
                total_failed += failed
                total_urls += urls
        except OSError as error:
            console.print(f"[red]Could not process {html_path}: {error}[/red]")
            total_failed += 1

    console.print(
        f"\n[bold cyan]Processed {len(html_paths)} HTML file(s); "
        f"{total_urls} media URL(s) found.[/bold cyan]"
    )
    if total_failed:
        console.print(f"[red]{total_failed} download or file operation(s) failed.[/red]")


def _crawling_menu() -> None:
    while True:
        _show_menu_header("Crawling")
        choice = _select_option(
            "Crawling",
            choices=[
                Choice(
                    "Crawl SimpCity thread  [not available yet]",
                    value="crawl",
                    disabled="Crawling is not included in this standalone version",
                ),
                Choice("Back", value="back"),
            ],
        )
        if choice is None or choice == "back":
            _clear_terminal()
            return


def _json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path.resolve()
        for path in directory.rglob("*.json")
        if path.is_file()
    )


def _choose_json_file(directory: Path, prompt: str) -> Path | None:
    files = _json_files(directory)
    if not files:
        console.print(f"[yellow]No JSON files found in {directory}.[/yellow]")
        return None

    choices = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            model_name = data.get("model_name", path.stem) if isinstance(data, dict) else path.stem
            posts = data.get("posts", []) if isinstance(data, dict) else []
            urls = sum(
                len(post.get("urls", []))
                for post in posts
                if isinstance(post, dict) and isinstance(post.get("urls", []), list)
            ) if isinstance(posts, list) else 0
            label = f"{path.relative_to(directory)}  ({model_name}, {urls} URLs)"
        except (OSError, json.JSONDecodeError):
            label = f"{path.relative_to(directory)}  (invalid JSON)"
        choices.append(Choice(label, value=path))
    choices.append(Choice("Back", value=None))
    _show_menu_header(prompt)
    selected = _select_option(
        prompt,
        choices=choices,
    )
    if selected is None:
        _clear_terminal()
    return Path(selected) if selected else None


def _show_json_scan(json_path: Path) -> None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        console.print(f"[red]Could not read {json_path}: {error}[/red]")
        return
    if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
        console.print("[red]This file is not a supported posts JSON file.[/red]")
        return

    posts = data["posts"]
    urls = sum(
        len(post.get("urls", []))
        for post in posts
        if isinstance(post, dict) and isinstance(post.get("urls", []), list)
    )
    info = Table.grid(padding=(0, 2))
    info.add_column(style="bold cyan", justify="right")
    info.add_column()
    info.add_row("File", str(json_path))
    info.add_row("Model", str(data.get("model_name", json_path.stem)))
    info.add_row("Forum", str(data.get("forum_source", "unknown")))
    info.add_row("Posts", str(len(posts)))
    info.add_row("Media URLs", str(urls))
    console.print(Panel(info, title="[cyan]Saved scan results[/cyan]", box=box.ROUNDED))


def _import_json_scan(source: Path, models_data_dir: Path) -> None:
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        console.print(f"[red]Could not import JSON: {error}[/red]")
        return
    if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
        console.print("[red]Expected a posts JSON file containing a 'posts' list.[/red]")
        return

    models_data_dir.mkdir(parents=True, exist_ok=True)
    existing_target = models_data_dir / source.name
    if source.resolve() == existing_target.resolve():
        console.print(f"[cyan]This file is already in {models_data_dir}.[/cyan]")
        return
    target = existing_target
    suffix = 2
    while target.exists():
        target = models_data_dir / f"{source.stem}_{suffix}{source.suffix}"
        suffix += 1
    try:
        shutil.copy2(source, target)
    except OSError as error:
        console.print(f"[red]Could not import JSON to {target}: {error}[/red]")
        return
    console.print(f"[green]Imported JSON scan to {target}[/green]")


def _export_json_scan(source: Path) -> None:
    destination_text = _ask_path(
        "Destination folder",
        Path.home() / "Downloads",
    )
    if not destination_text:
        return
    destination = Path(destination_text).expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        console.print(f"[red]Destination is not a folder: {destination}[/red]")
        return
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    suffix = 2
    while target.exists():
        target = destination / f"{source.stem}_{suffix}{source.suffix}"
        suffix += 1
    try:
        shutil.copy2(source, target)
    except OSError as error:
        console.print(f"[red]Could not export JSON to {target}: {error}[/red]")
        return
    console.print(f"[green]Exported JSON scan to {target}[/green]")


def _browse_app_folders(
    input_dir: Path,
    models_data_dir: Path,
    output_dir: Path,
    processed_data_dir: Path,
) -> None:
    table = Table(title="Application folders", box=box.ROUNDED)
    table.add_column("Area", style="cyan")
    table.add_column("Path")
    table.add_column("Items", justify="right")
    for label, path in (
        ("Saved HTML input", input_dir),
            ("Processed HTML input", APP_DIR / "Processed_input"),
            ("Pending model scans", models_data_dir),
        ("Processed scan data", processed_data_dir),
        ("Downloaded media", output_dir),
    ):
        try:
            count = sum(1 for child in path.iterdir()) if path.is_dir() else 0
            item_count = str(count)
        except OSError as error:
            item_count = f"[red]unavailable: {error}[/red]"
        table.add_row(label, str(path), item_count)
    console.print(table)


def _extraction_menu(
    input_dir: Path,
    models_data_dir: Path,
    output_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> None:
    while True:
        _show_menu_header("Extraction")
        choice = _select_option(
            "Extraction and downloads",
            choices=[
                Choice(
                    "Process Input: extract saved HTML into model queues",
                    value="extract",
                ),
                Choice("Select JSON file and view scan results", value="select_json"),
                Choice("Import JSON file from disk", value="import_json"),
                Choice("Back", value="back"),
            ],
        )

        if choice is None or choice == "back":
            _clear_terminal()
            return
        if choice == "extract":
            source = _ask_path("HTML file or folder to extract", input_dir)
            if source:
                _process_html_inputs(
                    source,
                    True,
                    output_dir,
                    models_data_dir,
                    max_concurrent,
                    max_speed_bps,
                )
        elif choice == "select_json":
            json_path = _choose_json_file(models_data_dir, "Select a saved scan")
            if json_path:
                _show_json_scan(json_path)
        elif choice == "import_json":
            source_text = _ask_path("JSON file to import", models_data_dir)
            if source_text:
                _import_json_scan(
                    Path(source_text).expanduser().resolve(), models_data_dir
                )


def _processing_menu(
    models_data_dir: Path,
    processed_data_dir: Path,
    output_dir: Path,
    max_concurrent: int,
    max_speed_bps: int,
) -> None:
    preserve_output = False
    while True:
        _show_menu_header("Processing", clear=not preserve_output)
        preserve_output = False
        choice = _select_option(
            "Processing",
            choices=[
                Choice("Process all scans for a model", value="process_model"),
                Choice("Download one JSON scan", value="download"),
                Choice(
                    "Sort and deduplicate files  [not available yet]",
                    value="sort",
                    disabled="Sorting is not included in this standalone version",
                ),
                Choice(
                    "Rename files  [not available yet]",
                    value="rename",
                    disabled="Renaming is not included in this standalone version",
                ),
                Choice("Back", value="back"),
            ],
        )
        if choice is None or choice == "back":
            _clear_terminal()
            return
        if choice == "process_model":
            queues = _model_queues(models_data_dir)
            if not queues:
                console.print(
                    f"[yellow]No pending scans found in "
                    f"{models_data_dir}.[/yellow]"
                )
                continue
            choices = [
                Choice(
                    f"{name} ({len(files)} queued "
                    f"{'scan' if len(files) == 1 else 'scans'})",
                    value=index,
                )
                for index, (name, _directory, files) in enumerate(queues)
            ]
            choices.append(Choice("Back", value=None))
            _show_menu_header("Select model queue")
            selected = _select_option("Model to process", choices=choices)
            if selected is None:
                _clear_terminal()
                continue
            name, directory, files = queues[selected]
            _process_model_queue(
                name,
                directory,
                files,
                output_dir,
                processed_data_dir,
                max_concurrent,
                max_speed_bps,
            )
            preserve_output = True
        if choice == "download":
            json_path = _choose_json_file(models_data_dir, "Select scan to download")
            if json_path:
                _download_json(
                    json_path, output_dir, max_concurrent, max_speed_bps
                )
                preserve_output = True


def _management_menu(
    input_dir: Path,
    models_data_dir: Path,
    output_dir: Path,
    processed_data_dir: Path,
) -> None:
    while True:
        _show_menu_header("Management")
        choice = _select_option(
            "Management",
            choices=[
                Choice("View model registry  [not available yet]", value="registry",
                       disabled="This standalone version has no model registry"),
                Choice("Browse application folders", value="browse"),
                Choice("View saved scan results", value="view_json"),
                Choice("Export scan results", value="export_json"),
                Choice("Back", value="back"),
            ],
        )
        if choice is None or choice == "back":
            _clear_terminal()
            return
        if choice == "browse":
            _browse_app_folders(
                input_dir, models_data_dir, output_dir, processed_data_dir
            )
        elif choice == "view_json":
            json_path = _choose_json_file(models_data_dir, "Select scan results")
            if json_path:
                _show_json_scan(json_path)
        elif choice == "export_json":
            json_path = _choose_json_file(models_data_dir, "Select scan to export")
            if json_path:
                _export_json_scan(json_path)


def _settings_menu(settings: dict[str, Any]) -> None:
    path_labels = {
        "input_dir": "Saved HTML input folder",
        "models_data_dir": "Scan data folder",
        "output_dir": "Media download folder",
    }
    while True:
        _show_menu_header("Settings")
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", justify="right")
        table.add_column()
        table.add_row("HTML input", settings["input_dir"])
        table.add_row("Scan data", settings["models_data_dir"])
        table.add_row("Downloads", settings["output_dir"])
        table.add_row("Concurrent downloads", str(settings["max_concurrent"]))
        speed = settings["speed_limit_mbps"]
        table.add_row("Speed limit", f"{speed:g} MB/s" if speed else "Unlimited")
        table.add_row(
            "PixelDrain credentials",
            "Configured in environment" if os.environ.get("PIXELDRAIN_APIKEY")
            or os.environ.get("PIXELDRAIN_COOKIES") else "Not configured",
        )
        console.print(Panel(table, title="[cyan]Current settings[/cyan]", box=box.ROUNDED))

        choices = [
            Choice(f"{label}: {settings[key]}", value=key)
            for key, label in path_labels.items()
        ]
        choices.extend([
            Choice(
                f"Maximum concurrent downloads: {settings['max_concurrent']}",
                value="max_concurrent",
            ),
            Choice(
                f"Speed limit (MB/s): {settings['speed_limit_mbps']:g}",
                value="speed_limit_mbps",
            ),
            Choice("Back", value="back"),
        ])
        choice = _select_option(
            "Settings",
            choices=choices,
        )
        if choice is None or choice == "back":
            _clear_terminal()
            return

        if choice in path_labels:
            value = _ask_path(f"Set {path_labels[choice]}", Path(settings[choice]))
            if value and value.strip():
                selected_path = Path(value.strip()).expanduser()
                if selected_path.exists() and not selected_path.is_dir():
                    console.print("[yellow]That path exists and is not a folder.[/yellow]")
                else:
                    settings[choice] = value.strip()
        elif choice == "max_concurrent":
            value = questionary.text(
                "Maximum simultaneous downloads (1-20)",
                default=str(settings["max_concurrent"]),
                style=MENU_STYLE,
            ).ask()
            try:
                parsed = int(value) if value is not None else 0
            except ValueError:
                parsed = 0
            if 1 <= parsed <= 20:
                settings["max_concurrent"] = parsed
            elif value:
                console.print("[yellow]Enter a whole number from 1 to 20.[/yellow]")
        elif choice == "speed_limit_mbps":
            value = questionary.text(
                "Download speed limit in MB/s (0 = unlimited)",
                default=f"{settings['speed_limit_mbps']:g}",
                style=MENU_STYLE,
            ).ask()
            try:
                parsed_speed = float(value) if value is not None else -1
            except ValueError:
                parsed_speed = -1
            if math.isfinite(parsed_speed) and parsed_speed >= 0:
                settings["speed_limit_mbps"] = parsed_speed
            elif value:
                console.print("[yellow]Enter a finite number of 0 or greater.[/yellow]")
        try:
            _save_settings(settings)
        except OSError as error:
            console.print(f"[red]Could not save settings: {error}[/red]")


def _system_menu() -> bool:
    while True:
        _show_menu_header("System")
        choice = _select_option(
            "System",
            choices=[
                Choice("About this downloader", value="about"),
                Choice("Open application folder", value="open_folder"),
                Choice("Check for updates  [not available yet]", value="updates",
                       disabled="Standalone updates are not configured"),
                Choice("Exit", value="exit"),
                Choice("Back", value="back"),
            ],
        )
        if choice is None or choice == "back":
            _clear_terminal()
            return False
        if choice == "exit":
            return True
        if choice == "about":
            console.print(
                Panel(
                    f"HTML Media Downloader\nPython {sys.version.split()[0]}\n"
                    f"Application folder: {APP_DIR}",
                    title="[cyan]About[/cyan]",
                    border_style="cyan",
                    box=box.ROUNDED,
                )
            )
        elif choice == "open_folder":
            try:
                os.startfile(str(APP_DIR))
            except (AttributeError, OSError) as error:
                console.print(f"[red]Could not open application folder: {error}[/red]")


def _run_menu(settings: dict[str, Any]) -> int:
    input_dir = _resolve_setting_path(settings, "input_dir")
    models_data_dir = _resolve_setting_path(settings, "models_data_dir")
    processed_data_dir = APP_DIR / "Processed_data"
    output_dir = _resolve_setting_path(settings, "output_dir")
    max_concurrent = settings["max_concurrent"]
    max_speed_bps = int(settings["speed_limit_mbps"] * 1_000_000)
    input_dir.mkdir(parents=True, exist_ok=True)
    models_data_dir.mkdir(parents=True, exist_ok=True)
    processed_data_dir.mkdir(parents=True, exist_ok=True)
    (APP_DIR / "Processed_input").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    while True:
        _show_menu_header()
        choice = _select_option(
            "Main menu",
            choices=[
                Choice(
                    "Start full workflow  (crawl/import > download > sort > rename)  [not available yet]",
                    value="workflow",
                    disabled="Crawling, sorting, and renaming are not included yet",
                ),
                Choice("Crawling", value="crawling"),
                Choice("Extraction", value="extraction"),
                Choice("Processing", value="processing"),
                Choice("Management", value="management"),
                Choice("Settings", value="settings"),
                Choice("System", value="system"),
                Choice("Exit", value="exit"),
            ],
        )

        if choice is None or choice == "exit":
            console.print("[dim]Closing program.[/dim]")
            return 0
        if choice == "crawling":
            _crawling_menu()
        elif choice == "extraction":
            _extraction_menu(
                input_dir,
                models_data_dir,
                output_dir,
                max_concurrent,
                max_speed_bps,
            )
        elif choice == "processing":
            _processing_menu(
                models_data_dir,
                processed_data_dir,
                output_dir,
                max_concurrent,
                max_speed_bps,
            )
        elif choice == "management":
            _management_menu(
                input_dir, models_data_dir, output_dir, processed_data_dir
            )
        elif choice == "settings":
            _settings_menu(settings)
            input_dir = _resolve_setting_path(settings, "input_dir")
            models_data_dir = _resolve_setting_path(settings, "models_data_dir")
            output_dir = _resolve_setting_path(settings, "output_dir")
            max_concurrent = settings["max_concurrent"]
            max_speed_bps = int(settings["speed_limit_mbps"] * 1_000_000)
        elif choice == "system":
            if _system_menu():
                console.print("[dim]Closing program.[/dim]")
                return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract supported media links from saved HTML and download them."
    )
    parser.add_argument(
        "html",
        nargs="*",
        help="Optional HTML file(s) or folder(s). Without paths, opens the menu.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Download destination (default: saved setting).",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=None,
        help="Maximum simultaneous downloads (default: saved setting).",
    )
    parser.add_argument(
        "--speed-limit-mbps",
        type=float,
        default=None,
        help="Per-download speed limit in MB/s; 0 means unlimited.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    settings = _load_settings()
    if args.output is not None:
        settings["output_dir"] = str(args.output.expanduser())
    if args.max_concurrent is not None:
        settings["max_concurrent"] = args.max_concurrent
    if args.speed_limit_mbps is not None:
        settings["speed_limit_mbps"] = args.speed_limit_mbps

    if settings["max_concurrent"] < 1:
        parser.error("--max-concurrent must be at least 1")
    if (not math.isfinite(settings["speed_limit_mbps"])
            or settings["speed_limit_mbps"] < 0):
        parser.error("--speed-limit-mbps must be a finite, non-negative number")

    output_dir = _resolve_setting_path(settings, "output_dir")
    max_speed_bps = int(settings["speed_limit_mbps"] * 1_000_000)
    if not args.html:
        try:
            return _run_menu(settings)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Closing program.[/dim]")
            return 0

    try:
        html_paths = _html_files(args.html)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    if not html_paths:
        console.print("[yellow]No .html or .htm files found.[/yellow]")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    max_concurrent = settings["max_concurrent"]
    total_failed = 0
    total_urls = 0
    for html_path in html_paths:
        failed, urls = _download_html(
            html_path, output_dir, max_concurrent, max_speed_bps
        )
        total_failed += failed
        total_urls += urls
    console.print(
        f"[bold cyan]Processed {len(html_paths)} HTML file(s); "
        f"{total_urls} media URL(s) found.[/bold cyan]"
    )
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
