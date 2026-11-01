from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import PROJECT_ROOT, build_search_url

CHROME_PROFILE = PROJECT_ROOT / ".cache" / "chrome-profile"


def chrome_user_data_dir() -> Path:
    system = platform.system().lower()
    if system == "windows":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "Google" / "Chrome" / "User Data"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


USER_CHROME = chrome_user_data_dir()

DRIVER_CACHE = PROJECT_ROOT / ".cache" / "drivers"


class ScrapeStopped(Exception):
    pass


class AccessRestricted(RuntimeError):
    pass


class UseChromeProfile(Exception):
    def __init__(self, profile_dir: str):
        self.profile_dir = profile_dir
        super().__init__(profile_dir)


def chrome_binary() -> str | None:
    if platform.system().lower() == "windows":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        for candidate in (
            Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ):
            if candidate.exists():
                return str(candidate)
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def chrome_version() -> tuple[int | None, str | None]:
    binary = chrome_binary()
    if not binary:
        return None, None
    try:
        output = subprocess.check_output([binary, "--version"], text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", output)
    if not match:
        return None, None
    return int(match.group(1)), match.group(0)


def create_driver(headless: bool = False, source_profile: str = ""):
    major, full = chrome_version()
    binary = chrome_binary()
    if not binary or not major or not full:
        raise RuntimeError("Google Chrome is not installed, or its version could not be read.")

    downloaded = _ensure_chromedriver(full)
    profile = source_profile or "Default"
    _release_scraper_chrome()
    time.sleep(0.4)
    from app.chrome_tabs import stage_account_profile

    stage_account_profile(profile)
    return _launch_chrome(
        headless=headless,
        user_data_dir=str(CHROME_PROFILE),
        profile_directory="Default",
        major=major,
        full=full,
        binary=binary,
        downloaded=downloaded,
        release_scraper=True,
        disable_extensions=True,
    )


def _default_chrome_running() -> bool:
    for _pid, args in _chrome_processes():
        if _is_user_chrome(args) and "--type=" not in args:
            return True
    return False


def _launch_chrome(
    headless: bool,
    user_data_dir: str,
    profile_directory: str,
    major: int,
    full: str,
    binary: str,
    downloaded,
    release_scraper: bool,
    disable_extensions: bool = False,
):
    errors: list[str] = []
    attempts = []
    if downloaded:
        attempts.append({"driver_executable_path": str(downloaded), "version_main": major})
    attempts.append({"version_main": major})
    sandbox_order = (False, True) if platform.system().lower() == "linux" else (True, False)
    for extra in attempts:
        for sandbox in sandbox_order:
            if release_scraper:
                _release_scraper_chrome()
            options = _options(headless=headless, sandbox=sandbox, disable_extensions=disable_extensions)
            if profile_directory:
                options.add_argument(f"--profile-directory={profile_directory}")
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            try:
                driver = uc.Chrome(
                    options=options,
                    browser_executable_path=binary,
                    user_data_dir=user_data_dir,
                    headless=headless,
                    use_subprocess=True,
                    **extra,
                )
                driver.set_page_load_timeout(50)
                driver.implicitly_wait(0)
                return driver
            except Exception as exc:
                errors.append(str(exc).splitlines()[0][:240])
                if release_scraper:
                    _release_scraper_chrome()
    detail = errors[-1] if errors else "Chrome failed to start."
    raise RuntimeError(
        "Could not start undetected Chrome. "
        f"Installed Chrome is {full}. Last error: {detail}"
    )


def _release_scraper_chrome() -> None:
    """Close only the scraper profile, not the person's normal Chrome window."""
    profile = str(CHROME_PROFILE)
    for pid, args in _chrome_processes():
        if not _command_has_dir(args, profile):
            continue
        try:
            os.kill(pid, 15)
        except OSError:
            continue
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock = CHROME_PROFILE / name
        try:
            if lock.is_symlink() or lock.exists():
                lock.unlink()
        except OSError:
            continue


def close_driver(driver) -> None:
    if driver is None:
        return
    if getattr(driver, "keep_browser", False):
        process = getattr(getattr(driver, "service", None), "process", None)
        if process is not None and process.poll() is None:
            process.kill()
        return
    try:
        driver.quit()
    except Exception:
        pass
    try:
        driver.quit = lambda *args, **kwargs: None
    except Exception:
        pass


def activate_tab(driver, title: str, url: str) -> bool:
    port = getattr(driver, "debug_port", None)
    pages = _debug_pages(port) if port else []
    chosen = _match_page(pages, title, url)
    if chosen:
        target_id = str(chosen.get("id") or "")
        if _switch_handle(driver, target_id):
            return True
        try:
            driver.execute_cdp_cmd("Target.activateTarget", {"targetId": target_id})
        except WebDriverException:
            pass
        if _switch_handle(driver, target_id):
            return True
    for handle in list(driver.window_handles):
        driver.switch_to.window(handle)
        current_title = driver.title or ""
        current_url = driver.current_url or ""
        if url and _same_target(url, current_url):
            return True
        if title and title.lower() in current_title.lower():
            return True
    return False


def _executable_token(args: str) -> str:
    text = args.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]
    return text.split(" ", 1)[0].strip().strip('"')


def _is_user_chrome(args: str) -> bool:
    executable = _executable_token(args)
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name not in {"chrome", "chrome.exe", "google-chrome", "google-chrome-stable"}:
        return False
    marker = "--user-data-dir="
    if marker not in args:
        return True
    value = _argument_value(args, marker)
    return _same_dir(value, str(USER_CHROME))


def _argument_value(args: str, marker: str) -> str:
    value = args.split(marker, 1)[1]
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[1:end] if end > 1 else value.strip('"')
    return value.split(" ", 1)[0].strip().strip('"')


def _same_dir(left: str, right: str) -> bool:
    first = left.strip().strip('"').rstrip("\\/")
    second = right.strip().strip('"').rstrip("\\/")
    if platform.system().lower() == "windows":
        return first.lower() == second.lower()
    return first == second


def _command_has_dir(args: str, folder: str) -> bool:
    folded = args.replace('"', "")
    if folder in folded:
        return True
    return folder.replace("/", "\\") in folded


def _hidden_kwargs() -> dict:
    if platform.system().lower() != "windows":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _chrome_processes() -> list[tuple[int, str]]:
    try:
        if platform.system().lower() == "windows":
            command = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'chrome' } | "
                "ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.CommandLine }"
            )
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", command],
                text=True,
                errors="replace",
                **_hidden_kwargs(),
            )
        else:
            output = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return []
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line or not line[0].isdigit():
            continue
        pid_text, _, args = line.partition(" ")
        if pid_text.isdigit():
            rows.append((int(pid_text), args))
    return rows


def _port_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _debug_pages(port: int) -> list[dict]:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("type") == "page"]


def _match_page(pages: list[dict], title: str, url: str) -> dict | None:
    for page in pages:
        if url and _same_target(url, str(page.get("url") or "")):
            return page
    title_text = (title or "").lower()
    for page in pages:
        if title_text and title_text in str(page.get("title") or "").lower():
            return page
    return None


def _switch_handle(driver, target_id: str) -> bool:
    if not target_id:
        return False
    for handle in driver.window_handles:
        if target_id in handle:
            driver.switch_to.window(handle)
            return True
    return False


def _same_target(wanted: str, current: str) -> bool:
    if not wanted or not current:
        return False
    left = urlparse(wanted)
    right = urlparse(current)
    if left.netloc and right.netloc and left.netloc.replace("www.", "") != right.netloc.replace("www.", ""):
        return False
    left_path = left.path.rstrip("/")
    right_path = right.path.rstrip("/")
    if not left_path:
        return False
    return left_path == right_path


def dismiss_overlays(driver) -> None:
    selectors = [
        "#ketch-banner-button-primary",
        "button[aria-label='I understand']",
        "#onetrust-accept-btn-handler",
        "button[aria-label='Accept All']",
        "button[aria-label='Accept all']",
        "button[aria-label='Accept']",
        "button#truste-consent-button",
    ]
    for selector in selectors:
        try:
            driver.execute_script(
                "document.querySelectorAll(arguments[0]).forEach(function(node){ node.click(); });",
                selector,
            )
        except WebDriverException:
            continue


def wait_for_search(driver, timeout: int = 35) -> None:
    def ready(browser):
        if browser.find_elements(By.CSS_SELECTOR, "[data-sentry-component='SearchResultSupplier']"):
            return True
        if browser.find_elements(By.CSS_SELECTOR, "[class*='search-results_resultsList'] li"):
            return True
        source = browser.page_source.lower()
        return "found no matches" in source

    WebDriverWait(driver, timeout).until(ready)


def access_restricted(source: str) -> bool:
    text = source.lower()
    return (
        "access is temporarily restricted" in text
        or "unusual activity from your device" in text
        or "automated (bot) activity" in text
    )


def raise_if_blocked(driver) -> None:
    try:
        source = driver.page_source.lower()
    except WebDriverException:
        return
    if access_restricted(source):
        save_debug(driver, "blocked")
        raise AccessRestricted(
            "Thomasnet restricted this network. Stop now and wait a few hours. "
            "Saved suppliers stay on disk. When the normal site loads in Chrome, "
            "start again with Resume checked and set Delay to 10 seconds or more."
        )
    blocked = (
        "cf-challenge" in source
        or "attention required" in source
        or "just a moment" in source
        or "/cdn-cgi/challenge" in source
    )
    if blocked:
        save_debug(driver, "blocked")
        raise RuntimeError(
            "The site showed a browser check. Leave headless off, complete the check in the Chrome window, and start again."
        )


def wait_for_profile(driver, timeout: int = 30) -> None:
    WebDriverWait(driver, timeout).until(
        EC.any_of(
            EC.presence_of_element_located((By.XPATH, "//h3[contains(., 'Business Description')]")),
            EC.presence_of_element_located((By.XPATH, "//h3[contains(., 'Business Details')]")),
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-sentry-component='BusinessDetailsSection']")),
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-sentry-component='CompanyProductsOffered']")),
        )
    )


def scroll_page(driver) -> None:
    try:
        height = driver.execute_script("return document.body.scrollHeight") or 2000
        for step in range(0, int(height), 700):
            driver.execute_script("window.scrollTo(0, arguments[0]);", step)
            time.sleep(0.15)
        driver.execute_script("window.scrollTo(0, 0);")
    except WebDriverException:
        return


def open_search_page(driver, search_term: str, page: int, hooks, headless: bool = False) -> str:
    if hooks.stopped():
        raise ScrapeStopped()
    url = build_search_url(search_term, page)
    hooks.log(f"Opening results page {page}")
    driver.get(url)
    dismiss_overlays(driver)
    raise_if_blocked(driver)
    _wait_for_results_page(driver, search_term, page, hooks, headless)
    scroll_page(driver)
    raise_if_blocked(driver)
    return driver.page_source


def _wait_for_results_page(driver, search_term: str, page: int, hooks, headless: bool) -> None:
    login_noted = False
    reloaded_after_login = False
    signup_clear_since = None
    deadline = time.time() + 45
    signin_deadline = 0.0
    while True:
        if hooks.stopped():
            raise ScrapeStopped()
        hooks.wait_if_paused()
        if not login_noted:
            tab = hooks.pull_tab()
            if tab:
                found = activate_tab(driver, tab.get("title") or "", tab.get("url") or "")
                if found:
                    hooks.log(f"Using the scraper tab {tab.get('title') or tab.get('label') or ''}.")
                else:
                    hooks.log("That tab is not in the scraper Chrome. Staying on the current scraper tab.")
        if hooks.pull_signin_click() and not _signup_in_progress(driver):
            _focus_scraper_window()
            if _click_continue_to_sign_in(driver):
                hooks.log("Opened Continue to Sign In. Finish sign-up in this Chrome window and leave it open.")
            else:
                hooks.log("The scraper Chrome is in front. Finish sign-up there and leave the window open.")
        if not _signup_in_progress(driver):
            dismiss_overlays(driver)
        if _results_page_ready(driver, search_term, page):
            return
        signing_in = page > 1 and (_auth_wall_open(driver) or _signup_in_progress(driver))
        if signing_in:
            if headless:
                raise RuntimeError(
                    "Thomasnet asks you to sign in before page 2. "
                    "Run with the Chrome window visible, sign in once, and start again."
                )
            if not login_noted:
                hooks.log(
                    "Thomasnet asks you to sign in before the next results page. "
                    "Sign up in the scraper Chrome window and leave it open. "
                    "Scraping continues after the supplier list appears."
                )
                if hasattr(hooks, "needs_signin"):
                    hooks.needs_signin()
                login_noted = True
            signup_clear_since = None
            reloaded_after_login = False
            signin_deadline = time.time() + 600
            time.sleep(0.4)
            continue
        if login_noted:
            now = time.time()
            if signup_clear_since is None:
                signup_clear_since = now
            if now - signup_clear_since < 8:
                time.sleep(0.4)
                continue
            if not reloaded_after_login:
                hooks.log("Sign-up step finished. Opening the results page again.")
                driver.get(build_search_url(search_term, page))
                reloaded_after_login = True
                signup_clear_since = None
                signin_deadline = time.time() + 90
                time.sleep(0.4)
                continue
            if now > signin_deadline:
                save_debug(driver, "search_page")
                raise RuntimeError(
                    f"Results page {page} is still waiting for sign-in. "
                    "Finish sign-up in the Chrome window and start again."
                )
            time.sleep(0.4)
            continue
        if time.time() > deadline:
            save_debug(driver, "search_page")
            raise RuntimeError(
                f"Results page {page} did not finish loading. "
                "If the Chrome window is asking you to sign in, sign in and start again."
            )
        time.sleep(0.4)


def _click_continue_to_sign_in(driver) -> bool:
    script = """
        const nodes = [...document.querySelectorAll('button, a')];
        const target = nodes.find(node => (node.innerText || '').replace(/\\s+/g, ' ').includes('Continue to Sign In'));
        if (!target) return false;
        target.click();
        return true;
    """
    try:
        return bool(driver.execute_script(script))
    except WebDriverException:
        return False


def _focus_scraper_window() -> None:
    from app.chrome_tabs import focus_scraper_window

    focus_scraper_window()


def _looks_like_signup(url: str, title: str, handle_count: int) -> bool:
    text = f"{url} {title}".lower()
    markers = (
        "sign-in",
        "signin",
        "sign_in",
        "sign-up",
        "signup",
        "sign_up",
        "/register",
        "create-account",
        "createaccount",
        "accounts.google",
        "oauth",
        "/login",
        "log-in",
    )
    if any(marker in text for marker in markers):
        return True
    return handle_count > 1


def _signup_in_progress(driver) -> bool:
    try:
        url = driver.current_url or ""
        title = driver.title or ""
        handle_count = len(driver.window_handles)
    except WebDriverException:
        return True
    if _looks_like_signup(url, title, handle_count):
        return True
    if _auth_wall_open(driver):
        return True
    try:
        return bool(
            driver.execute_script(
                """
                if (document.querySelector('input[type="password"]')) return true;
                const text = (document.body && document.body.innerText || '').toLowerCase();
                if (text.includes('continue to sign in') || text.includes('register to continue')) return true;
                const dialog = document.querySelector("dialog[open], [role='dialog']");
                if (!dialog) return false;
                const dialogText = (dialog.innerText || '').toLowerCase();
                return dialogText.includes('sign in') || dialogText.includes('sign up')
                    || dialogText.includes('register') || dialogText.includes('password');
                """
            )
        )
    except WebDriverException:
        return True


def _auth_wall_open(driver) -> bool:
    try:
        dialogs = driver.find_elements(By.CSS_SELECTOR, "dialog[data-ref='authWall']")
    except WebDriverException:
        return False
    for dialog in dialogs:
        try:
            if dialog.get_attribute("open") is not None or dialog.is_displayed():
                return True
        except WebDriverException:
            continue
    return False


def _results_page_ready(driver, search_term: str, page: int) -> bool:
    try:
        if not driver.find_elements(By.CSS_SELECTOR, "[data-sentry-component='SearchResultSupplier']"):
            return False
    except WebDriverException:
        return False
    parsed = _search_page(driver.page_source, search_term, page)
    if not parsed.cards:
        return False
    if page <= 1:
        return True
    if parsed.current_page == page:
        return True
    if parsed.range_start and ((parsed.range_start - 1) // 25) + 1 == page:
        return True
    return False


def _search_page(html: str, search_term: str, requested: int):
    from app.parser import parse_search_page

    return parse_search_page(html, requested, search_term)


def save_debug(driver, name: str) -> None:
    folder = PROJECT_ROOT / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / f"{name}.html").write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass
    try:
        driver.save_screenshot(str(folder / f"{name}.png"))
    except Exception:
        pass


def _options(headless: bool, sandbox: bool, disable_extensions: bool = False):
    options = uc.ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1600,1000")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-notifications")
    if disable_extensions:
        options.add_argument("--disable-extensions")
    if not sandbox:
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
    return options


def _driver_platform() -> str | None:
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux64"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "mac-arm64"
    if system == "darwin":
        return "mac-x64"
    if system == "windows":
        return "win64"
    return None


def _ensure_chromedriver(full_version: str) -> Path | None:
    target_platform = _driver_platform()
    if not target_platform:
        return None
    folder = DRIVER_CACHE / full_version
    exe_name = "chromedriver.exe" if platform.system().lower() == "windows" else "chromedriver"
    executable = folder / exe_name
    if executable.exists():
        return executable
    url = (
        "https://storage.googleapis.com/chrome-for-testing-public/"
        f"{full_version}/{target_platform}/chromedriver-{target_platform}.zip"
    )
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "chromedriver.zip"
    try:
        with urlopen(url, timeout=60) as response:
            archive.write_bytes(response.read())
        with zipfile.ZipFile(archive) as zipped:
            member = next(name for name in zipped.namelist() if name.endswith("chromedriver") or name.endswith("chromedriver.exe"))
            zipped.extract(member, folder)
            extracted = folder / member
        extracted.replace(executable)
        if platform.system().lower() != "windows":
            executable.chmod(0o755)
        archive.unlink(missing_ok=True)
        return executable
    except Exception:
        return None
