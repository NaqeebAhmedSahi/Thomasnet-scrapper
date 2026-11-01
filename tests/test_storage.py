import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.storage import Storage


class StorageTests(unittest.TestCase):
    def test_saves_supplier_products_and_links(self):
        with TemporaryDirectory() as folder:
            storage = Storage(folder)
            storage.add_links(
                [
                    {
                        "company_id": "30939814",
                        "slug": "seattle-surgical-repair-30939814",
                        "name": "Seattle Surgical Repair",
                        "profile_url": "https://www.thomasnet.com/company/seattle-surgical-repair-30939814/profile",
                        "website": "http://www.seattlesurgical.com/",
                        "location": "Seattle, WA 98133",
                        "page_number": 1,
                        "card_position": 1,
                        "search_term": "Surgical instruments distributor",
                    }
                ]
            )
            supplier = {
                "company_id": "30939814",
                "name": "Seattle Surgical Repair",
                "profile_url": "https://www.thomasnet.com/company/seattle-surgical-repair-30939814/profile",
                "website": "http://www.seattlesurgical.com/",
                "categories": ["Instruments: Surgical"],
                "products_services": ["Borescopes"],
                "brands": [],
                "tags": ["Surgical Instruments"],
            }
            product = {
                "company_id": "30939814",
                "company_name": "Seattle Surgical Repair",
                "profile_url": supplier["profile_url"],
                "website": supplier["website"],
                "page_number": 1,
                "categories": [{"group": "Instruments", "name": "Instruments: Surgical"}],
                "products_services": ["Borescopes"],
                "brands": [],
                "scraped_at": "2026-10-31T00:00:00+00:00",
            }
            storage.save(supplier, product)
            storage.mark_page(1, 2808, "Surgical instruments distributor")

            reloaded = Storage(folder)
            self.assertTrue(reloaded.has("30939814"))
            self.assertEqual(reloaded.suppliers[0]["name"], "Seattle Surgical Repair")
            self.assertEqual(reloaded.products[0]["products_services"], ["Borescopes"])
            self.assertEqual(reloaded.links[0]["page_number"], 1)
            self.assertEqual(reloaded.state["last_page"], 1)
            self.assertIn(1, reloaded.state["completed_pages"])

            with (Path(folder) / "suppliers.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["categories"], "Instruments: Surgical")
            self.assertEqual(rows[0]["products_services"], "Borescopes")

            with (Path(folder) / "products.csv").open(encoding="utf-8-sig", newline="") as handle:
                product_rows = list(csv.DictReader(handle))
            kinds = {row["kind"] for row in product_rows}
            self.assertEqual(kinds, {"category", "product"})
            self.assertTrue(any(row["name"] == "Borescopes" for row in product_rows))

            saved_json = json.loads((Path(folder) / "products.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_json[0]["categories"][0]["group"], "Instruments")

            reloaded.save(supplier, product)
            self.assertEqual(len(Storage(folder).suppliers), 1)

            reloaded.clear()
            emptied = Storage(folder)
            self.assertEqual(emptied.suppliers, [])
            self.assertEqual(emptied.products, [])
            self.assertFalse(emptied.has("30939814"))


if __name__ == "__main__":
    unittest.main()
