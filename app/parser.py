from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from app.config import BASE_URL

PAGE_SIZE = 25
FULL_PAGE_SIZES = {10, 15, 20, 25, 30, 50}

NOT_AVAILABLE = {
    "not available",
    "n/a",
    "none",
    "no brands carried",
    "-",
}

DETAIL_LABELS = {
    "website": "website",
    "primary company type": "primary_company_type",
    "additional activities": "additional_activities",
    "key personnel": "key_personnel",
    "diverse / small bus. status": "diversity_status",
    "quality certifications": "quality_certifications",
    "registration": "registration",
    "annual sales": "annual_sales",
    "no of employees": "employees",
    "number of employees": "employees",
    "employees": "employees",
    "year founded": "year_founded",
    "telephone": "phone",
    "phone": "phone",
    "fax": "fax",
    "email": "email",
}


@dataclass
class SearchPage:
    cards: list[dict] = field(default_factory=list)
    range_start: int | None = None
    range_end: int | None = None
    total_results: int | None = None
    current_page: int | None = None
    has_next: bool = False

    @property
    def per_page(self) -> int | None:
        if self.range_start is None or self.range_end is None:
            return None
        size = self.range_end - self.range_start + 1
        return size if size > 0 else None

    @property
    def total_pages(self) -> int | None:
        if not self.total_results:
            return None
        size = _page_size(self)
        return (self.total_results + size - 1) // size


def make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def clean(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"\bmore\+\s*$", "", text, flags=re.I).strip()
    return text


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = clean(item)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def useful(value: str | None) -> bool:
    text = clean(value or "")
    return bool(text) and text.casefold() not in NOT_AVAILABLE


def absolute_url(href: str) -> str:
    return urljoin(BASE_URL + "/", href)


def split_location(location: str) -> tuple[str, str, str]:
    match = re.match(r"^(.*?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$", clean(location))
    if not match:
        return "", "", ""
    return match.group(1).strip(), match.group(2).upper(), match.group(3)


def company_identity(url: str) -> tuple[str, str]:
    match = re.search(r"/company/([^/?#]+)/profile", url, flags=re.I)
    slug = match.group(1) if match else ""
    id_match = re.search(r"(\d+)$", slug)
    company_id = id_match.group(1) if id_match else slug
    return slug, company_id


def clean_company_types(raw: str) -> str:
    raw = raw.replace("*", " ")
    parts = [clean(part) for part in re.split(r"[•|,]", raw)]
    return " | ".join(unique(parts))


def parse_search_page(html: str, page_number: int, search_term: str) -> SearchPage:
    soup = make_soup(html)
    page = SearchPage()
    page.range_start, page.range_end, page.total_results = _result_count(soup)
    page.current_page = _current_page(soup) or _page_from_links(soup) or _page_from_range(page)
    page.has_next = _has_next(soup)
    actual_page = page.current_page or page_number
    page.cards = [
        _parse_card(card, index, actual_page, search_term)
        for index, card in enumerate(_result_cards(soup), start=1)
    ]
    page.cards = [card for card in page.cards if card.get("profile_url")]
    return page


def parse_profile_page(html: str) -> dict:
    soup = make_soup(html)
    details = _business_details(soup)
    groups, products, brands = _products(soup)
    phone = details.get("phone") or _href_value(soup, "tel:")
    email = details.get("email") or _href_value(soup, "mailto:")
    website = details.get("website") or ""
    name = ""
    heading = soup.find("h1")
    if heading:
        name = clean(heading.get_text(" ", strip=True))
    profile = {
        "name": name,
        "description": _description(soup),
        "website": website,
        "primary_company_type": details.get("primary_company_type", ""),
        "additional_activities": details.get("additional_activities", ""),
        "key_personnel": details.get("key_personnel", ""),
        "diversity_status": details.get("diversity_status", ""),
        "quality_certifications": details.get("quality_certifications", ""),
        "registration": details.get("registration", ""),
        "annual_sales": details.get("annual_sales", ""),
        "employees": details.get("employees", ""),
        "year_founded": details.get("year_founded", ""),
        "phone": phone,
        "fax": details.get("fax", ""),
        "email": email,
        "address": "",
        "city": "",
        "state": "",
        "postal_code": "",
        "categories": unique([item["name"] for item in groups]),
        "category_groups": groups,
        "products_services": products,
        "brands": brands,
        "logo_url": "",
        "thomas_verified": "No",
        "header_location": "",
    }
    _apply_header(soup, profile)
    _apply_embedded_company(html, profile)
    return profile


def _apply_header(soup: BeautifulSoup, profile: dict) -> None:
    header = _header_details(soup)
    if useful(header["phone"]) and not useful(profile.get("phone")):
        profile["phone"] = header["phone"]
    if useful(header["website"]) and not useful(profile.get("website")):
        profile["website"] = header["website"]
    if useful(header["company_type"]) and not useful(profile.get("primary_company_type")):
        profile["primary_company_type"] = header["company_type"]
    profile["logo_url"] = header["logo_url"]
    profile["thomas_verified"] = "Yes" if header["thomas_verified"] else "No"
    profile["header_location"] = header["location"]


def _header_details(soup: BeautifulSoup) -> dict:
    logo = ""
    image = soup.select_one("img[alt$='Company Logo'], img[class*='supplierLogo']")
    if image is not None:
        logo = _logo_url(image.get("src") or "")
        if not logo and image.get("srcset"):
            logo = _logo_url(image.get("srcset").split(",")[0].strip().split(" ")[0])
    phone = ""
    telephone = soup.select_one("a[href^='tel:']")
    if telephone is not None and telephone.get("href"):
        phone = unquote(telephone["href"].split(":", 1)[-1]).strip()
    website = ""
    for anchor in soup.select("a[href^='http']"):
        href = (anchor.get("href") or "").strip()
        label = clean(anchor.get_text(" ", strip=True)).lower()
        if not href or "thomasnet.com" in href:
            continue
        if label == "visit website":
            website = href
            break
    location = ""
    location_node = soup.select_one("[data-testid='srp.supplier-location-link']")
    if location_node is not None:
        location = clean(location_node.get_text(" ", strip=True))
    company_type = ""
    details = soup.select_one("[data-sentry-component='SupplierDetails']")
    if details is not None:
        for node in details.find_all("div"):
            text = clean(node.get_text(" ", strip=True))
            if text in {
                "Distributor",
                "Manufacturer",
                "Custom Manufacturer",
                "Service Company",
                "Finishing Service",
                "Manufacturers' Rep",
            }:
                company_type = text
                break
    return {
        "logo_url": logo,
        "phone": phone,
        "website": website,
        "location": location,
        "company_type": company_type,
        "thomas_verified": soup.select_one("[data-sentry-component='ThomasPartnerBadge']") is not None,
    }


def _logo_url(src: str) -> str:
    src = unquote((src or "").strip())
    if not src:
        return ""
    query = parse_qs(urlparse(src).query)
    if query.get("url"):
        return query["url"][0]
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return BASE_URL + src
    return src


def list_items(scope) -> list[str]:
    if scope is None:
        return []
    items: list[str] = []
    links = scope.select("li a")
    nodes = links or scope.select("li") or scope.select("a")
    for node in nodes:
        text = clean(node.get_text(" ", strip=True))
        if _keep_catalog_item(text):
            items.append(text)
    return unique(items)


def _keep_catalog_item(text: str) -> bool:
    if not text or len(text) < 2 or len(text) > 90:
        return False
    lowered = text.casefold()
    if lowered in NOT_AVAILABLE:
        return False
    blocked = {
        "home",
        "save",
        "select",
        "view profile",
        "visit website",
        "claim your company",
        "claim this business",
        "submit",
        "scroll to top",
        "categories",
        "brands carried",
        "about",
        "profile",
        "overview",
        "products / services offered",
        "all products / services",
    }
    if lowered in blocked:
        return False
    noisy = ("profile overview", "sign in", "shortlist", "create your free", "claim this business")
    return not any(part in lowered for part in noisy)


def _apply_embedded_company(html: str, profile: dict) -> None:
    company = _embedded_company(html)
    if not company:
        return
    for field in ("name", "website", "phone", "annual_sales", "employees", "year_founded", "address", "city", "state", "postal_code"):
        if useful(company.get(field)) and not useful(profile.get(field)):
            profile[field] = company[field]
    embedded_description = company.get("description") or ""
    if useful(embedded_description) and len(embedded_description) > len(profile.get("description") or ""):
        profile["description"] = embedded_description
    if company.get("category_groups"):
        profile["category_groups"] = company["category_groups"]
        profile["categories"] = unique([item["name"] for item in company["category_groups"]])
    if company.get("products_services"):
        profile["products_services"] = company["products_services"]
    if company.get("brands"):
        profile["brands"] = unique((profile.get("brands") or []) + company["brands"])
    if not useful(profile.get("primary_company_type")) and company.get("primary_company_type"):
        profile["primary_company_type"] = company["primary_company_type"]
    if not useful(profile.get("additional_activities")) and company.get("additional_activities"):
        profile["additional_activities"] = company["additional_activities"]


def _embedded_company(html: str) -> dict:
    soup = make_soup(html)
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return {}
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return {}
    data = payload.get("props", {}).get("pageProps", {}).get("data")
    if not isinstance(data, dict):
        return {}
    groups = []
    seen: set[tuple[str, str]] = set()
    for item in data.get("headings") or []:
        if not isinstance(item, dict):
            continue
        name = clean(item.get("name"))
        family = clean(item.get("familyName")) or "Categories"
        key = (family.casefold(), name.casefold())
        if not name or key in seen:
            continue
        seen.add(key)
        groups.append({"group": family, "name": name})
    families = unique(
        clean(item.get("name"))
        for item in (data.get("families") or [])
        if isinstance(item, dict)
    )
    brands: list[str] = []
    for key in ("brands", "headingBrands"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    brands.append(clean(item.get("name") or item.get("brandName") or ""))
                elif isinstance(item, str):
                    brands.append(clean(item))
    address = data.get("address") if isinstance(data.get("address"), dict) else {}
    type_names = {
        "D": "Distributor",
        "M": "Manufacturer",
        "S": "Service Company",
        "C": "Custom Manufacturer",
    }
    other = []
    for code in data.get("otherActivities") or []:
        label = type_names.get(str(code).upper(), "")
        if label:
            other.append(label)
    return {
        "name": clean(data.get("name")),
        "description": clean(data.get("description")),
        "website": clean(data.get("website")),
        "phone": clean(data.get("primaryPhone")),
        "annual_sales": clean(data.get("annualSales")),
        "employees": clean(data.get("numberEmployees")),
        "year_founded": clean(str(data.get("yearFounded") or "")),
        "address": clean(address.get("address1")),
        "city": clean(address.get("city")),
        "state": clean(address.get("state")),
        "postal_code": clean(address.get("zip")),
        "primary_company_type": type_names.get(str(data.get("type") or "").upper(), ""),
        "additional_activities": " | ".join(unique(other)),
        "category_groups": groups,
        "products_services": families,
        "brands": unique(brands),
    }


def _result_cards(soup: BeautifulSoup):
    cards = soup.select("[data-sentry-component='SearchResultSupplier']")
    if cards:
        return cards
    container = soup.select_one("[class*='search-results_resultsList']")
    if container:
        return container.find_all("li", recursive=False)
    return []


def _result_count(soup: BeautifulSoup) -> tuple[int | None, int | None, int | None]:
    region = soup.select_one("[class*='search-results_pagination']")
    chunks = []
    if region:
        chunks.append(region.get_text(" ", strip=True))
    chunks.extend(clean(node.get_text(" ", strip=True)) for node in soup.select("small"))
    pattern = re.compile(r"(\d[\d,]*)\s+to\s+(\d[\d,]*)\s+of\s+(\d[\d,]*)", re.I)
    for chunk in chunks:
        match = pattern.search(chunk)
        if match:
            start, end, total = (int(part.replace(",", "")) for part in match.groups())
            return start, end, total
    return None, None, None


def _current_page(soup: BeautifulSoup) -> int | None:
    for button in soup.select("button[aria-label^='Results Page']"):
        kind = (button.get("kind") or "").lower()
        current = (button.get("aria-current") or "").lower()
        if kind == "primary-outline" or current in {"page", "true"}:
            match = re.search(r"(\d+)", button.get("aria-label") or "")
            if match:
                return int(match.group(1))
    return None


def _page_from_links(soup: BeautifulSoup) -> int | None:
    counts: dict[int, int] = {}
    for anchor in soup.select("a[href*='page_number=']"):
        match = re.search(r"page_number=(\d+)", anchor.get("href") or "")
        if not match:
            continue
        number = int(match.group(1))
        counts[number] = counts.get(number, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _page_size(page: SearchPage) -> int:
    if page.range_start == 1 and page.range_end:
        return max(1, page.range_end - page.range_start + 1)
    if page.per_page in FULL_PAGE_SIZES:
        return page.per_page
    return PAGE_SIZE


def _page_from_range(page: SearchPage) -> int | None:
    if not page.range_start:
        return None
    return ((page.range_start - 1) // _page_size(page)) + 1


def _has_next(soup: BeautifulSoup) -> bool:
    button = soup.select_one("button[aria-label='Next Results Page']")
    if button is None:
        return False
    if button.has_attr("disabled"):
        return False
    if (button.get("aria-disabled") or "").lower() == "true":
        return False
    return True


def _parse_card(card, position: int, page_number: int, search_term: str) -> dict:
    profile_url = ""
    for anchor in card.select("a[href*='/profile']"):
        href = anchor.get("href") or ""
        if "/company/" in href:
            profile_url = absolute_url(href)
            break
    slug, company_id = company_identity(profile_url)
    name_node = card.select_one("[data-testid='supplier-name-link']")
    name = clean(name_node.get_text(" ", strip=True)) if name_node else ""
    website = ""
    website_node = card.select_one("a[data-sentry-component='visitWebsiteButton']")
    if website_node and website_node.get("href"):
        website = website_node["href"].strip()
    if not website:
        for anchor in card.select("a[href^='http']"):
            href = (anchor.get("href") or "").strip()
            if href and "thomasnet.com" not in href:
                website = href
                break
    location_node = card.select_one("[data-testid='srp.supplier-location-link']")
    location = clean(location_node.get_text(" ", strip=True)) if location_node else _icon_value(card, "location-dot")
    description_node = card.select_one("[data-sentry-component='TrimmedDescription']")
    description = clean(description_node.get_text(" ", strip=True)) if description_node else ""
    tags = unique(
        node.get_text(" ", strip=True)
        for node in card.select("[data-sentry-component='TagWithCheck']")
    )
    return {
        "company_id": company_id,
        "slug": slug,
        "name": name,
        "profile_url": profile_url,
        "website": website,
        "location": location,
        "employees": _icon_value(card, "user-group"),
        "annual_sales": _icon_value(card, "landmark"),
        "year_founded": _icon_value(card, "calendar"),
        "company_types": clean_company_types(_icon_value(card, "industry")),
        "description_preview": description,
        "tags": tags,
        "page_number": page_number,
        "card_position": position,
        "search_term": search_term,
    }


def _icon_value(root, icon_name: str) -> str:
    icon = root.select_one(f"l-icon[name='{icon_name}']")
    if icon is None:
        return ""
    container = icon.find_parent("div")
    if container is None:
        return ""
    label = container.select_one("span.txt-label")
    if label is None:
        return ""
    return clean(label.get_text(" ", strip=True))


def _description(soup: BeautifulSoup) -> str:
    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = clean(heading.get_text(" ", strip=True)).lower()
        if "company description" in title:
            paragraph = heading.find_next("p")
            if paragraph:
                return clean(paragraph.get_text(" ", strip=True))
    paragraphs = [clean(node.get_text(" ", strip=True)) for node in soup.select("p[style*='pre-wrap']")]
    paragraphs = [item for item in paragraphs if item]
    if not paragraphs:
        return ""
    return max(paragraphs, key=len)


def _business_details(soup: BeautifulSoup) -> dict[str, str]:
    panel = soup.select_one("[data-sentry-component='BusinessDetailsSection']")
    if panel is None:
        for heading in soup.find_all(["h2", "h3"]):
            if clean(heading.get_text(" ", strip=True)).lower() == "business details":
                panel = heading.find_parent("l-panel") or heading.parent
                break
    if panel is None:
        return {}
    found: dict[str, str] = {}
    for label in panel.select("div.txt-label"):
        key = DETAIL_LABELS.get(clean(label.get_text(" ", strip=True)).rstrip(":").lower())
        if not key or key in found:
            continue
        sibling = label.find_next_sibling("ul")
        if sibling is None:
            continue
        if key == "website":
            anchor = sibling.select_one("a[href^='http']")
            found[key] = anchor["href"].strip() if anchor and anchor.get("href") else ""
            continue
        values = unique(item.get_text(" ", strip=True) for item in sibling.select("li"))
        found[key] = " | ".join(values)
    return found


def _products(soup: BeautifulSoup) -> tuple[list[dict], list[str], list[str]]:
    panel = soup.select_one("[data-sentry-component='CompanyProductsOffered']")
    if panel is None:
        panel = soup.select_one("[data-ref='company.productsOffered.section']")
    if panel is None:
        return [], [], []
    groups: list[dict] = []
    products: list[str] = []
    brands: list[str] = []
    categories_panel = panel.select_one("[aria-labelledby='companyProductsCategoriesTab']") or panel
    for heading in categories_panel.find_all("h4"):
        title = clean(heading.get_text(" ", strip=True))
        items = _items_until_next_heading(heading)
        lowered = title.lower()
        if "brand" in lowered:
            brands.extend(items)
        elif "product" in lowered or "service" in lowered:
            products.extend(items)
        else:
            for item in items:
                groups.append({"group": title or "Categories", "name": item})
    brands_panel = panel.select_one("[aria-labelledby='companyProductsBrandsTab']")
    if brands_panel is not None:
        brands.extend(list_items(brands_panel))
    return groups, unique(products), unique(brands)


def _items_until_next_heading(heading) -> list[str]:
    items: list[str] = []
    for sibling in heading.find_next_siblings():
        if getattr(sibling, "name", None) in {"h3", "h4"}:
            break
        items.extend(list_items(sibling))
    return unique(items)


def _href_value(soup: BeautifulSoup, prefix: str) -> str:
    anchor = soup.select_one(f"a[href^='{prefix}']")
    if anchor is None or not anchor.get("href"):
        return ""
    return anchor["href"].split(":", 1)[-1].strip()
