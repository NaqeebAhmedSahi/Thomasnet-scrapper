from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
BASE_URL = "https://www.thomasnet.com"
DEFAULT_SEARCH = "Surgical instruments distributor"


@dataclass
class Job:
    search_term: str = DEFAULT_SEARCH
    start_page: int = 1
    max_pages: int = 0
    max_suppliers: int = 0
    delay: float = 2.5
    headless: bool = False
    output_dir: str = str(DEFAULT_OUTPUT)
    resume: bool = True
    chrome_profile: str = ""
    chrome_tab_title: str = ""
    chrome_tab_url: str = ""


def build_search_url(search_term: str, page: int) -> str:
    from urllib.parse import urlencode

    params = {"searchterm": search_term}
    if page > 1:
        params["pg"] = str(page)
    return f"{BASE_URL}/suppliers/search?{urlencode(params)}"
