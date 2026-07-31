"""
Commercial Plus Group — car wash M&A / business brokerage scraper.

Commercial Plus, LLC runs a dedicated "Car Wash M&A" brokerage line (licensed
brokers Georgina Adkins / AZ Lic. #BR661617000 and cooperating state-licensed
brokers) alongside gas station / c-store brokerage, publishing its own
property listings at commercialplus.com/properties/. This scraper only keeps
listings whose title contains "car wash" (filters out their gas station / c-
store / QSR listings) AND that describe an operating business (filters out
raw "development site" / vacant land offerings, which are land, not a
business for sale).

Source: https://commercialplus.com/properties/
Output: output/commercialplus_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, polite_delay, parse_price, clean_text, html_to_text,
                   parse_location, parse_location_prose)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("commercialplus")

BASE_URL = "https://commercialplus.com"
LISTINGS_URL = "{}/properties/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "commercialplus_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "business_type", "description", "broker_name", "listing_url",
    "bays", "sqft", "listing_code",
]

DEV_SITE_MARKERS = ("development site", "development opportunity", "vacant land",
                     "entitled", "site plan")


def infer_business_type(text: str) -> str:
    t = text.lower()
    if "tunnel" in t:
        return "Tunnel/Conveyor"
    if "self-serve" in t or "self serve" in t:
        return "Self-Serve"
    if "in-bay" in t or "mini express" in t or "two-bay" in t or "2-bay" in t:
        return "In-Bay Automatic"
    if "full service" in t or "full-service" in t or "flex service" in t:
        return "Full-Service"
    if "express" in t:
        return "Express Exterior"
    return "Other"


def collect_car_wash_links(session) -> List[Dict[str, str]]:
    polite_delay(1.0, 2.0)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        if resp.status_code != 200:
            logger.info("Properties index -> HTTP %d", resp.status_code)
            return []
    except Exception as e:
        logger.warning("Properties index failed: %s", e)
        return []

    found = []
    seen = set()
    for m in re.finditer(
        r'<a[^>]+href="(https://commercialplus\.com/[a-z0-9-]+_[a-zA-Z0-9-]+/)"[^>]*>(.*?)</a>',
        resp.text, re.S,
    ):
        href, raw_txt = m.group(1), m.group(2)
        txt = clean_text(re.sub(r"<[^>]+>", " ", raw_txt))
        if not txt or "car wash" not in txt.lower():
            continue
        if href in seen:
            continue
        seen.add(href)
        found.append({"url": href, "title": txt})
    return found


def parse_detail(session, link: Dict[str, str]) -> Optional[Dict]:
    url, title = link["url"], link["title"]
    polite_delay(1.5, 3.0)
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None
    except Exception as e:
        logger.warning("Detail failed %s: %s", url, e)
        return None

    text = html_to_text(resp.text)

    if any(m in title.lower() or m in text.lower()[:1500] for m in DEV_SITE_MARKERS):
        logger.info("Skipping development-site (non-operating) listing: %s", title)
        return None

    slug = url.rstrip("/").rsplit("/", 1)[-1]

    # Address line: "<street>, <City>, <ST> <zip>" appears near the top of the
    # body copy right after the title, when a structured street address is
    # published. Some listings (e.g. confidential-address opportunities)
    # instead only name the city/state in prose ("...car wash in Graham,
    # Texas."), so fall back to that before giving up.
    city, state = "", ""
    m = re.search(r"\b\d{1,6}[^,\n]{0,60},\s*([A-Za-z .'-]+),\s*([A-Z]{2})\b", text)
    if m:
        city, state = m.group(1).strip(), m.group(2)
    if not state:
        city, state = parse_location_prose(text[:2000])
    if not state:
        city, state = parse_location(title)

    # Asking price: a standalone $X,XXX,XXX (>= 7 digits incl. commas) figure.
    asking_price = None
    m = re.search(r"\$\d{1,3}(?:,\d{3}){2,}", text)
    if m:
        asking_price = parse_price(m.group())

    # Annual revenue: "$778K in total wash sales" / "2025 Revenue: $778K" style.
    annual_revenue = None
    m = re.search(r"(\$[\d,.]+\s*[KkMm]?)\s+in\s+(?:total\s+)?wash\s+sales", text)
    if not m:
        m = re.search(r"revenue[:\s]+(\$[\d,.]+\s*[KkMm]?)", text, re.I)
    if m:
        annual_revenue = parse_price(m.group(1))

    # Broker — "<Name> | Commercial Plus, LLC"
    broker_name = "Commercial Plus, LLC"
    m = re.search(r"([A-Z][a-zA-Z.'-]+ [A-Z][a-zA-Z.'-]+)\s*\|\s*Commercial Plus, LLC", text)
    if m:
        broker_name = m.group(1).strip()

    # Bays — "two-bay" / "2-bay" style callouts
    bays = None
    m = re.search(r"\b(\w+|\d+)-bay\b", title + " " + text[:1500], re.I)
    if m:
        word = m.group(1).lower()
        WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        bays = WORDS.get(word) or (int(word) if word.isdigit() else None)

    # The real body copy always opens with "...is pleased to present..."; the
    # raw page text before that is nav/breadcrumb chrome, not a description.
    description = ""
    m = re.search(r"is pleased to present\s*(.{50,650})", text, re.S)
    if m:
        description = clean_text(m.group(1))
    business_type = infer_business_type(title + " " + text[:1500])

    return {
        "source_id": "cpg-{}".format(slug),
        "title": title,
        "city": city,
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "business_type": business_type,
        "description": description,
        "broker_name": broker_name,
        "listing_url": url,
        "bays": bays,
        "sqft": None,
        "listing_code": slug[-24:],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Commercial Plus Group car wash listing links...")
    links = collect_car_wash_links(session)
    logger.info("Found %d car-wash-titled listings; parsing details...", len(links))

    all_listings = []
    seen = set()
    for i, link in enumerate(links, 1):
        row = parse_detail(session, link)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            all_listings.append(row)
            logger.info("  [%d/%d] %s — %s, %s", i, len(links),
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
