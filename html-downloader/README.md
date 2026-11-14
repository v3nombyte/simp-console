# HTML Downloader

A separate, standalone tool for extracting and downloading media links from saved HTML pages. Its interactive CLI uses nested, colored keyboard menus with a chevron selector, Rich status panels, URL-resolution and download progress bars, and per-run summaries. The menu includes Extraction, Processing, Management, Settings, and System sections. Crawling, full workflow, sorting, renaming, registry, and update actions are shown as unavailable in this standalone version. The host-resolution code and URL extraction are copied into this folder so this tool can run independently without changing the original Simp Console files.

## Setup on Windows

From this folder, create an environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Playwright is only needed for hosts whose pages require browser-based JavaScript resolution. Direct links and hosts handled through HTTP or APIs can work without a browser. If you skip the Playwright install, those browser-dependent hosts may fail while other downloads continue.

GoonBox image links are resolved through its image API before download. GoonBox album links are expanded through its paginated album API, and downloaded images are placed in the album folder.

Transient request or stream interruptions are retried for up to three attempts. Incomplete files are removed before retrying. Failed media details, including the URL and specific resolver or download error when available, are written to `fails.txt` beside the affected host's downloads. For expanded albums, the error also identifies the individual media page and resolved media URL (without signed query tokens). Failures for media stored directly in the post folder (such as embedded media and Simp CDN links) remain in the post-root `fails.txt`. Collection failures are logged in the actual collection folder, including its album-named subfolder.

When only some GoonBox or Turbo media fail, the failure summary lists their individual image/video positions (for example, `Source: GoonBox - image 3, image 5`). If all media in the post fail, totals remain grouped by source and media type instead. SimpCity CDN image URLs on `simp1` through `simp6.cuckcapital.cr` are recognized as direct media links.

## Use

Run with no arguments to open the interactive menu. Available actions include:

- **Process Input:** extract one or more saved HTML pages into `models_data/<model>/`. Each JSON keeps the source HTML filename, with its extension changed to `.json`. After JSON extraction succeeds, the source HTML moves to `Processed_input/<model>/`; files with no posts remain in the input location.
- **Process Data:** select a model queue and download its pending JSON scans. Successful scans move into `Processed_data/<model>/`; scans with failed downloads move to `Processed_data/<model>/retries/` and are not included in the processing queue. To retry one, move its JSON back into `models_data/<model>/`; already-downloaded media is skipped.
- **Extraction:** inspect saved scan JSON or import a posts JSON file into the pending scan-data folder.
- **Processing:** download one selected JSON scan, or process an entire model queue. Sorting and renaming are shown but disabled because they are not implemented in this standalone copy.
- **Management:** browse the app folders, inspect saved scan JSON, or export a scan JSON file.
- **Settings:** configure HTML input, scan-data and output folders, concurrent downloads, and speed limit. Settings are saved in `settings.json`.
- **System:** view app information or open the application folder.

Existing files are not overwritten when extracting, importing, exporting, moving HTML, or archiving JSON; a numeric suffix is added when needed. JSON files previously saved directly in `models_data` remain available as a legacy queue.

Put saved `.html` or `.htm` pages in `input`, or pass a file/folder explicitly to download directly:

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe main.py "C:\path\to\saved-page.html"
.\.venv\Scripts\python.exe main.py "C:\path\to\saved-pages" --output "D:\Media"
```

Folders are searched recursively. Each saved page is read in document order, and every forum post keeps its original position, even when it contains no supported media links. Extracted links use the existing URL patterns and skip patterns in `url_patterns.json` and `skip_url.json`. For legacy `attachmentList` entries, the image URL in the nested `<img src>` is used instead of the surrounding forum attachment-page link; these contextual attachment images still respect skip patterns.

Simp CDN image URLs ending in `.md.<extension>` are cleaned by removing the `.md` variant suffix; the original `.md` URL is never downloaded. Simp CDN avatar URLs under `/simpo/data/avatars/` are skipped during extraction and filtered from existing scan JSON before download. For a GoonBox `/img/` link paired with a Simp image, the download order is GoonBox first, then an already-clean Simp URL if present, and finally a cleaned URL derived from the `.md` variant.

ImgBB page URLs on `ibb.co` and thumbnail URLs on `i.ibb.co` are included in the extraction allowlist. Thumbnail URLs are converted to their ImgBB page URL using the first path segment as the image ID (for example, `https://i.ibb.co/ID/file.jpg` becomes `https://ibb.co/ID`) before downloading. Lazy-image attributes are checked when the normal `src` is not an HTTP URL.

Downloads are grouped by model, saved page, and the forum's displayed post number when available. For example, a post labeled `#39` on Page 2 is saved in `Page 002/Post 039/`. The page-local position is retained in scan data, and is used as the folder-number fallback when the saved HTML has no recognizable forum post number:

```text
output/
└── Model/
    └── Page 001/
        ├── Post 001/
        │   └── fails.txt
        └── Post 002/
            ├── Bunkr/
            │   └── Album name/
            │       ├── image.jpg
            │       └── fails.txt
            ├── PixelDrain/
            │   └── List title/
            │       ├── video.mp4
            │       └── fails.txt
            └── fails.txt
```

Each post gets its own folder, with downloaded files grouped by host. Collection links get an additional album-named folder when a title is available (for example, `Bunkr/<album name>` or `PixelDrain/<list title>`); GoonBox album names are derived from the URL slug with the trailing ID removed. On Windows, unusually long media filenames are shortened with a stable hash suffix to keep the full output path below the system path limit; the extension is preserved. Host-folder failures are logged in that host's folder, including a collection's album-named folder. Embedded Turbo `/embed/` videos, GoonBox `/img/` images, and hosts beginning with `simp` are placed directly in the post folder, and their failures are logged in the post-root `fails.txt`. A post with no supported media links receives a post-root `fails.txt` containing the reason and the post text captured from `.bbWrapper` in the saved HTML. Smilie elements are omitted. Download failures are grouped by host and separated by a blank line (for example, `Bunkr download failed: image 1`). Media positions are assigned during HTML extraction and do not depend on download completion order. Existing non-empty media files are not overwritten. Failure logs are refreshed after each run, so recovered failures do not leave stale `fails.txt` files behind.

Optional PixelDrain credentials can be supplied through the `PIXELDRAIN_APIKEY` and `PIXELDRAIN_COOKIES` environment variables. Download concurrency and speed can be adjusted with `--max-concurrent` and `--speed-limit-mbps`.

The copied modules under `core` provide extraction, host resolution, and downloading. They are separate copies; changes here do not modify the original project modules.
