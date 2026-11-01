# Thomasnet supplier scraper

Scrapes a Thomasnet supplier search with Selenium and undetected Chrome. It walks the result pages in order. On each page it stores every company profile link, opens each profile, and writes the company record plus a separate product catalog.

The default search is [Surgical instruments distributor](https://www.thomasnet.com/suppliers/search?searchterm=Surgical%20instruments%20distributor).

## What gets saved

Each supplier file includes the company name, profile link, website, phone, street address, location, employees, annual sales, year founded, company type, description, tags, personnel, certifications, and the product names found on the profile.

Products are stored again on their own, grouped as categories, products / services, and brands.

| File | Contents |
| --- | --- |
| `data/suppliers.json` | One object per company |
| `data/suppliers.csv` | The same companies, one row each |
| `data/products.json` | One object per company, with grouped catalog names |
| `data/products.csv` | One row per category, product, or brand |
| `data/profile_links.json` | Profile links collected from each results page before those profiles are opened |
| `data/state.json` | Pages already finished, and company ids already saved |
| `logs/scraper.log` | Run log |

Files are rewritten after every company. You can stop a run and start it again. Saved companies are skipped unless you turn resume off.

A full result set is about 2,800 profiles. At a few seconds per profile, that run takes several hours. Use max pages or max suppliers while you confirm the output.

## Windows

Install Google Chrome and Python 3.10 or newer. When you install Python, check Add python.exe to PATH.

Unzip the project, open the folder, and double-click `setup_and_run.bat`.

The first run installs the Python packages, then opens the window. Later runs start the window directly.

Pick the Chrome profile on that Windows computer. A Chrome profile copied from Linux will not stay signed in on Windows.

## Setup

Google Chrome is required. These commands are run from the project folder on Linux.

```bash
cd /home/dev/WorkSpace/thomasnet
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Activate the environment again in every new terminal:

```bash
cd /home/dev/WorkSpace/thomasnet
source .venv/bin/activate
```

## GUI

```bash
python gui.py
```

The window lets you set the search term, start page, max pages, max new suppliers, delay, and output folder.

- **Start** opens Chrome and begins at the start page.
- **Pause** holds the run between steps. **Resume** continues it.
- **Stop** finishes the current step and leaves saved files in place.
- **Open output folder** opens `data` in the file browser.
- **Clear saved data** deletes the JSON and CSV files in the output folder.
- Double-click a company in the table to open its Thomasnet profile.
- Pick the Chrome profile that is signed in to Thomasnet. Start loads that profile. Your other Chrome windows stay open.
- **Sign in** opens the sign-in step in that scraper window only.

Leave max pages and max new suppliers blank to scrape every page. Keep **Skip suppliers already saved** checked to continue an interrupted run.

Headless Chrome stays off unless you check it. A visible window is more reliable if the site shows a browser check.

## Command line

```bash
python cli.py --help
```

Scrape the default surgical instruments search, all pages:

```bash
python cli.py --search "Surgical instruments distributor"
```

Try one page and one new profile:

```bash
python cli.py --search "Surgical instruments distributor" --max-pages 1 --max-suppliers 1
```

Scrape only page 2:

```bash
python cli.py --search "Surgical instruments distributor" --start-page 2 --max-pages 1
```

Scrape pages 2 and 3, then stop:

```bash
python cli.py --search "Surgical instruments distributor" --start-page 2 --max-pages 2
```

Hide Chrome, wait 3 seconds between profiles, and write to another folder:

```bash
python cli.py --search "Surgical instruments distributor" --headless --delay 3 --output data
```

Scrape companies again even when they are already in the output folder:

```bash
python cli.py --search "Surgical instruments distributor" --no-resume --max-pages 1 --max-suppliers 5
```

## How a run moves

1. Open `https://www.thomasnet.com/suppliers/search` for the search term and page number.
2. Read the supplier cards in the results list.
3. Append those profile links to `data/profile_links.json`.
4. Open each profile in a new tab.
5. Read the business description, business details, categories, products / services, and brands. View All lists are opened when they stay on that company.
6. Save the company to `suppliers.json` and `suppliers.csv`.
7. Save the catalog to `products.json` and `products.csv`.
8. Repeat on the next results page until the last page, the page limit, or the supplier limit.

Page 1 uses the search URL. Later pages add `pg`. Thomasnet asks for a sign-in before those pages. Click Sign in in the scraper window if the register box appears. Your other Chrome windows stay open. The login stays in the scraper Chrome, and scraping continues when the supplier list appears.

## Checks

```bash
python -m unittest tests.test_parser tests.test_storage tests.test_chrome_tabs
```

## If Chrome does not start

The scraper matches ChromeDriver to the installed Chrome version and caches it in `.cache/drivers`. The first launch needs a network connection for that download.

If Chrome still fails, delete the cache and try a visible window:

```bash
rm -rf .cache/drivers
python cli.py --search "Surgical instruments distributor" --max-pages 1 --max-suppliers 1
```

If the results page is empty, open `logs/search_empty.html` or `logs/blocked.html`. Run again with the Chrome window visible. Do not use `--headless` for that attempt.

The log file is `logs/scraper.log`.
