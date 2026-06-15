"""JSON import/export for scan results.

Export: Save scan results as shareable JSON.
Import: Load a JSON file and reconstruct ScanResult objects.
"""

import json
import os

from .models import ScanResult, Post


def export_json(result: ScanResult, filepath: str, mode: str = "normal") -> bool:
    """Export a ScanResult to a JSON file. Returns True on success."""
    try:
        data = result.to_dict()
        data['mode'] = mode
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except (OSError, IOError) as e:
        print(f"Export error: {e}")
        return False


def export_all_formats(result: ScanResult, output_dir: str, model_name: str = "",
                       forum_source: str = "", mode: str = "normal") -> dict:
    """
    Export scan results in three formats:

    1. {model}_all_urls.txt — all URLs flat (always produced)
    2. {model}_posts.json — structured JSON with per-post data (normal mode only)
    3. posts/ directory — one .txt per post: Page{X}-Post{Y}.txt (normal mode only)

    For no_filter and reverse modes, only the flat TXT file is produced
    with a mode suffix (e.g. rincosplay_all_urls_no_filter.txt).

    Returns:
        dict with paths created and URL counts.
    """
    if not model_name:
        model_name = result.model_name
    if not forum_source:
        forum_source = result.forum_source

    # Determine mode suffix for filename
    mode_suffix = f"_{mode}" if mode != "normal" else ""

    # Create output subdirectory
    base = os.path.join(output_dir, f"{forum_source}_{model_name}" if forum_source else model_name)
    os.makedirs(base, exist_ok=True)

    # All URLs flat file (always produced)
    all_urls_path = os.path.join(base, f"{model_name}_all_urls{mode_suffix}.txt")
    all_urls = []
    for post in result.posts:
        all_urls.extend(post.urls)
    with open(all_urls_path, 'w', encoding='utf-8') as f:
        for url in sorted(set(all_urls)):
            f.write(url + '\n')

    paths = {
        "all_urls": all_urls_path,
        "json": "",
        "posts_dir": "",
        "total_urls": len(set(all_urls)),
        "total_posts": len(result.posts),
    }

    # Full export — JSON and per-post files for all modes
    posts_dir = os.path.join(base, "posts")
    os.makedirs(posts_dir, exist_ok=True)

    json_path = os.path.join(base, f"{model_name}_posts{mode_suffix}.json")
    export_json(result, json_path, mode=mode)
    paths["json"] = json_path

    for post in result.posts:
        post_filename = f"Page{post.page}-Post{post.post_index}.txt"
        post_path = os.path.join(posts_dir, post_filename)
        with open(post_path, 'w', encoding='utf-8') as f:
            if post.author:
                f.write(f"# Author: {post.author}\n")
            f.write(f"# Post ID: {post.post_id}\n")
            f.write(f"# Page: {post.page}, Index: {post.post_index}\n")
            f.write(f"# Source: {post.source_file}\n\n")
            for url in post.urls:
                f.write(url + '\n')
    paths["posts_dir"] = posts_dir

    return paths


def import_json(filepath: str) -> ScanResult | None:
    """Import a JSON file and return a ScanResult. Returns None on failure."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return ScanResult.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"Import error: {e}")
        return None
