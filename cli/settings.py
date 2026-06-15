"""Settings management for Simp Console."""

import os
import json
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

import questionary

from .logger import print_info, print_success, print_error

PROJECT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_DIR / "settings.json"


@dataclass
class AppSettings:
    input_dir: str = "input"
    models_data_dir: str = "models_data"
    output_dir: str = "output"
    sort_dir: str = os.path.expanduser("~/Downloads")
    extraction_mode: str = "normal"
    max_concurrent_downloads: int = 3
    streaming_resolve: bool = True
    update_branch: str = "main"
    cookies_json: str = ""
    request_delay: float = 3.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    max_speed_mbps: int = 0  # 0 = unlimited, in MB/s
    pixeldrain_api_key: str = ""  # API key for pixeldrain.com
    pixeldrain_cookies_json: str = ""  # pd_auth_key cookie JSON for pixeldrain

    def save(self):
        os.makedirs(PROJECT_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "AppSettings":
        if SETTINGS_PATH.exists():
            try:
                with open(SETTINGS_PATH) as f:
                    data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()


# ── Interactive settings editor ────────────────────────────────────

def edit_settings(settings: AppSettings) -> AppSettings:
    """Let user pick a setting to change from a list, then edit it."""
    while True:
        print_info("Current settings:")
        choices = [
            questionary.Choice(
                title=f"📁 Input directory:          {settings.input_dir}",
                value="input_dir",
            ),
            questionary.Choice(
                title=f"📁 Models data directory:    {settings.models_data_dir}",
                value="models_data_dir",
            ),
            questionary.Choice(
                title=f"📁 Output directory:         {settings.output_dir}",
                value="output_dir",
            ),
            questionary.Choice(
                title=f"📁 Sort directory:           {settings.sort_dir}",
                value="sort_dir",
            ),
            questionary.Choice(
                title=f"⚙️  Extraction mode:          {settings.extraction_mode}",
                value="extraction_mode",
            ),
            questionary.Choice(
                title=f"⚙️  Max concurrent downloads: {settings.max_concurrent_downloads}",
                value="max_concurrent_downloads",
            ),
            questionary.Choice(
                title=f"⚙️  Download Speed Limit (MB/s): {settings.max_speed_mbps if settings.max_speed_mbps > 0 else 'unlimited'}",
                value="max_speed_mbps",
            ),
            questionary.Choice(
                title=f"⚙️  Request delay:            {settings.request_delay}s",
                value="request_delay",
            ),
            questionary.Choice(
                title=f"🔄  Update branch:            {settings.update_branch}",
                value="update_branch",
            ),
            questionary.Choice(
                title=f"🔑 Cookies:                  {'saved ✓' if settings.cookies_json else 'not set'}",
                value="cookies_json",
            ),
            questionary.Choice(
                title=f"🔑 Pixeldrain API key:       {'saved ✓' if settings.pixeldrain_api_key else 'not set'}",
                value="pixeldrain_api_key",
            ),
            questionary.Choice(
                title=f"🔑 Pixeldrain cookie:        {'saved ✓' if settings.pixeldrain_cookies_json else 'not set'}",
                value="pixeldrain_cookies_json",
            ),
        ]
        choices.append(questionary.Separator())
        choices.append(questionary.Choice(
            title="✅  Save and return to menu",
            value="__save__",
        ))
        choices.append(questionary.Choice(
            title="❌  Discard changes and return",
            value="__cancel__",
        ))

        pick = questionary.select(
            "What would you like to change? (select a setting)",
            choices=choices,
        ).ask()

        if not pick or pick in ("__save__", "__cancel__"):
            if pick == "__save__":
                settings.save()
                print_success("Settings saved.")
            else:
                # Reload from disk to discard
                settings = AppSettings.load()
                print_info("Changes discarded.")
            return settings

        _edit_single_setting(settings, pick)


def _edit_single_setting(settings: AppSettings, key: str):
    """Prompt the user to change a single setting by key."""
    if key == "extraction_mode":
        # Build choices with the current value pre-selected
        mode_choices = [
            questionary.Choice(
                title="normal \u2014 apply URL patterns",
                value="normal",
                checked=(settings.extraction_mode == "normal"),
            ),
            questionary.Choice(
                title="no_filter \u2014 keep all URLs",
                value="no_filter",
                checked=(settings.extraction_mode == "no_filter"),
            ),
            questionary.Choice(
                title="reverse \u2014 keep URLs NOT matching patterns",
                value="reverse",
                checked=(settings.extraction_mode == "reverse"),
            ),
            questionary.Separator(),
            questionary.Choice(title="\u2190 Back", value=None),
        ]
        mode = questionary.select(
            "Extraction mode:",
            choices=mode_choices,
        ).ask()
        if mode:
            settings.extraction_mode = mode
            print_success(f"Extraction mode \u2192 {mode}")

    elif key == "max_concurrent_downloads":
        inp = questionary.text(
            "Max concurrent downloads:",
            default=str(settings.max_concurrent_downloads),
        ).ask()
        if inp and inp.strip().isdigit():
            val = int(inp.strip())
            if 1 <= val <= 20:
                settings.max_concurrent_downloads = val
                print_success(f"Max concurrent downloads → {val}")
            else:
                print_error("Must be between 1 and 20")
        elif inp:
            print_error("Not a valid number")

    elif key == "max_speed_mbps":
        inp = questionary.text(
            "Download Speed Limit in MB/s (0 = unlimited, max 1000):",
            default=str(settings.max_speed_mbps),
        ).ask()
        if inp and inp.strip().isdigit():
            val = int(inp.strip())
            if 0 <= val <= 1000:
                settings.max_speed_mbps = val
                label = f"{val} MB/s" if val > 0 else "unlimited"
                print_success(f"Download Speed Limit → {label}")
            else:
                print_error("Must be between 0 and 1000")
        elif inp:
            print_error("Not a valid number")

    elif key == "request_delay":
        inp = questionary.text(
            "Request delay (seconds between crawl requests) \u2014 leave empty to keep current:",
            default=str(settings.request_delay),
        ).ask()
        if inp is None:
            return
        if inp:
            try:
                val = float(inp.strip())
                if 0.0 <= val <= 60.0:
                    settings.request_delay = val
                    print_success(f"Request delay \u2192 {val}s")
                else:
                    print_error("Must be between 0 and 60")
            except ValueError:
                print_error("Not a valid number")

    elif key == "cookies_json":
        _edit_cookies(settings)

    elif key == "update_branch":
        branch_choices = [
            questionary.Choice(
                title="main (stable) \u2014 recommended for daily use",
                value="main",
                checked=(settings.update_branch == "main"),
            ),
            questionary.Choice(
                title="dev (development) \u2014 latest features, may be unstable",
                value="dev",
                checked=(settings.update_branch == "dev"),
            ),
            questionary.Separator(),
            questionary.Choice(title="\u2190 Back", value=None),
        ]
        pick = questionary.select(
            "Default update branch:",
            choices=branch_choices,
        ).ask()
        if pick:
            settings.update_branch = pick
            print_success(f"Update branch → {pick}")

    elif key == "pixeldrain_api_key":
        inp = questionary.text(
            "Pixeldrain API Key (leave empty to keep current):",
            default=settings.pixeldrain_api_key,
        ).ask()
        if inp is None:
            return
        settings.pixeldrain_api_key = inp.strip()
        print_success(f"Pixeldrain API key {'saved' if settings.pixeldrain_api_key else 'cleared'}")

    elif key == "pixeldrain_cookies_json":
        print_info("Pixeldrain cookies: " + ("saved ✓" if settings.pixeldrain_cookies_json else "not set"))
        action = questionary.select(
            "Pixeldrain cookie action:",
            choices=[
                "Replace cookie (paste EditThisCookie JSON)",
                "Clear cookie",
                questionary.Separator(),
                "Keep as-is (back)",
            ],
        ).ask()

        if action and "Replace" in action:
            new_val = questionary.text(
                "Paste EditThisCookie JSON array (with pd_auth_key entry):"
            ).ask()
            if new_val and new_val.strip():
                import json
                try:
                    data = json.loads(new_val.strip())
                    if isinstance(data, list) and len(data) > 0:
                        settings.pixeldrain_cookies_json = new_val.strip()
                        print_success("Pixeldrain cookie updated.")
                    else:
                        print_error("Must be a non-empty JSON array")
                except json.JSONDecodeError as e:
                    print_error(f"Invalid JSON: {e}")

        elif action and "Clear" in action:
            if questionary.confirm("Clear saved pixeldrain cookie?", default=False).ask():
                settings.pixeldrain_cookies_json = ""
                print_success("Pixeldrain cookie cleared.")

    elif key in ("input_dir", "models_data_dir", "output_dir", "sort_dir"):
        labels = {
            "input_dir": "Input directory (for HTML files)",
            "models_data_dir": "Models data directory (for extracted JSON)",
            "output_dir": "Output directory (for downloads)",
            "sort_dir": "Sort directory (destination for sorted files)",
        }
        inp = questionary.text(
            labels[key],
            default=getattr(settings, key),
        ).ask()
        if inp is None:
            return
        if inp and inp.strip():
            setattr(settings, key, inp.strip())
            print_success(f"{labels[key]} \u2192 {inp.strip()}")

def _edit_cookies(settings: AppSettings):
    """Edit cookies inline (not the full manage_cookies submenu)."""
    print_info("Current cookies: " + ("saved \u2713" if settings.cookies_json else "not set"))
    action = questionary.select(
        "Cookie action:",
        choices=[
            "Replace cookies",
            "Clear cookies",
            questionary.Separator(),
            "Keep as-is (back)",
        ],
    ).ask()

    if action == "Replace cookies":
        new_cookies = questionary.text("Paste new EditThisCookie JSON array:").ask()
        if new_cookies and new_cookies.strip():
            import json
            try:
                data = json.loads(new_cookies.strip())
                if isinstance(data, list) and len(data) > 0:
                    settings.cookies_json = new_cookies.strip()
                    print_success("Cookies updated.")
                else:
                    print_error("Must be a non-empty JSON array")
            except json.JSONDecodeError as e:
                print_error(f"Invalid JSON: {e}")

    elif action == "Clear cookies":
        if questionary.confirm("Clear saved cookies?", default=False).ask():
            settings.cookies_json = ""
            print_success("Cookies cleared.")


def manage_cookies(settings: AppSettings) -> AppSettings:
    """Dedicated cookie management."""
    print_info("Cookie Management")
    if settings.cookies_json:
        print_info("Cookies are currently saved.")
        action = questionary.select(
            "What would you like to do?",
            choices=[
                "View cookie domains",
                "Replace cookies",
                "Clear cookies",
                "Back",
            ],
        ).ask()

        if action == "View cookie domains":
            try:
                data = json.loads(settings.cookies_json)
                if isinstance(data, list):
                    domains = set()
                    for e in data:
                        if isinstance(e, dict) and "domain" in e:
                            domains.add(e["domain"])
                    print_info(f"Cookie domains: {', '.join(sorted(domains))}")
                else:
                    print_error("Invalid cookie JSON format")
            except json.JSONDecodeError:
                print_error("Cannot decode saved cookies")
            questionary.press_any_key_to_continue("Press any key to continue...").ask()

        elif action == "Replace cookies":
            new_cookies = questionary.text(
                "Paste new EditThisCookie JSON array:",
            ).ask()
            if new_cookies and new_cookies.strip():
                # Validate
                try:
                    data = json.loads(new_cookies.strip())
                    if isinstance(data, list) and len(data) > 0:
                        settings.cookies_json = new_cookies.strip()
                        settings.save()
                        print_success("Cookies updated.")
                    else:
                        print_error("Must be a non-empty JSON array")
                except json.JSONDecodeError as e:
                    print_error(f"Invalid JSON: {e}")

        elif action == "Clear cookies":
            if questionary.confirm("Clear saved cookies?", default=False).ask():
                settings.cookies_json = ""
                settings.save()
                print_success("Cookies cleared.")
    else:
        print_info("No cookies saved.")
        if questionary.confirm("Paste cookie JSON now?", default=True).ask():
            new_cookies = questionary.text(
                "Paste EditThisCookie JSON array:",
            ).ask()
            if new_cookies and new_cookies.strip():
                try:
                    data = json.loads(new_cookies.strip())
                    if isinstance(data, list) and len(data) > 0:
                        settings.cookies_json = new_cookies.strip()
                        settings.save()
                        print_success("Cookies saved.")
                    else:
                        print_error("Must be a non-empty JSON array")
                except json.JSONDecodeError as e:
                    print_error(f"Invalid JSON: {e}")

    return settings
