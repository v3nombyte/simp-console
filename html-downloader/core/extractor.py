"""Per-post URL extraction from forum HTML files.

Supports SimpCity, SocialMediaGirls, and CelebForum formats.
Extracts URLs grouped by individual forum post.
"""

import os
import re
import json
import base64
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

from .models import Post, ScanResult


# ── Config ──────────────────────────────────────────────────────────

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS_PATH = os.path.join(PROJECT_DIR, "url_patterns.json")
SKIP_PATH = os.path.join(PROJECT_DIR, "skip_url.json")

# Default patterns to match if file doesn't exist
DEFAULT_PATTERNS = [
    "bunkr\\.cr", "bunkr\\.ru", "bunkr\\.ac", "bunkr\\.ci",
    "bunkr\\.fi", "bunkr\\.media", "bunkr\\.black", "bunkr\\.ph",
    "bunkr\\.pk", "bunkr\\.red", "bunkr\\.si", "bunkr\\.site",
    "bunkr\\.sk", "bunkr\\.ws", "bunkrrr\\.org", "bunkr\\.ax",
    "bunkr\\.bz", "bunkr\\.cat", "bunkrr\\.su",
    "cyberdrop\\.me", "cyberfile\\.me", "cyberfile\\.su",
    "cyberdrop\\.cr", "cyberfiles-static\\.b-cdn\\.net",
    "pixl\\.li", "jpg4\\.su", "jpg5\\.su", "jpg6\\.su", "jpg7\\.cr",
    "jpg\\.church",
    "pixeldrain\\.com",
    "saint2\\.su",
    "selti-delivery\\.ru",
    "simp[1-6]\\.cuckcapital\\.cr/",
    "simpcity\\.cr/attachments/",
    "celebforum\\.to/attachments/",
    "forums\\.socialmediagirls\\.com/attachments",
    "smgmedia\\.socialmediagirls\\.com",
    "anonfiles\\.com",
    "gofile\\.io",
    "goonbox\\.cr",
    "imgbox\\.com",
    "giphy\\.com",
    "mega\\.nz",
    "mega\\.co\\.nz",
    "host\\\\.church",
    "media\\.redgifs\\.com",
    "media\\.imagepond\\.net/media/",
    "www\\.imagebam\\.com/image",
    "1fichier\\.com",
    "drive\\.google\\.com/drive/folders",
    "e-hentai\\.org/g/",
    "cdn\\.camwhores\\.tv/contents/",
    "gotanynudes\\.com/",
    "i\\.gyazo\\.com",
    "i\\.imgur\\.com",
    "ibb\\.co/",
    "turbo\\.cr/",
    "redgifs\\.com/",
]

DEFAULT_SKIP = [
    "jpg6\\.su/upload",
    "jpg6\\.su/sdk/pup-sc",
    "coomer\\.party",
    "gofile\\.io/dist/img",
    "bunkr\\.fi/images/fav",
    "fav\\.ico",
    "favicon",
    "preview",
    "thumbnail",
    "thumb_",
    "simp.*selti-delivery\\.ru/simpo/data/avatars",
    "simp[1-6]\\.cuckcapital\\.cr/simpo/data/avatars/",
    "pixeldrain\\.com/res/img",
    "celebforum\\.to/data/assets/",
    "celebforum\\.to/data/attachments/",
    "celebforum\\.to/threads",
    "forums\\.socialmediagirls\\.com/threads",
    "x\\.com/",
    "reddit\\.com/",
    "instagram\\.com/",
    "cover\\.png",
    "\\.md(?:\\.|$|[?#])",
]


# ── Helpers ─────────────────────────────────────────────────────────

def load_json(path, default):
    """Load a JSON file or return default."""
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def extract_page_number(filename: str) -> int:
    """Extract page number from filename like '... Page 2 ...' or '... Page 10 ...'."""
    m = re.search(r'[Pp]age\s*(\d+)', filename)
    if m:
        return int(m.group(1))
    return 1


def extract_model_name(soup: BeautifulSoup, filename: str) -> str:
    """Extract the model name from the forum title, falling back to filename."""
    heading = soup.select_one("h1.p-title-value")
    if heading:
        for label in heading.select("a.labelLink, span.label-append"):
            label.decompose()
        title = re.split(r"[/,|]", heading.get_text(" ", strip=True), maxsplit=1)[0]
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title

    name_match = re.search(
        r'(.+?)\s*[_|]\s*(?:Page\s*\d+|SimpCity|Social Media)', filename
    )
    if name_match:
        return name_match.group(1).strip()
    return filename.split("(")[-1].split(")")[-1].strip() if "(" in filename else filename


def identify_forum_source(soup: BeautifulSoup) -> str:
    """Identify the forum source from HTML metadata."""
    try:
        og = soup.find('meta', property='og:url')
        if og and og.get('content') and 'celebforum.to' in og['content']:
            return 'celebforum'
        title_tag = soup.find('title')
        if title_tag and 'Social Media Girls' in title_tag.text:
            return 'socialmediagirls'
        icon = soup.find('link', rel='icon')
        if icon and icon.get('href') and 'simpcity' in icon['href']:
            return 'simpcity'
    except Exception:
        pass
    return ''


def get_base_url(soup: BeautifulSoup) -> str:
    """Get base URL from og:url meta tag."""
    try:
        og = soup.find('meta', property='og:url')
        if og and og.get('content'):
            return og['content']
    except Exception:
        pass
    return ''


def _thread_post_number(post_article) -> int | None:
    for link in post_article.select(
        ".message-attribution-opposite a[href*='/post-']"
    ):
        match = re.fullmatch(r"\s*#(\d+)\s*", link.get_text(" ", strip=True))
        if match:
            return int(match.group(1))
    return None


# ── URL Extraction ──────────────────────────────────────────────────

def extract_post_media(post_article, base_url: str) -> list[dict[str, str]]:
    """
    Extract distinct media references in their document order.
    """
    for sig in post_article.select(".message-signature"):
        sig.decompose()

    attrs = {
        "a": "href",
        "img": "src",
        "script": "src",
        "link": "href",
        "iframe": "src",
        "source": "src",
        "video": "src",
    }
    ordered: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    simp_images_with_goonbox_links: set[str] = set()
    fallback_urls_by_primary: dict[str, list[str]] = {}
    last_resort_urls_by_primary: dict[str, list[str]] = {}
    style_pattern = re.compile(r'url\(["\']?(.*?)["\']?\)')

    def is_simp_md_variant(raw_url: str) -> bool:
        image_url = urlparse(urljoin(base_url, raw_url))
        return bool(
            image_url.hostname
            and re.fullmatch(
                r"simp[1-6]\.cuckcapital\.cr", image_url.hostname.lower()
            )
            and re.search(r"\.md(?=\.[^./]+$)", image_url.path, re.IGNORECASE)
        )

    def clean_simp_cdn_url(raw_url: str) -> str:
        image_url = urlparse(urljoin(base_url, raw_url))
        if (
            image_url.hostname
            and re.fullmatch(
                r"simp[1-6]\.cuckcapital\.cr", image_url.hostname.lower()
            )
        ):
            image_url = image_url._replace(
                path=re.sub(
                    r"\.md(?=\.[^./]+$)",
                    "",
                    image_url.path,
                    flags=re.IGNORECASE,
                )
            )
        return image_url.geturl()

    def add(raw_url: str, kind: str, is_attachment: bool = False) -> None:
        raw_url = raw_url.strip()
        if not raw_url:
            return
        url = urljoin(base_url, raw_url) if base_url else raw_url
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        url = clean_simp_cdn_url(url)
        if url in seen:
            return
        parsed_url = urlparse(url)
        if parsed_url.hostname and parsed_url.hostname.lower() == "i.ibb.co":
            image_id = parsed_url.path.strip("/").split("/", 1)[0]
            if re.fullmatch(r"[A-Za-z0-9]+", image_id):
                url = f"https://ibb.co/{image_id}"
                if url in seen:
                    return
        seen.add(url)
        ordered.append((url, kind, is_attachment))

    def is_simp_cdn_image(raw_url: str) -> bool:
        image_url = urlparse(urljoin(base_url, raw_url))
        return bool(
            image_url.hostname
            and re.fullmatch(
                r"simp[1-6]\.cuckcapital\.cr", image_url.hostname.lower()
            )
            and os.path.splitext(image_url.path)[1].lower()
            in {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
        )

    for element in post_article.find_all(True):
        tag = element.name.lower()
        attachment_item = element.find_parent("li", class_="file")
        is_attachment = bool(
            attachment_item
            and attachment_item.find_parent("ul", class_="attachmentList")
        )
        attr = attrs.get(tag)
        if attr:
            candidates = (
                element.get(attr),
                element.get("data-url"),
                element.get("data-src"),
            )
            value = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate
                    and urljoin(base_url, candidate).startswith(("http://", "https://"))
                ),
                None,
            )
            if tag == "a" and value:
                link_url = urlparse(urljoin(base_url, value))
                is_goonbox_image = (
                    link_url.hostname is not None
                    and link_url.hostname.lower() in {"goonbox.cr", "www.goonbox.cr"}
                    and link_url.path.startswith("/img/")
                )
                if is_goonbox_image:
                    for image in element.select("img[src], img[data-url], img[data-src]"):
                        image_url = next(
                            (
                                candidate
                                for candidate in (
                                    image.get("src"),
                                    image.get("data-url"),
                                    image.get("data-src"),
                                )
                                if candidate
                                and urljoin(base_url, candidate).startswith(
                                    ("http://", "https://")
                                )
                            ),
                            None,
                        )
                        if image_url and is_simp_cdn_image(image_url):
                            simp_url = clean_simp_cdn_url(image_url)
                            simp_images_with_goonbox_links.add(simp_url)
                            is_last_resort = is_simp_md_variant(image_url)
                            target_urls = (
                                last_resort_urls_by_primary
                                if is_last_resort
                                else fallback_urls_by_primary
                            )
                            fallback_urls = target_urls.setdefault(
                                urljoin(base_url, value), []
                            )
                            if simp_url not in fallback_urls:
                                fallback_urls.append(simp_url)
            paired_simp_image = (
                tag == "img"
                and value is not None
                and clean_simp_cdn_url(value) in simp_images_with_goonbox_links
            )
            has_attachment_image = (
                tag == "a"
                and is_attachment
                and element.select_one("img[src], img[data-src]") is not None
            )
            if value and not has_attachment_image and not paired_simp_image:
                add(
                    value,
                    "image" if tag == "img" else "video" if tag in {"video", "source"} else "media",
                    is_attachment and tag == "img",
                )

        onclick = element.get("onclick", "")
        for match in re.finditer(r"""['"](//[^'"]+)['"]""", onclick):
            add(match.group(1), "media")

        srcset = element.get("srcset")
        if srcset:
            for part in srcset.split(","):
                add(part.strip().split(" ")[0], "image" if tag == "img" else "media")

        style = element.get("style")
        if style:
            for match in style_pattern.findall(style):
                add(match, "image")

    image_exts = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
    video_exts = {".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
    resolved: list[dict[str, str]] = []
    resolved_seen: set[str] = set()
    for url, kind, is_attachment in ordered:
        parsed = urlparse(url)
        if ("simpcity.cr" in parsed.netloc or "simpcity.su" in parsed.netloc) and "/redirect/" in parsed.path:
            qs = parse_qs(parsed.query)
            to_b64 = qs.get("to", [""])[0]
            if to_b64:
                try:
                    # Add padding for base64 decode
                    padded = to_b64 + "=" * ((4 - len(to_b64) % 4) % 4)
                    decoded = base64.b64decode(padded).decode("utf-8", errors="replace")
                    if decoded.startswith("http"):
                        url = decoded
                        parsed = urlparse(url)
                except Exception:
                    pass
            if "simpcity.cr" in parsed.netloc:
                continue
        if "cdn.jsdelivr.net" in parsed.netloc:
            continue
        if parsed.netloc == "simpcity.cr" and parsed.path.startswith("/members/"):
            continue
        if parsed.netloc == "www.instagram.com":
            continue
        if parsed.netloc in ("x.com", "www.x.com", "www.reddit.com", "reddit.com"):
            continue
        if parsed.netloc in ("t.me", "www.tiktok.com", "tiktok.com"):
            continue
        if url in resolved_seen:
            continue
        resolved_seen.add(url)
        extension = os.path.splitext(parsed.path)[1].lower()
        if extension in image_exts:
            kind = "image"
        elif extension in video_exts:
            kind = "video"
        item = {"url": url, "kind": kind}
        fallback_urls = fallback_urls_by_primary.get(url)
        if fallback_urls:
            item["fallback_urls"] = fallback_urls
        last_resort_urls = last_resort_urls_by_primary.get(url)
        if last_resort_urls:
            item["last_resort_urls"] = last_resort_urls
        if is_attachment:
            item["attachment"] = "true"
        resolved.append(item)

    return resolved


def extract_post_urls(post_article, base_url: str) -> list[str]:
    """Extract distinct media URLs from a single post in document order."""
    return [item["url"] for item in extract_post_media(post_article, base_url)]


def filter_urls(
    urls: list[str],
    patterns: list[str],
    skip_patterns: list[str],
    force_include: set[str] | None = None,
) -> list[str]:
    """
    Filter URLs: keep those matching any pattern, skip those matching skip patterns.
    """
    result = []
    for url in urls:
        # Check skip patterns first
        skipped = False
        for sp in skip_patterns:
            if re.search(sp, url, re.IGNORECASE):
                skipped = True
                break
        if skipped:
            continue
        # Check include patterns (any match = keep)
        matched = url in force_include if force_include else False
        if not matched:
            for p in patterns:
                if re.search(p, url, re.IGNORECASE):
                    matched = True
                    break
        if matched:
            result.append(url)
    return result


# ── Main Extraction ────────────────────────────────────────────────

def extract_from_html(
    html_path: str,
    patterns: list[str] | None = None,
    skip_patterns: list[str] | None = None,
    mode: str = "normal",
) -> ScanResult:
    """
    Extract posts and URLs from a single HTML file.

    Args:
        html_path: Path to the HTML file.
        patterns: URL inclusion patterns (regex).
        skip_patterns: URL skip patterns (regex).
        mode: "normal" (apply both), "no_filter" (all URLs), "reverse" (NOT matching patterns).

    Returns:
        ScanResult with posts.
    """
    if patterns is None:
        patterns = load_json(PATTERNS_PATH, DEFAULT_PATTERNS)
    if skip_patterns is None:
        skip_patterns = load_json(SKIP_PATH, DEFAULT_SKIP)

    filename = os.path.basename(html_path)
    page_num = extract_page_number(filename)

    with open(html_path, 'r', encoding='utf-8', errors='replace') as f:
        soup = BeautifulSoup(f, 'lxml')

    forum_source = identify_forum_source(soup)
    base_url = get_base_url(soup)

    model_name = extract_model_name(soup, filename)

    result = ScanResult(
        model_name=model_name,
        forum_source=forum_source,
    )

    # Find all posts
    post_articles = soup.select("article.message-body")
    if not post_articles:
        # Fallback: try broader selector
        post_articles = soup.select("[class*='message'] [class*='body']")

    for idx, article in enumerate(post_articles, start=1):
        post_text = article.get_text("\n", strip=True)
        post_id = article.get("id", "") or ""
        # Get post ID from parent article
        parent = article.find_parent("article", class_="message")
        if parent:
            post_id = parent.get("id", "")
        author = article.get("data-author", "") or parent.get("data-author", "") if parent else ""
        if not author:
            author_el = article.find_previous("a", class_="username")
            if author_el:
                author = author_el.text.strip()

        media_items = extract_post_media(parent or article, base_url)
        urls = [item["url"] for item in media_items]

        if mode == "normal":
            attachment_urls = {
                item["url"]
                for item in media_items
                if item.get("attachment") == "true"
            }
            selected_urls = set(
                filter_urls(urls, patterns, skip_patterns, attachment_urls)
            )
        elif mode == "reverse":
            selected_urls = {
                url for url in urls
                if not any(re.search(pattern, url) for pattern in patterns)
            }
        else:
            selected_urls = set(urls)
        selected_media = []
        for index, item in enumerate(
            (item for item in media_items if item["url"] in selected_urls),
            start=1,
        ):
            selected_item = {
                "url": item["url"],
                "kind": item["kind"],
                "index": index,
            }
            if item.get("fallback_urls"):
                selected_item["fallback_urls"] = item["fallback_urls"]
            if item.get("last_resort_urls"):
                selected_item["last_resort_urls"] = item["last_resort_urls"]
            if item.get("attachment") == "true":
                selected_item["attachment"] = "true"
            selected_media.append(selected_item)
        selected_urls_ordered = [item["url"] for item in selected_media]

        post = Post(
            post_id=post_id or f"post-{page_num}-{idx}",
            page=page_num,
            post_index=idx,
            thread_post_number=_thread_post_number(parent or article),
            author=author,
            urls=selected_urls_ordered,
            media_items=selected_media,
            post_text=post_text,
            source_file=filename,
        )
        result.posts.append(post)

    result.total_posts = len(result.posts)
    result.total_urls = sum(len(p.urls) for p in result.posts)

    return result


def extract_from_folder(
    folder_path: str,
    patterns: list[str] | None = None,
    skip_patterns: list[str] | None = None,
    mode: str = "normal",
) -> list[ScanResult]:
    """
    Extract from all HTML files in a folder (flat, no recursion into subfolders).

    Returns a list of ScanResult objects (one per file).
    """
    results = []
    for fname in sorted(os.listdir(folder_path)):
        if fname.endswith('.html'):
            fpath = os.path.join(folder_path, fname)
            try:
                sr = extract_from_html(fpath, patterns, skip_patterns, mode)
                results.append(sr)
            except Exception as e:
                print(f"  [ERROR] Failed to process '{fname}': {e}")
    return results


def merge_results(results: list[ScanResult], model_name: str = "") -> ScanResult:
    """Merge multiple ScanResults into one, deduplicating URLs globally."""
    if not results:
        return ScanResult(model_name=model_name or "unknown", forum_source="")

    merged = ScanResult(
        model_name=model_name or results[0].model_name,
        forum_source=results[0].forum_source,
    )
    seen_urls: set[str] = set()
    for r in results:
        for post in r.posts:
            # Remove URLs already seen in earlier posts (e.g. quoted content)
            unique_urls = [u for u in post.urls if u not in seen_urls]
            seen_urls.update(unique_urls)
            merged.posts.append(Post(
                post_id=post.post_id,
                page=post.page,
                post_index=post.post_index,
                thread_post_number=post.thread_post_number,
                author=post.author,
                urls=unique_urls,
                source_file=post.source_file,
            ))
    merged.total_posts = len(merged.posts)
    merged.total_urls = sum(len(p.urls) for p in merged.posts)
    return merged
