from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.browser import (
    AccessRestricted,
    ScrapeStopped,
    chrome_version,
    close_driver,
    create_driver,
    dismiss_overlays,
    open_search_page,
    raise_if_blocked,
    save_debug,
    wait_for_profile,
)
from app.config import DEFAULT_LOG_DIR, Job, build_search_url
from app.parser import list_items, make_soup, parse_profile_page, parse_search_page, split_location, unique, useful
from app.storage import Storage


class Hooks:
    def log(self, message: str) -> None:
        logging.getLogger("thomasnet").info(message)
        print(message, flush=True)

    def progress(self, data: dict) -> None:
        return None

    def supplier(self, row: dict) -> None:
        return None

    def stopped(self) -> bool:
        return False

    def wait_if_paused(self) -> None:
        return None

    def pull_profile(self) -> str | None:
        return None

    def pull_tab(self) -> dict | None:
        return None

    def pull_signin_click(self) -> bool:
        return False

    def needs_signin(self) -> None:
        return None


def _open_browser(job: Job, hooks: Hooks):
    major, full = chrome_version()
    profile = job.chrome_profile or "Default"
    hooks.log(
        f"Opening Chrome profile {profile} ({full or 'unknown'}). "
        "Your other Chrome windows stay open."
    )
    return create_driver(job.headless, source_profile=profile)


def setup_logging() -> logging.Logger:
    DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("thomasnet")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
        handler = logging.FileHandler(DEFAULT_LOG_DIR / "scraper.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def run(job: Job, hooks: Hooks | None = None) -> dict:
    hooks = hooks or Hooks()
    setup_logging()
    storage = Storage(job.output_dir)
    summary = {
        "saved": 0,
        "skipped": 0,
        "pages": 0,
        "products": 0,
        "stopped": False,
        "error": "",
    }
    driver = None
    page = job.start_page
    failed_page = None
    try:
        driver = _open_browser(job, hooks)
        hooks.log(f"Search: {job.search_term}")
        while True:
            if hooks.stopped():
                summary["stopped"] = True
                break
            hooks.wait_if_paused()
            if job.max_pages and summary["pages"] >= job.max_pages:
                hooks.log("Reached the page limit.")
                break
            try:
                html = open_search_page(driver, job.search_term, page, hooks, headless=job.headless)
                failed_page = None
            except ScrapeStopped:
                summary["stopped"] = True
                break
            except AccessRestricted as exc:
                summary["error"] = str(exc)
                hooks.log(str(exc))
                break
            except Exception as exc:
                if failed_page == page:
                    save_debug(driver, "search_error")
                    raise
                failed_page = page
                hooks.log(f"Could not open page {page}: {exc}. Connecting to your Chrome again.")
                close_driver(driver)
                driver = _open_browser(job, hooks)
                continue

            parsed = parse_search_page(html, page, job.search_term)
            if parsed.current_page and parsed.current_page != page:
                hooks.log(f"Landed on page {parsed.current_page} while requesting page {page}.")
            if not parsed.cards:
                save_debug(driver, "search_empty")
                hooks.log("No supplier cards on this page. Stopping. See logs/search_empty.html.")
                break

            storage.add_links(parsed.cards)
            total_pages = parsed.total_pages or 0
            hooks.log(
                f"Page {parsed.current_page or page}"
                f"{f' of {total_pages}' if total_pages else ''}: "
                f"stored {len(parsed.cards)} profile links"
                f"{f' ({parsed.total_results} suppliers in this search)' if parsed.total_results else ''}."
            )
            _emit_progress(hooks, storage, parsed, page, "", summary)

            for card in parsed.cards:
                if hooks.stopped():
                    summary["stopped"] = True
                    break
                hooks.wait_if_paused()
                if job.max_suppliers and summary["saved"] >= job.max_suppliers:
                    hooks.log("Reached the supplier limit.")
                    break
                if job.resume and storage.has(card.get("company_id", ""), card.get("profile_url", "")):
                    summary["skipped"] += 1
                    hooks.log(f"Already saved, skipping {card.get('name') or card.get('profile_url')}")
                    _emit_progress(hooks, storage, parsed, page, card.get("name", ""), summary)
                    continue
                hooks.log(f"Scraping {card.get('name') or 'supplier'}")
                _emit_progress(hooks, storage, parsed, page, card.get("name", ""), summary)
                try:
                    profile = _scrape_profile(driver, card, hooks)
                except AccessRestricted as exc:
                    summary["error"] = str(exc)
                    hooks.log(str(exc))
                    break
                except ScrapeStopped:
                    summary["stopped"] = True
                    break
                except Exception as exc:
                    hooks.log(f"Failed {card.get('name') or 'supplier'}: {exc}")
                    save_debug(driver, "profile_error")
                    continue
                supplier, product = _build_records(card, profile, job.search_term)
                storage.save(supplier, product)
                summary["saved"] += 1
                summary["products"] += _product_count(product)
                hooks.supplier(supplier)
                _emit_progress(hooks, storage, parsed, page, supplier.get("name", ""), summary)
                _sleep(job.delay, hooks)

            if summary["stopped"] or summary["error"]:
                break
            if job.max_suppliers and summary["saved"] >= job.max_suppliers:
                summary["pages"] += 1
                break
            storage.mark_page(parsed.current_page or page, parsed.total_results, job.search_term)
            summary["pages"] += 1
            reached_end = not parsed.has_next
            if parsed.total_pages and (parsed.current_page or page) >= parsed.total_pages:
                reached_end = True
            if reached_end:
                hooks.log("Reached the last results page.")
                break
            page += 1
            _sleep(max(job.delay, 1.0), hooks)
    except ScrapeStopped:
        summary["stopped"] = True
    except Exception as exc:
        summary["error"] = str(exc)
        hooks.log(f"Stopped on an error: {exc}")
    finally:
        close_driver(driver)
    if summary["stopped"]:
        hooks.log("Stop requested. Saved suppliers are already on disk.")
    elif not summary["error"]:
        hooks.log(
            f"Finished. Saved {summary['saved']} new suppliers, "
            f"skipped {summary['skipped']}, across {summary['pages']} pages."
        )
    return summary


def _scrape_profile(driver, card: dict, hooks: Hooks) -> dict:
    profile_url = card["profile_url"]
    driver.get(profile_url)
    raise_if_blocked(driver)
    dismiss_overlays(driver)
    try:
        wait_for_profile(driver)
    except TimeoutException:
        raise_if_blocked(driver)
        raise
    time.sleep(0.4)
    profile = parse_profile_page(driver.page_source)
    if not profile.get("categories") and not profile.get("products_services"):
        extra = _expand_catalog(driver, profile_url, card.get("slug", ""), hooks)
        profile = parse_profile_page(driver.page_source)
        profile["category_groups"] = _merge_groups(profile.get("category_groups") or [], extra.get("category_groups") or [])
        profile["categories"] = unique([item["name"] for item in profile["category_groups"]])
        profile["products_services"] = unique((profile.get("products_services") or []) + (extra.get("products_services") or []))
        profile["brands"] = unique((profile.get("brands") or []) + (extra.get("brands") or []))
        return profile
    return profile


def _expand_catalog(driver, profile_url: str, slug: str, hooks: Hooks) -> dict:
    extra = {"category_groups": [], "products_services": [], "brands": []}
    for selector in ("button#companyProductsBrandsTab", "button#companyProductsCategoriesTab"):
        found = driver.find_elements(By.CSS_SELECTOR, selector)
        if not found:
            continue
        try:
            driver.execute_script("arguments[0].click();", found[0])
            time.sleep(0.3)
        except WebDriverException:
            continue
    labels: list[str] = []
    for element in driver.find_elements(By.CSS_SELECTOR, "[aria-label^='Go to View All']"):
        label = (element.get_attribute("aria-label") or "").strip()
        if label and label not in labels:
            labels.append(label)
    for label in labels:
        if hooks.stopped():
            raise ScrapeStopped()
        try:
            _open_view_all(driver, label, profile_url, slug, extra)
        except Exception as exc:
            hooks.log(f"Could not open {label}: {exc}")
            _return_to_profile(driver, profile_url)
    return extra


def _open_view_all(driver, label: str, profile_url: str, slug: str, extra: dict) -> None:
    origin = driver.current_url
    safe = label.replace("\\", "\\\\").replace("'", "\\'")
    element = WebDriverWait(driver, 8).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"[aria-label='{safe}']"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    driver.execute_script("arguments[0].click();", element)
    try:
        WebDriverWait(driver, 4).until(
            lambda browser: browser.current_url != origin or browser.find_elements(By.CSS_SELECTOR, "dialog[open]")
        )
    except TimeoutException:
        return
    current = driver.current_url
    if slug and slug not in current:
        driver.get(profile_url)
        try:
            wait_for_profile(driver)
        except TimeoutException:
            pass
        return
    soup = make_soup(driver.page_source)
    dialog = soup.select_one("dialog[open]")
    scope = dialog or soup.select_one("[data-sentry-component='CompanyProductsOffered']") or soup.select_one("main")
    items = list_items(scope)[:400]
    lowered = label.lower()
    if "brand" in lowered:
        extra["brands"].extend(items)
    elif "product" in lowered or "service" in lowered:
        extra["products_services"].extend(items)
    else:
        group = label.replace("Go to View All", "").replace("Categories", "").strip() or "Categories"
        for item in items:
            extra["category_groups"].append({"group": group, "name": item})
    if dialog is not None:
        for button in driver.find_elements(By.CSS_SELECTOR, "dialog[open] button[aria-label='Close'], dialog[open] button[slot='close']"):
            try:
                driver.execute_script("arguments[0].click();", button)
                break
            except WebDriverException:
                continue
    if driver.current_url != origin:
        _return_to_profile(driver, profile_url)


def _return_to_profile(driver, profile_url: str) -> None:
    try:
        if driver.current_url != profile_url:
            driver.get(profile_url)
            wait_for_profile(driver)
    except WebDriverException:
        return


def _build_records(card: dict, profile: dict, search_term: str) -> tuple[dict, dict]:
    city, state, postal = split_location(card.get("location") or "")
    city = profile.get("city") or city
    state = profile.get("state") or state
    postal = profile.get("postal_code") or postal
    scraped_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    description = profile.get("description") or card.get("description_preview") or ""
    supplier = {
        "company_id": card.get("company_id", ""),
        "slug": card.get("slug", ""),
        "name": profile.get("name") or card.get("name") or "",
        "profile_url": card.get("profile_url", ""),
        "website": _pick(profile.get("website", ""), card.get("website", "")),
        "logo_url": profile.get("logo_url") or "",
        "thomas_verified": profile.get("thomas_verified") or "No",
        "location": card.get("location") or profile.get("header_location") or ", ".join(part for part in (city, f"{state} {postal}".strip()) if part),
        "address": profile.get("address") or "",
        "city": city,
        "state": state,
        "postal_code": postal,
        "employees": _pick(profile.get("employees", ""), card.get("employees", "")),
        "annual_sales": _pick(profile.get("annual_sales", ""), card.get("annual_sales", "")),
        "year_founded": _pick(profile.get("year_founded", ""), card.get("year_founded", "")),
        "primary_company_type": profile.get("primary_company_type") or "",
        "additional_activities": profile.get("additional_activities") or "",
        "company_types": card.get("company_types") or "",
        "description": description,
        "description_preview": card.get("description_preview") or "",
        "tags": card.get("tags") or [],
        "key_personnel": profile.get("key_personnel") or "",
        "diversity_status": profile.get("diversity_status") or "",
        "quality_certifications": profile.get("quality_certifications") or "",
        "registration": profile.get("registration") or "",
        "phone": profile.get("phone") or "",
        "fax": profile.get("fax") or "",
        "email": profile.get("email") or "",
        "categories": profile.get("categories") or [],
        "products_services": profile.get("products_services") or [],
        "brands": profile.get("brands") or [],
        "search_term": search_term,
        "search_url": build_search_url(search_term, int(card.get("page_number") or 1)),
        "page_number": card.get("page_number") or "",
        "card_position": card.get("card_position") or "",
        "scraped_at": scraped_at,
    }
    if not supplier["primary_company_type"]:
        parts = [part.strip() for part in (supplier["company_types"] or "").split("|")]
        supplier["primary_company_type"] = parts[0] if parts and parts[0] else ""
    product = {
        "company_id": supplier["company_id"],
        "company_name": supplier["name"],
        "profile_url": supplier["profile_url"],
        "website": supplier["website"],
        "page_number": supplier["page_number"],
        "categories": profile.get("category_groups") or [
            {"group": "Categories", "name": name} for name in supplier["categories"]
        ],
        "products_services": supplier["products_services"],
        "brands": supplier["brands"],
        "scraped_at": scraped_at,
    }
    return supplier, product


def _pick(profile_value: str, card_value: str) -> str:
    if useful(profile_value):
        return profile_value.strip()
    if useful(card_value):
        return card_value.strip()
    return (profile_value or card_value or "").strip()


def _merge_groups(base: list[dict], extra: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for item in list(base) + list(extra):
        group = (item.get("group") or "Categories").strip()
        name = (item.get("name") or "").strip()
        key = (group.casefold(), name.casefold())
        if not name or key in seen:
            continue
        seen.add(key)
        merged.append({"group": group, "name": name})
    return merged


def _product_count(product: dict) -> int:
    return len(product.get("categories") or []) + len(product.get("products_services") or []) + len(product.get("brands") or [])


def _emit_progress(hooks: Hooks, storage: Storage, parsed, page: int, current: str, summary: dict) -> None:
    product_names = 0
    for record in storage.products:
        product_names += _product_count(record)
    hooks.progress(
        {
            "page": parsed.current_page or page,
            "total_pages": parsed.total_pages or 0,
            "total_results": parsed.total_results or 0,
            "saved": len(storage.suppliers),
            "skipped": summary["skipped"],
            "new_saved": summary["saved"],
            "products": product_names,
            "current": current,
        }
    )


def _sleep(seconds: float, hooks: Hooks) -> None:
    if seconds <= 0:
        return
    jitter = random.uniform(-0.25, 0.45)
    end = time.time() + max(0.2, seconds + jitter)
    while time.time() < end:
        if hooks.stopped():
            return
        hooks.wait_if_paused()
        if hooks.stopped():
            return
        time.sleep(min(0.2, max(0.0, end - time.time())))
