"""
Car Wash Brokers Inc — dedicated car wash business brokerage scraper.

CWB is a specialist car wash M&A/business brokerage (Roger Pencek, President
& Broker; John Mitchell, agent) operating a WordPress "Luxus" real-estate
listing theme at carwashbrokers.com. Every listing is CWB's own — this is a
niche specialist broker's own site (same category as Car Wash Advisory named
in the site brief), not a multi-broker aggregator.

Structure (confirmed by hand 2026-07-30):
  - Detail links: https://www.carwashbrokers.com/?property=<slug>
  - Detail page: .address (first match = street address), .price-area .price,
    .main-features li .single-feature (p=label, span=value) for Type/Build/
    Size/Lot Size, .text ul.wp-block-list li for description bullets,
    .agent-info h6.name / li Email for the listing agent.

Source: https://www.carwashbrokers.com/property-listing/
Output: output/carwashbrokers_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import get_session, polite_delay, parse_price, clean_text, parse_location

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("carwashbrokers")

BASE_URL = "https://www.carwashbrokers.com"
LISTINGS_URL = "{}/property-listing/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "carwashbrokers_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "business_type", "description", "broker_name", "listing_url",
    "bays", "sqft", "listing_code",
]

# Non-listing pages that share the ?property= URL scheme on this theme
# (the brokerage's own "about us" profile page). Also excludes listings whose
# own address field and title/URL slug name CONFLICTING locations (a source
# data-quality issue, not something to guess-resolve) — better to skip than
# risk publishing a wrong state.
SKIP_SLUGS = {
    "car-wash-brokers-inc",
    # Title/slug say "DFW Texas"; the page's own address field instead reads
    # "1629 Western Center Rd, North Phoenix, United States" with no state
    # given. Conflicting, unresolvable from this page alone — excluded.
    "express-carwash-dfw-texas",
}

# Raw land / development sites are not operating car wash businesses — the
# site only carries operating businesses for sale, so these are filtered.
LAND_ONLY_MARKERS = ("vacant land", "entitled")


def infer_business_type(title: str, body: str) -> str:
    t = (title + " " + body).lower()
    if "tunnel" in t:
        return "Tunnel/Conveyor"
    if "self-serve" in t or "self serve" in t:
        return "Self-Serve"
    if "in-bay" in t or "in bay" in t:
        return "In-Bay Automatic"
    if "full service" in t or "full-service" in t:
        return "Full-Service"
    if "express" in t:
        return "Express Exterior"
    return "Other"


def collect_detail_urls(session) -> List[str]:
    urls = []
    seen = set()
    for page_url in (LISTINGS_URL, BASE_URL + "/"):
        polite_delay(1.0, 2.0)
        try:
            resp = session.get(page_url, timeout=30)
            if resp.status_code != 200:
                logger.info("%s -> HTTP %d, skipping.", page_url, resp.status_code)
                continue
        except Exception as e:
            logger.warning("%s failed: %s", page_url, e)
            continue
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "?property=" not in href:
                continue
            slug = href.split("?property=")[-1].split("&")[0].strip("/")
            if slug in SKIP_SLUGS or href in seen:
                continue
            seen.add(href)
            urls.append(href)
    return urls


def parse_detail(session, url: str) -> Optional[Dict]:
    polite_delay(1.5, 3.0)
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
    except Exception as e:
        logger.warning("Detail failed %s: %s", url, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    title_tag = soup.find("title")
    title = clean_text(title_tag.get_text()).split("–")[0].strip() if title_tag else ""
    if not title:
        return None

    slug = url.split("?property=")[-1].split("&")[0].strip("/")
    if slug in SKIP_SLUGS:
        return None

    # Description — CWB renders this either as <ul><li>...</li></ul> bullets
    # or as a single <p> with <br/> line breaks depending on the listing, so
    # split on the tag's own line-break rendering rather than only <li>.
    desc_bits = []
    text_block = soup.select_one(".property-description .text")
    if text_block:
        for line in text_block.get_text("\n", strip=True).split("\n"):
            t = clean_text(line).lstrip("•").strip()
            if t:
                desc_bits.append(t)
    description = " ".join(desc_bits)[:600]

    if any(m in (title + " " + description).lower() for m in LAND_ONLY_MARKERS):
        logger.info("Skipping land-only listing: %s", title)
        return None

    # Address — the FIRST .address on a single-property page is the property's
    # own street address (later .address matches belong to "Related
    # Properties" sidebar cards), so take the first only.
    addr_el = soup.select_one(".property-single .address") or soup.select_one(".address")
    address = clean_text(addr_el.get_text()) if addr_el else ""
    city, state = parse_location(address)
    if not state:
        city, state = parse_location(title)

    # Price
    price_el = soup.select_one(".price-area .price")
    asking_price = parse_price(price_el.get_text()) if price_el else None

    # Type / Build / Size / Lot Size feature list
    sqft = None
    biz_type_raw = ""
    for feat in soup.select(".main-features .single-feature"):
        label_el = feat.find("p")
        value_el = feat.find("span")
        if not label_el or not value_el:
            continue
        label = clean_text(label_el.get_text()).lower()
        value = clean_text(value_el.get_text())
        if label == "type":
            biz_type_raw = value
        elif label == "size":
            m = re.search(r"([\d,]+)", value)
            if m:
                sqft = int(m.group(1).replace(",", ""))

    business_type = infer_business_type(title + " " + biz_type_raw, description)

    # Agent
    agent_el = soup.select_one(".agent-info h6.name")
    broker_name = clean_text(agent_el.get_text()) if agent_el else "Car Wash Brokers Inc"

    # Revenue — CWB bullets often read "2025 Gross Sales $336K+-" etc.
    annual_revenue = None
    m = re.search(r"(?:gross sales|est\.?\s*gross sales|revenue)\D{0,15}(\$[\d,.]+\s*[KkMm]?)",
                  description, re.I)
    if m:
        annual_revenue = parse_price(m.group(1))

    return {
        "source_id": "cwb-{}".format(slug),
        "title": title,
        "city": city,
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "business_type": business_type,
        "description": description,
        "broker_name": broker_name,
        "listing_url": url,
        "bays": None,
        "sqft": sqft,
        "listing_code": slug[:40],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Car Wash Brokers Inc detail URLs...")
    urls = collect_detail_urls(session)
    logger.info("Found %d unique listing URLs; parsing details...", len(urls))

    all_listings = []
    seen = set()
    for i, url in enumerate(urls, 1):
        row = parse_detail(session, url)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            all_listings.append(row)
            logger.info("  [%d/%d] %s — %s, %s", i, len(urls),
                        row["listing_code"], row["city"] or "?", row["state"] or "?")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
