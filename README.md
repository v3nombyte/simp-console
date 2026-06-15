# Simp Console

Terminal-based SimpScraper pipeline for crawling, extracting, downloading, sorting, and renaming media from forum threads. **Questionary-powered interactive CLI** — no web server required, runs directly on the client machine.

## Quick Start

```bash
# One-time install
python install.py

# Launch
python main.py
```

## Overview

Simp Console automates the complete workflow of grabbing media from forum threads (primarily SimpCity):

1. **Crawl** threads via cookie-authenticated HTTP requests
2. **Extract** media URLs from the crawled HTML (or imported JSON/TXT)
3. **Download** all resolved media files with parallel workers and live progress display
4. **Sort & Dedupe** by file type, removing exact + perceptual duplicates
5. **Rename** sequentially for clean archival

Every step can be run independently from the main menu, or chained into a fully automated pipeline.

## Full Pipeline (🚀 Start Full Workflow)

The guided workflow walks through each stage:

| Step | Description |
|------|-------------|
| **Input Source** | Choose crawl method, HTML files, JSON file, or import |
| **⬇️ Download** | Automatic download of all extracted URLs with live dashboard |
| **📦 Sort & Dedupe** | Quality-aware dedup (SHA-256 + perceptual hash), sorted into `images/` and `videos/` |
| **🏷️ Rename** | Sequential rename to `ModelName_1.ext`, `ModelName_2.ext`, … |

## Menu Structure

```
┌─────────────────────────────────────────────────────┐
│  🚀 Start Full Workflow                              │
├─────────────────────────────────────────────────────┤
│  🌐 Crawl SimpCity Thread                            │
├─────────────────────────────────────────────────────┤
│  📄 Extract URLs from HTML Files                     │
│    ├── Archive groups (subdirectories)               │
│    └── Loose files (root input folder)               │
│  📂 Select JSON File                                 │
│  📥 Import JSON File (from anywhere on disk)         │
├─────────────────────────────────────────────────────┤
│  ⬇️  Download Media Files                             │
│  📦 Sort & Dedupe                                     │
│  🏷️  Rename Files                                     │
├─────────────────────────────────────────────────────┤
│  📋 View Model Registry                               │
│  📂 Browse Model Folder                               │
│  📤 Export Results (TXT / JSON)                       │
├─────────────────────────────────────────────────────┤
│  ⚙️  Settings                                          │
│  🍪 Manage Cookies                                    │
│  🔄 Check for Updates                                 │
│  ❌ Exit                                               │
└─────────────────────────────────────────────────────┘
```

## Live Download Dashboard

During downloads, the console switches to a full-screen live panel with:

```
┌────────────────────────────────────────────────────┐
│  MiaCandy  |  URLs: 237  |  Done: 89/237  |  ...  │
│  Downloaded: 1.2 GB  |  24 MB/s avg  |  ETA: 4m   │
├────────────────────────────────────────────────────┤
│  Host queue:  bunkr(12)  gofile(8)  pixeldrain(5) │
├────────────────────────────────────────────────────┤
│  12 MB/s  ████████████░░░░░░░░  68%  pixeldrain.. │
│   0 B/s   ░░░░░░░░░░░░░░░░░░░░   0%  gofile.io..  │
│   8 MB/s  ██████████████████░░  91%  bunkr.cr...  │
│  ... 7 more active downloads ...                   │
├────────────────────────────────────────────────────┤
│  ✓ 187 OK  |  ✗ 12 Failed  |  Hosts: 6             │
│  [P] Pause  |  [C] Cancel                          │
└────────────────────────────────────────────────────┘
```

- Completed jobs scroll off the top; the panel always shows the next pending URLs
- **Header** includes aggregate speed (5s rolling window over all active downloads)
- **Host queue breakdown** row shows pending files per host (counts inline)
- Per-file progress bars with real-time speed (MB/s)
- Counters: OK files, failed files, active hosts
- Keyboard shortcuts: `P` Pause, `C` Cancel
- Console output is suppressed during live display — all logging goes to file

## Features

### Crawling
- Cookie-based SimpCity thread crawling with configurable delay
- Fetches all pages of a thread, saves as `Title - Page N.html`
- Rate-limited to avoid detection

### Extraction
- 3 modes: **Normal** (filtered), **Reverse Filter** (exclude matching), **No Filter** (everything)
- Archive group workflow: previously processed HTML files grouped by model
- Model name auto-suggested from HTML file title
- Results saved as both TXT (URL list) and JSON (post-structured)

### Download
- Parallel workers (configurable concurrency, **4 simultaneous per host**)
- **Streaming resolution:** Next batch pre-resolved while current batch downloads
- **Host queue breakdown display:** Per-host pending file counts shown in dashboard
- **Aggregate speed tracking:** Total speed across all active files (5s rolling window)
- **Configurable per-file speed throttle** (MB/s)
- Host-specific pipelines:
  - **gofile.io** — API with auth token forwarding
  - **pixeldrain.com** — REST API (individual files + `/l/` lists)  
  - **bunkr** variants — page scrape + browser fallback
  - **cyberdrop / cyberfile** — page scrape + browser fallback
  - **jpg4-7.su, jpg.church, host.church** — Chevereto oEmbed API
  - **redgifs.com** — scrape / direct
  - **mega.nz / mega.co.nz** — `mega.py` AES-decryption library
  - **pixl.li, pixhost.to, imgbox.com** — referer-based
  - **Direct pass-through** — imgur, catbox, postimg, gyazo, etc.
  - **Browser fallback** — Playwright+Chromium for problematic hosts
- Retry pass for all failed URLs
- Failed URLs saved to `models_data/` for later retry — includes full context (Mode{mode}, model_name, page, post)
- Existing files skipped automatically (pre-request check)

### Sort & Dedupe
- SHA-256 exact duplicate detection (file-level)
- Perceptual hashing (image + video) for near-duplicate detection
- Sorts into `images/` and `videos/` subdirectories
- Unknown file types go into `unknown/`
- ZIP/RAR archives auto-extracted after download
- Logs with newest entries at top

### Rename
- Sequential: `ModelName_001.ext`, `ModelName_002.ext`, …
- Preserves file extensions
- Configurable output folder

### Model Registry
- JSON-backed database of every processed model
- Tracks extraction, download, sort, and rename status per model
- Shows on-disk file counts and sizes

### Settings
Full configuration via interactive editor:

| Setting | Default | Description |
|---------|---------|-------------|
| Input directory | `input/` | Where HTML files live |
| Models data directory | `models_data/` | Extracted JSON/TXT output |
| Output directory | `output/` | Downloaded media destination |
| Sort directory | `~/Downloads` | Sorted/deduplicated output |
| Max concurrent downloads | `3` | Parallel download threads |
| Max Download Speed (MB/s) | `0` | Per-file download throttle in MB/s, 0 = unlimited |
| Streaming Resolve | `True` | Pre-resolve next batch of URLs while current batch downloads |
| Request delay | `3.0s` | Seconds between crawl requests |
| Extraction mode | `normal` | Normal / Reverse / No Filter |
| Update branch | `main` | Git branch for updates |

### Cookie Management
- **View** — shows stored cookies (masked values, never plaintext)
- **Replace** — paste new EditThisCookie JSON array
- **Clear** — remove all cookies

Supports any browser's EditThisCookie JSON format.

### Update System
- Git-based pull with commit log display
- Shows new commits before applying
- Auto-restart after successful update (`.restart.rqd` marker)
- Branch-selectable (main/dev via settings)
- Handles MumPi's USB-drive "dubious ownership" automatically

### File Logging
- Rotating 5 MB × 3 files
- Logs in `logs/` directory
- Timestamps with log levels

## Requirements

- Python 3.10–3.13
- Internet connection for crawling and downloading
- EditThisCookie JSON export for SimpCity crawling
- Playwright + Chromium (for browser-based host fallback)

## Installation

```bash
# Clone
git clone https://github.com/v3nombyte/simp-console.git
cd simp-console

# Install (creates venv, installs deps, sets up Playwright)
python install.py

# Run
python main.py
```

The installer auto-detects your OS (Linux / Windows), creates a virtual environment, installs all Python dependencies, and sets up Playwright's Chromium browser.

## Project Structure

```
simp-console/
├── main.py                  # Entry point (auto-detects venv)
├── start.py                 # Same with auto-restart lifecycle loop
├── install.py               # Cross-platform installer
├── requirements.txt         # Python dependencies
├── _platform.py             # OS-specific helpers (venv path, restart)
├── cli/
│   ├── menu.py              # Main questionary menu + update checker
│   ├── actions.py           # Individual operations
│   ├── workflow.py          # Full pipeline orchestrator
│   ├── settings.py          # Settings editor + cookie management
│   ├── logger.py            # File logger + download progress callback
│   └── live_display.py      # Rich live download dashboard
├── core/                    # Shared engine (shared with webapp)
│   ├── crawler.py           # SimpCity thread crawler
│   ├── downloader.py        # Async download pipeline
│   ├── extractor.py         # HTML URL extraction
│   ├── sorter.py            # SHA-256 + perceptual dedup
│   ├── renamer.py           # Sequential rename
│   ├── registry.py          # Model registry DB
│   ├── models.py            # Settings model
│   ├── exporter.py          # TXT/JSON export
│   └── unpacker.py          # Archive extraction
├── input/                   # HTML files for extraction
├── models_data/             # Extracted JSON/TXT results + failed URLs
├── output/                  # Downloaded media files
├── logs/                    # Rotating log files
├── url_patterns.json        # Keep patterns for URL filtering
├── skip_url.json            # Skip patterns for URL filtering
└── settings.json            # Persisted config (user-created)
```

## URL Filter Patterns

Two JSON files control which URLs the extractor picks up:

- **`url_patterns.json`** (keep) — URLs matching any entry are included during normal extraction. Ships with ~55 entries covering all common media hosts.
- **`skip_url.json`** (skip) — URLs matching any entry are excluded. Use to filter out thumbnails, avatars, social media links, and forum noise. Ships with ~19 entries.

Extraction modes:
- **Normal** — applies keep + skip filters
- **Reverse Filter** — inverts keep patterns (extracts what doesn't match — use to discover new domains)
- **No Filter** — extracts every URL found

## Supported Hosts (Download)

| Host | Strategy | Status |
|------|----------|--------|
| Bunkr variants (all) | Scrape + Browser fallback | ✓ Tested |
| Cyberdrop / Cyberfile (me, su, cr) | Scrape + Browser fallback | ✓ Tested |
| jpg4-7.su, jpg.church, host.church | Chevereto oEmbed API | ✓ Tested |
| pixl.li, pixhost.to, imgbox.com | Referer header | ✓ Tested |
| imagebam.com, imagepond.net | Scrape / Referer | ✓ Tested |
| pixeldrain.com (individual + `/l/` lists) | REST API | ✓ Tested |
| gofile.io | REST API | ✓ Tested |
| redgifs.com | Scrape / Direct | ✓ Tested |
| mega.nz / mega.co.nz | mega.py library | ✓ Tested |
| saint2.su, selti-delivery.ru | Direct URL mapping | ✓ Tested |
| i.imgur.com, i.gyazo.com, catbox, postimg | Passthrough | ✓ Tested |
| cdn.camwhores.tv, smgmedia | Passthrough | ✓ Tested |
| anonfiles.com, 1fichier.com, giphy.com | Generic HTTP fallback | ⚠️ May not work |
| drive.google.com, e-hentai.org | Generic HTTP fallback | ⚠️ May not work |

## License

Private — v3nombyte
