import json
import unittest

from app.config import build_search_url
from app.parser import parse_profile_page, parse_search_page
from app.scraper import _build_records

SEARCH_HTML = """
<div class="search-results_paginationAdvanced__7Plnk">
  <small>1 to 25 of 2808</small>
  <button aria-label="Previous Results Page" disabled></button>
  <button aria-label="Results Page 1" kind="primary-outline">1</button>
  <button aria-label="Results Page 2" kind="neutral-text">2</button>
  <button aria-label="Next Results Page"></button>
</div>
<ul class="search-results_resultsList__m_2kX">
  <li data-sentry-component="SearchResultSupplier">
    <button data-testid="supplier-name-link">Seattle Surgical Repair<span title="Validated"></span></button>
    <a href="/company/seattle-surgical-repair-30939814/profile?searchterm=Surgical+instruments+distributor&amp;page_number=1">View Profile</a>
    <a data-sentry-component="visitWebsiteButton" href="http://www.seattlesurgical.com/">Visit Website</a>
    <div class="flex">
      <l-icon name="location-dot"></l-icon>
      <span class="txt-label"><a data-testid="srp.supplier-location-link"><span>Seattle, WA 98133</span></a></span>
    </div>
    <div class="flex">
      <l-icon name="user-group"></l-icon>
      <span class="txt-label">10-49</span>
    </div>
    <div class="flex">
      <l-icon name="landmark"></l-icon>
      <span class="txt-label">$5M-$9.9M</span>
    </div>
    <div class="flex">
      <l-icon name="calendar"></l-icon>
      <span class="txt-label">1962</span>
    </div>
    <div class="flex">
      <l-icon name="industry"></l-icon>
      <span class="txt-label">Distributor* • Service Company</span>
    </div>
    <div data-sentry-component="TrimmedDescription">
      <p>Distributor of surgical instruments and medical equipment. more+</p>
    </div>
    <div data-sentry-component="TagWithCheck"><span>Surgical Instruments</span></div>
    <div data-sentry-component="TagWithCheck"></div>
  </li>
</ul>
"""

PROFILE_HTML = """
<h1>Seattle Surgical Repair</h1>
<div>
  <h3>Business Description</h3>
  <h3>Company Description by Thomasnet</h3>
  <p style="white-space:pre-wrap">Distributor of surgical instruments and medical equipment. Products include general, orthopedic, neurosurgical, cardiovascular, and thoracic surgical instruments. Secondary services include surgical instrument repair.</p>
</div>
<l-panel data-sentry-component="CompanyProductsOffered">
  <h3>Products / Services Offered</h3>
  <div role="tabpanel" aria-labelledby="companyProductsCategoriesTab">
    <h4>Instruments</h4>
    <div>
      <ul>
        <li><a>Instruments: Surgical</a></li>
        <li><a>Instruments: Surgical</a></li>
        <li><a>Instruments: Orthopedic</a></li>
      </ul>
    </div>
    <h4>All Products / Services</h4>
    <div>
      <ul>
        <li><a>Borescopes</a></li>
        <li><a>Containers</a></li>
        <li><a>Fiber Optic Cable and Cable Assemblies</a></li>
        <li><a>Surgical Equipment</a></li>
      </ul>
    </div>
  </div>
  <div role="tabpanel" hidden aria-labelledby="companyProductsBrandsTab">
    <h4>All Brands Carried</h4>
    <small>No brands carried</small>
  </div>
</l-panel>
<l-panel data-sentry-component="BusinessDetailsSection">
  <h3>Business Details</h3>
  <div>
    <div class="txt-label">Website</div>
    <ul>
      <li><a href="http://www.seattlesurgical.com/">Homepage</a></li>
      <li><p>Instruments: Surgical</p></li>
    </ul>
  </div>
  <div>
    <div class="txt-label">Primary Company Type</div>
    <ul><li><p>Distributor</p></li></ul>
  </div>
  <div>
    <div class="txt-label">Additional Activities</div>
    <ul><li><p>Service Company</p></li></ul>
  </div>
  <div>
    <div class="txt-label">Key Personnel</div>
    <ul><li>Not available</li></ul>
  </div>
  <div>
    <div class="txt-label">Diverse / Small Bus. Status</div>
    <ul><li><p>Not available</p></li></ul>
  </div>
  <div>
    <div class="txt-label">Quality Certifications</div>
    <ul><li><p>Not available</p></li></ul>
  </div>
  <div>
    <div class="txt-label">Registration</div>
    <ul><li><p>Not available</p></li></ul>
  </div>
  <div>
    <div class="txt-label">Annual Sales:</div>
    <ul><li><p>$5 - 9.9 Mil</p></li></ul>
  </div>
  <div>
    <div class="txt-label">No of Employees:</div>
    <ul><li><p>10-49</p></li></ul>
  </div>
  <div>
    <div class="txt-label">Year Founded:</div>
    <ul><li><p>1962</p></li></ul>
  </div>
</l-panel>
<a href="tel:+1-206-555-0100">Call</a>
"""


class ParserTests(unittest.TestCase):
    def test_search_url(self):
        first = build_search_url("Surgical instruments distributor", 1)
        second = build_search_url("Surgical instruments distributor", 2)
        self.assertIn("searchterm=Surgical+instruments+distributor", first)
        self.assertNotIn("pg=", first)
        self.assertIn("pg=2", second)

    def test_search_card(self):
        page = parse_search_page(SEARCH_HTML, 1, "Surgical instruments distributor")
        self.assertEqual(page.range_start, 1)
        self.assertEqual(page.range_end, 25)
        self.assertEqual(page.total_results, 2808)
        self.assertEqual(page.total_pages, 113)
        self.assertEqual(page.current_page, 1)
        self.assertTrue(page.has_next)
        self.assertEqual(len(page.cards), 1)
        card = page.cards[0]
        self.assertEqual(card["name"], "Seattle Surgical Repair")
        self.assertEqual(card["company_id"], "30939814")
        self.assertIn("/company/seattle-surgical-repair-30939814/profile", card["profile_url"])
        self.assertTrue(card["profile_url"].startswith("https://www.thomasnet.com/"))
        self.assertEqual(card["website"], "http://www.seattlesurgical.com/")
        self.assertEqual(card["location"], "Seattle, WA 98133")
        self.assertEqual(card["employees"], "10-49")
        self.assertEqual(card["annual_sales"], "$5M-$9.9M")
        self.assertEqual(card["year_founded"], "1962")
        self.assertEqual(card["company_types"], "Distributor | Service Company")
        self.assertEqual(card["tags"], ["Surgical Instruments"])
        self.assertNotIn("more+", card["description_preview"])
        self.assertIn("surgical instruments", card["description_preview"])

    def test_profile_and_merged_record(self):
        profile = parse_profile_page(PROFILE_HTML)
        self.assertIn("Secondary services include surgical instrument repair.", profile["description"])
        self.assertEqual(profile["categories"], ["Instruments: Surgical", "Instruments: Orthopedic"])
        self.assertEqual(
            profile["products_services"],
            ["Borescopes", "Containers", "Fiber Optic Cable and Cable Assemblies", "Surgical Equipment"],
        )
        self.assertEqual(profile["brands"], [])
        self.assertEqual(profile["website"], "http://www.seattlesurgical.com/")
        self.assertEqual(profile["primary_company_type"], "Distributor")
        self.assertEqual(profile["additional_activities"], "Service Company")
        self.assertEqual(profile["annual_sales"], "$5 - 9.9 Mil")
        self.assertEqual(profile["employees"], "10-49")
        self.assertEqual(profile["year_founded"], "1962")
        self.assertEqual(profile["phone"], "+1-206-555-0100")

        card = parse_search_page(SEARCH_HTML, 1, "Surgical instruments distributor").cards[0]
        supplier, product = _build_records(card, profile, "Surgical instruments distributor")
        self.assertEqual(supplier["annual_sales"], "$5 - 9.9 Mil")
        self.assertEqual(supplier["city"], "Seattle")
        self.assertEqual(supplier["state"], "WA")
        self.assertEqual(supplier["postal_code"], "98133")
        self.assertEqual(supplier["primary_company_type"], "Distributor")
        self.assertIn("Borescopes", product["products_services"])
        self.assertEqual(product["categories"][0]["group"], "Instruments")
        self.assertEqual(product["brands"], [])


class EmbeddedCatalogTests(unittest.TestCase):
    def test_next_data_catalog_replaces_short_visible_list(self):
        payload = {
            "props": {
                "pageProps": {
                    "data": {
                        "name": "Seattle Surgical Repair",
                        "primaryPhone": "(206) 365-2000",
                        "type": "D",
                        "otherActivities": ["S"],
                        "address": {
                            "address1": "10726 Aurora Ave N",
                            "city": "Seattle",
                            "state": "WA",
                            "zip": "98133",
                        },
                        "families": [{"name": "Borescopes"}, {"name": "Instruments"}],
                        "headings": [
                            {"name": "Endoscopes", "familyName": "Borescopes"},
                            {"name": "Instruments: Surgical", "familyName": "Instruments"},
                            {"name": "Instruments: Surgical", "familyName": "Surgical Equipment"},
                        ],
                        "brands": None,
                    }
                }
            }
        }
        html = (
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
            "<l-panel data-sentry-component='CompanyProductsOffered'>"
            "<div role='tabpanel' aria-labelledby='companyProductsCategoriesTab'>"
            "<h4>All Products / Services</h4><ul><li><a>Should Not Win</a></li></ul>"
            "</div></l-panel>"
        )
        profile = parse_profile_page(html)
        self.assertEqual(profile["products_services"], ["Borescopes", "Instruments"])
        self.assertEqual(profile["phone"], "(206) 365-2000")
        self.assertEqual(profile["address"], "10726 Aurora Ave N")
        self.assertEqual(profile["primary_company_type"], "Distributor")
        self.assertEqual(profile["additional_activities"], "Service Company")
        self.assertEqual(
            profile["category_groups"],
            [
                {"group": "Borescopes", "name": "Endoscopes"},
                {"group": "Instruments", "name": "Instruments: Surgical"},
                {"group": "Surgical Equipment", "name": "Instruments: Surgical"},
            ],
        )
        self.assertNotIn("Should Not Win", profile["products_services"])


class HeaderTests(unittest.TestCase):
    def test_header_logo_phone_website_and_verified_badge(self):
        html = """
        <h1>Walter Stern, Inc.</h1>
        <img alt="Walter Stern, Inc. Company Logo" src="/_sda/_next/image?url=https%3A%2F%2Fcdn.thomasnet.com%2Fccp%2F00423974%2F226892.png&w=256&q=75">
        <div data-sentry-component="ThomasPartnerBadge"><button>Thomas Verified</button></div>
        <div data-sentry-component="SupplierDetails">
          <div>Distributor</div>
          <a data-testid="srp.supplier-location-link">Port Washington, NY 11050</a>
        </div>
        <dialog>
          <a href="tel:(516) 883-9100">(516) 883-9100</a>
          <a href="https://www.waltersterninc.com/index.html">Visit Website</a>
        </dialog>
        """
        profile = parse_profile_page(html)
        self.assertEqual(profile["phone"], "(516) 883-9100")
        self.assertEqual(profile["website"], "https://www.waltersterninc.com/index.html")
        self.assertEqual(profile["logo_url"], "https://cdn.thomasnet.com/ccp/00423974/226892.png")
        self.assertEqual(profile["thomas_verified"], "Yes")
        self.assertEqual(profile["primary_company_type"], "Distributor")
        self.assertEqual(profile["header_location"], "Port Washington, NY 11050")


class NextPageTests(unittest.TestCase):
    def test_last_page_has_no_next(self):
        html = (
            SEARCH_HTML.replace(
                '<button aria-label="Next Results Page"></button>',
                '<button aria-label="Next Results Page" disabled></button>',
            )
            .replace("1 to 25 of 2808", "2801 to 2808 of 2808")
            .replace(
                '<button aria-label="Results Page 1" kind="primary-outline">1</button>',
                '<button aria-label="Results Page 113" kind="primary-outline">113</button>',
            )
        )
        page = parse_search_page(html, 113, "Surgical instruments distributor")
        self.assertFalse(page.has_next)
        self.assertEqual(page.current_page, 113)
        self.assertEqual(page.total_pages, 113)


if __name__ == "__main__":
    unittest.main()
