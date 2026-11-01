from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

SUPPLIER_FIELDS = [
    "company_id",
    "slug",
    "name",
    "profile_url",
    "website",
    "logo_url",
    "thomas_verified",
    "location",
    "address",
    "city",
    "state",
    "postal_code",
    "employees",
    "annual_sales",
    "year_founded",
    "primary_company_type",
    "additional_activities",
    "company_types",
    "description",
    "description_preview",
    "tags",
    "key_personnel",
    "diversity_status",
    "quality_certifications",
    "registration",
    "phone",
    "fax",
    "email",
    "categories",
    "products_services",
    "brands",
    "search_term",
    "search_url",
    "page_number",
    "card_position",
    "scraped_at",
]

LINK_FIELDS = [
    "company_id",
    "slug",
    "name",
    "profile_url",
    "website",
    "location",
    "page_number",
    "card_position",
    "search_term",
]

PRODUCT_FIELDS = [
    "company_id",
    "company_name",
    "profile_url",
    "website",
    "kind",
    "group",
    "name",
    "page_number",
    "scraped_at",
]


class Storage:
    def __init__(self, output_dir: str | Path):
        self.directory = Path(output_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.suppliers_path = self.directory / "suppliers.json"
        self.csv_path = self.directory / "suppliers.csv"
        self.products_path = self.directory / "products.json"
        self.products_csv_path = self.directory / "products.csv"
        self.links_path = self.directory / "profile_links.json"
        self.state_path = self.directory / "state.json"
        self._lock = threading.Lock()
        self.suppliers = _load_json(self.suppliers_path, [])
        self.products = _load_json(self.products_path, [])
        self.links = _load_json(self.links_path, [])
        self.state = _load_json(
            self.state_path,
            {
                "search_term": "",
                "updated_at": "",
                "total_results": 0,
                "last_page": 0,
                "completed_pages": [],
                "scraped_ids": [],
            },
        )
        self.ids = {str(item) for item in self.state.get("scraped_ids", []) if item}

    def has(self, company_id: str, profile_url: str = "") -> bool:
        if company_id and str(company_id) in self.ids:
            return True
        if profile_url:
            return any(row.get("profile_url") == profile_url for row in self.suppliers)
        return False

    def add_links(self, cards: list[dict]) -> None:
        with self._lock:
            by_key = {_link_key(row): row for row in self.links}
            for card in cards:
                row = {field: card.get(field, "") for field in LINK_FIELDS}
                by_key[_link_key(row)] = row
            self.links = list(by_key.values())
            _write_json(self.links_path, self.links)

    def save(self, supplier: dict, product: dict) -> None:
        with self._lock:
            company_id = str(supplier.get("company_id") or "")
            profile_url = supplier.get("profile_url") or ""
            self.suppliers = [
                row
                for row in self.suppliers
                if not _same_company(row, company_id, profile_url)
            ]
            self.suppliers.append(supplier)
            self.products = [
                row
                for row in self.products
                if not _same_company(row, company_id, profile_url)
            ]
            self.products.append(product)
            if company_id:
                self.ids.add(company_id)
            self.state["scraped_ids"] = sorted(self.ids)
            self.state["updated_at"] = _now()
            self.state["saved_suppliers"] = len(self.suppliers)
            self._flush_all()

    def mark_page(self, page: int, total_results: int | None, search_term: str) -> None:
        with self._lock:
            pages = {int(item) for item in self.state.get("completed_pages", [])}
            pages.add(int(page))
            self.state["completed_pages"] = sorted(pages)
            self.state["last_page"] = int(page)
            self.state["search_term"] = search_term
            if total_results:
                self.state["total_results"] = int(total_results)
            self.state["updated_at"] = _now()
            self.state["scraped_ids"] = sorted(self.ids)
            _write_json(self.state_path, self.state)

    def clear(self) -> None:
        with self._lock:
            self.suppliers = []
            self.products = []
            self.links = []
            self.ids = set()
            self.state = {
                "search_term": "",
                "updated_at": _now(),
                "total_results": 0,
                "last_page": 0,
                "completed_pages": [],
                "scraped_ids": [],
                "saved_suppliers": 0,
            }
            self._flush_all()

    def _flush_all(self) -> None:
        _write_json(self.suppliers_path, self.suppliers)
        _write_json(self.products_path, self.products)
        _write_json(self.links_path, self.links)
        _write_json(self.state_path, self.state)
        _write_csv(self.csv_path, SUPPLIER_FIELDS, [_csv_ready(row, SUPPLIER_FIELDS) for row in self.suppliers])
        _write_csv(self.products_csv_path, PRODUCT_FIELDS, _flatten_products(self.products))


def _same_company(row: dict, company_id: str, profile_url: str) -> bool:
    if company_id and str(row.get("company_id") or "") == company_id:
        return True
    return bool(profile_url) and row.get("profile_url") == profile_url


def _link_key(row: dict) -> str:
    return str(row.get("company_id") or row.get("profile_url") or "")


def _flatten_products(records: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for record in records:
        base = {
            "company_id": record.get("company_id", ""),
            "company_name": record.get("company_name", ""),
            "profile_url": record.get("profile_url", ""),
            "website": record.get("website", ""),
            "page_number": record.get("page_number", ""),
            "scraped_at": record.get("scraped_at", ""),
        }
        for item in record.get("categories") or []:
            if isinstance(item, dict):
                rows.append({**base, "kind": "category", "group": item.get("group", ""), "name": item.get("name", "")})
            else:
                rows.append({**base, "kind": "category", "group": "", "name": item})
        for name in record.get("products_services") or []:
            rows.append({**base, "kind": "product", "group": "All Products / Services", "name": name})
        for name in record.get("brands") or []:
            rows.append({**base, "kind": "brand", "group": "Brands Carried", "name": name})
    return rows


def _csv_ready(row: dict, fields: list[str]) -> dict:
    ready = {}
    for field in fields:
        value = row.get(field, "")
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                ready[field] = " | ".join(
                    item.get("name", "") for item in value if isinstance(item, dict)
                )
            else:
                ready[field] = " | ".join(str(item) for item in value)
        elif value is None:
            ready[field] = ""
        else:
            ready[field] = value
    return ready


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
