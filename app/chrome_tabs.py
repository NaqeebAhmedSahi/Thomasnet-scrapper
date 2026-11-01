from __future__ import annotations

import json
import platform
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

from app.browser import CHROME_PROFILE, USER_CHROME as CHROME_ROOT, _hidden_kwargs
URL_RE = re.compile(rb"https?://[^\x00-\x20\"'<>\\]{8,400}")
SKIP_HOSTS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adtrafficquality.google",
    "captcha-delivery.com",
    "company-target.com",
    "rubiconproject.com",
    "adnxs.com",
    "google-analytics.com",
    "googletagmanager.com",
)


def chrome_profiles() -> list[dict]:
    state_path = CHROME_ROOT / "Local State"
    if not state_path.exists():
        return [{"dir": "Default", "name": "Your Chrome", "email": "", "label": "Your Chrome"}]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [{"dir": "Default", "name": "Your Chrome", "email": "", "label": "Your Chrome"}]
    info = state.get("profile", {}).get("info_cache", {})
    profiles = []
    for directory, meta in info.items():
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("name") or directory).strip()
        email = str(meta.get("user_name") or "").strip()
        label = f"{name} ({email})" if email else name
        profiles.append({"dir": directory, "name": name, "email": email, "label": label})
    profiles.sort(key=lambda item: (item["dir"] != "Default", item["label"].lower()))
    return profiles or [{"dir": "Default", "name": "Your Chrome", "email": "", "label": "Your Chrome"}]


def visible_tabs() -> list[dict]:
    """Open Chrome windows, including the scraper window when it is running."""
    items = []
    seen_titles = set()
    for window in list_chrome_windows():
        title = window["title"].replace(" - Google Chrome", "").strip() or "Chrome"
        prefix = "Scraper window" if window["scraper"] else "Open window"
        seen_titles.add(title.lower())
        items.append(
            {
                "kind": "window",
                "window_id": window["id"],
                "title": title,
                "label": f"{prefix}: {title}",
                "profile": "",
                "url": "",
                "scraper": window["scraper"],
            }
        )
    for tab in scraper_tabs():
        if tab["title"].lower() in seen_titles:
            continue
        tab["label"] = f"Scraper tab: {tab['title']}"
        items.append(tab)
    return items


def scraper_tabs() -> list[dict]:
    """Tabs that belong to the scraper Chrome only."""
    port = _scraper_debug_port()
    if not port:
        return []
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    items = []
    for page in payload:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        url = str(page.get("url") or "")
        if url.startswith("blob:") or url.startswith("chrome-extension:"):
            continue
        title = str(page.get("title") or "").strip() or tab_title(url) or "Chrome tab"
        items.append(
            {
                "kind": "tab",
                "window_id": "",
                "title": title,
                "label": title,
                "profile": "",
                "url": url,
                "scraper": True,
            }
        )
    return items


def _scraper_debug_port() -> int | None:
    needle = f"--user-data-dir={CHROME_PROFILE}"
    try:
        output = subprocess.check_output(["ps", "-eo", "args"], text=True, errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return None
    for line in output.splitlines():
        if needle not in line or "--type=" in line:
            continue
        match = re.search(r"--remote-debugging-port=(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def chrome_choices(profile_dir: str) -> list[dict]:
    items = []
    for window in list_chrome_windows():
        if window["scraper"]:
            continue
        title = window["title"].replace(" - Google Chrome", "").strip() or "Chrome"
        prefix = "Open window"
        items.append(
            {
                "kind": "window",
                "window_id": window["id"],
                "title": title,
                "label": f"{prefix}: {title}",
                "profile": profile_dir,
                "url": "",
                "scraper": window["scraper"],
            }
        )
    seen = set()
    for tab in profile_tabs(profile_dir):
        if not tab.get("thomasnet") or tab["key"] in seen:
            continue
        seen.add(tab["key"])
        items.append(tab)
    return items


def list_chrome_windows() -> list[dict]:
    if platform.system().lower() == "windows":
        return _windows_chrome_windows()
    try:
        output = subprocess.check_output(["wmctrl", "-lx"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    windows = []
    pattern = re.compile(r"^(0x\S+)\s+-?\d+\s+(.*?\.Google-chrome)\s+\S+\s+(.*)$")
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        window_id, wm_class, title = match.group(1), match.group(2), match.group(3)
        windows.append(
            {
                "id": window_id,
                "title": title.strip(),
                "scraper": "chrome-profile" in wm_class or "thomasnet/.cache" in wm_class,
            }
        )
    return windows


def profile_tabs(profile_dir: str) -> list[dict]:
    folder = CHROME_ROOT / profile_dir / "Sessions"
    if not folder.exists():
        return []
    files = sorted(folder.glob("Session_*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        files = sorted(folder.glob("Tabs_*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        return []
    try:
        data = files[0].read_bytes()
    except OSError:
        return []
    tabs = []
    seen = set()
    for url in extract_urls(data):
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if any(host.endswith(skip) for skip in SKIP_HOSTS):
            continue
        if "recaptcha" in url or "/ads/" in url:
            continue
        key = f"{parsed.scheme}://{host}{parsed.path}".rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        title = tab_title(url)
        tabs.append(
            {
                "kind": "tab",
                "window_id": "",
                "title": title,
                "label": f"Tab: {title}",
                "profile": profile_dir,
                "url": url.split("#", 1)[0],
                "key": key,
                "scraper": False,
                "thomasnet": "thomasnet.com" in host,
            }
        )
    tabs.sort(key=lambda item: (not item["thomasnet"], item["title"].lower()))
    return tabs[:40]


def extract_urls(data: bytes) -> list[str]:
    found = []
    for match in URL_RE.findall(data):
        url = match.decode("utf-8", "ignore").strip()
        url = url.rstrip(".,);")
        if url.startswith("http://") or url.startswith("https://"):
            found.append(url)
    return found


def tab_title(url: str) -> str:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if "company" in parts:
        index = parts.index("company")
        if index + 1 < len(parts):
            slug = parts[index + 1]
            stem = re.sub(r"-\d+$", "", slug).replace("-", " ").strip()
            if stem:
                return stem.title()
    if parts:
        leaf = unquote(parts[-1]).replace("-", " ").strip()
        if leaf and leaf.lower() not in {"profile", "search", "suppliers"}:
            return leaf[:80]
    host = parsed.netloc.replace("www.", "")
    return host or url[:80]


def open_chrome_item(item: dict) -> bool:
    window_id = item.get("window_id") or ""
    if not window_id:
        return False
    focus_window(window_id)
    return True


def focus_window(window_id: str) -> None:
    try:
        if platform.system().lower() == "windows":
            command = (
                f"$p = Get-Process -Id {int(window_id)} -ErrorAction SilentlyContinue; "
                "if ($p -and $p.MainWindowTitle) { "
                "(New-Object -ComObject WScript.Shell).AppActivate($p.MainWindowTitle) | Out-Null }"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_hidden_kwargs(),
            )
            return
        subprocess.Popen(
            ["wmctrl", "-i", "-a", window_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return


def _windows_chrome_windows() -> list[dict]:
    command = (
        "Get-Process chrome -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowTitle } | "
        "ForEach-Object { '{0}|{1}' -f $_.Id, $_.MainWindowTitle }"
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", command],
            text=True,
            errors="replace",
            **_hidden_kwargs(),
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    windows = []
    for line in output.splitlines():
        if "|" not in line:
            continue
        window_id, title = line.split("|", 1)
        windows.append(
            {
                "id": window_id.strip(),
                "title": title.strip(),
                "scraper": "thomasnet" in title.lower(),
            }
        )
    return windows


def focus_scraper_window() -> None:
    for window in list_chrome_windows():
        if window["scraper"]:
            focus_window(window["id"])
            return


def stage_account_profile(profile_dir: str) -> None:
    """Build the scraper window from the selected Chrome account, without its extensions."""
    source = CHROME_ROOT / profile_dir
    if not source.is_dir():
        raise RuntimeError(f"Chrome profile folder was not found: {profile_dir}")
    destination = CHROME_PROFILE / "Default"
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in ("Cookies", "Login Data", "Login Data For Account", "Web Data", "Account Web Data"):
        src = source / name
        if not src.exists():
            src = source / "Network" / name
        if not src.exists():
            continue
        _copy_sqlite(src, destination / name)
        copied += 1
    preferences = source / "Preferences"
    if preferences.exists():
        _write_account_preferences(preferences, destination / "Preferences")
        copied += 1
    accounts = source / "Accounts"
    if accounts.is_dir():
        try:
            shutil.copytree(accounts, destination / "Accounts", dirs_exist_ok=True)
        except OSError:
            pass
    _write_local_state(profile_dir)
    secure = destination / "Secure Preferences"
    if secure.exists():
        secure.unlink()
    if copied == 0:
        raise RuntimeError(f"No sign-in data was found in Chrome profile {profile_dir}.")


def _write_account_preferences(source: Path, destination: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    extensions = data.get("extensions")
    if isinstance(extensions, dict):
        extensions["settings"] = {}
        extensions.pop("commands", None)
        data["extensions"] = extensions
    profile = data.get("profile")
    if isinstance(profile, dict):
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        data["profile"] = profile
    destination.write_text(json.dumps(data), encoding="utf-8")


def _write_local_state(profile_dir: str) -> None:
    source = CHROME_ROOT / "Local State"
    if not source.exists():
        return
    data = json.loads(source.read_text(encoding="utf-8"))
    info = data.get("profile", {}).get("info_cache", {})
    selected = info.get(profile_dir, {}) if isinstance(info, dict) else {}
    data.setdefault("profile", {})
    data["profile"]["info_cache"] = {"Default": selected}
    data["profile"]["last_used"] = "Default"
    data["profile"]["profiles_created"] = 1
    data["profile"]["profiles_order"] = ["Default"]
    (CHROME_PROFILE / "Local State").write_text(json.dumps(data), encoding="utf-8")


def _copy_sqlite(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    try:
        src = sqlite3.connect(f"file:{source}?immutable=1", uri=True)
        dest = sqlite3.connect(destination)
        src.backup(dest)
        dest.close()
        src.close()
    except sqlite3.Error:
        shutil.copy2(source, destination)
