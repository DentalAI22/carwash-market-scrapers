"""
Calder Capital — Midwest-focused M&A business brokerage scraper (car wash
industry vertical: caldergr.com/car-wash-business-m-a-business-broker/).

Calder runs a single combined "Businesses for Sale" board across all industry
verticals they broker (construction, manufacturing, service — including a
dedicated Car Wash practice). There is no per-industry listings URL, so this
scraper reads the live board at caldergr.com/businesses-for-sale/ and keeps
only listings whose title identifies them as a car wash business (their own
listings are consistently titled "Project <Name>: <Industry>" — car wash
listings currently read "... Car Wash Platform" / "... Car Wash Business" per
Calder's naming convention). Everything else on the board (manufacturing,
construction, staffing, etc.) is skipped — this scraper is car-wash-only by
design, matching the site brief.

Source: https://caldergr.com/businesses-for-sale/
Output: output/caldercapital_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from utils import get_session, polite_delay, parse_price, clean_text, html_to_text

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("caldercapital")

BASE_URL = "https://caldergr.com"
LISTINGS_URL = "{}/businesses-for-sale/".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "caldercapital_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "business_type", "description", "broker_name", "listing_url",
    "bays", "sqft", "listing_code",
]


def collect_car_wash_links(session) -> List[Dict[str, str]]:
    polite_delay(1.0, 2.0)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        if resp.status_code != 200:
            logger.info("Businesses-for-sale board -> HTTP %d", resp.status_code)
            return []
    except Exception as e:
        logger.warning("Businesses-for-sale board failed: %s", e)
        return []

    found = []
    seen = set()
    for m in re.finditer(
        r'href="(https://www\.caldergr\.com/business_listing/\d+-[a-z0-9-]+/)"',
        resp.text,
    ):
        href = m.group(1)
        if href in seen:
            continue
        seen.add(href)
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if "car-wash" not in slug and "carwash" not in slug:
            continue
        found.append({"url": href})
    return found


def parse_detail(session, link: Dict[str, str]) -> Optional[Dict]:
    url = link["url"]
    polite_delay(1.5, 3.0)
    try:
        # A bare requests UA gets a Cloudflare 406 on this host even with the
        # shared browser UA header; www + full Accept headers avoid it. If it
        # still fails, this individual listing is skipped, not fabricated.
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            logger.warning("Detail %s -> HTTP %d", url, resp.status_code)
            return None
    except Exception as e:
        logger.warning("Detail failed %s: %s", url, e)
        return None

    text = html_to_text(resp.text)

    # The <title> tag reads "For Sale - Project Suds: Car Wash Platform"; grab
    # it specifically (not the later in-body "For Sale" heading, which is
    # immediately followed by the region word with no separator).
    m = re.search(r"For Sale\s*[-–]\s*(.+?)\n", text)
    title = clean_text(m.group(1)) if m else "Car Wash Platform"

    slug = url.rstrip("/").rsplit("/", 1)[-1]

    # Calder's "Business Details" block reads "Location: <region> Facilities
    # and Assets: ..." with no delimiter between the region and the next
    # field label, so anchor on that label; fall back to just the first word
    # if the template differs (safer than over-capturing into the next field).
    m = re.search(r"Location:\s*([A-Za-z][a-zA-Z .,'-]{0,30}?)\s+Facilities and Assets:", text)
    if not m:
        m = re.search(r"Location:\s*([A-Za-z]+)", text)
    region = clean_text(m.group(1)) if m else ""
    # Calder's confidential-portfolio listings often disclose only a region
    # ("Midwest") rather than a specific city/state — never guess a state.
    city, state = (region, "") if region and region not in ("", "N/A") else ("", "")

    asking_price = None
    m = re.search(r"Asking Price:\s*(\$[\d,.]+\s*[KkMm]?)", text)
    if m:
        asking_price = parse_price(m.group(1))

    annual_revenue = None
    m = re.search(r"Revenue:\s*(\$[\d,.]+)", text)
    if m:
        annual_revenue = parse_price(m.group(1))

    cash_flow = None
    m = re.search(r"Cash Flow:\s*(\$[\d,.]+)", text)
    if m:
        cash_flow = parse_price(m.group(1))

    desc_bits = []
    # Anchor on the body description's actual opening line, not the <title>
    # tag (which also contains "Car Wash Platform" and would otherwise pull
    # in the intervening site-nav menu text instead of the real description).
    m = re.search(r"Based in the Midwest,\s*(.+?)Buyers will be required", text, re.S)
    if m:
        desc_bits.append(clean_text(m.group(1))[:600])
    if cash_flow:
        desc_bits.append("Cash flow: ${:,}".format(cash_flow))
    description = " ".join(desc_bits)[:700]

    business_type = "Combo" if "express and full" in text.lower() else "Other"

    return {
        "source_id": "calder-{}".format(slug),
        "title": title or "Car Wash Platform",
        "city": city,
        "state": state,
        "asking_price": asking_price,
        "annual_revenue": annual_revenue,
        "business_type": business_type,
        "description": description,
        "broker_name": "Calder Capital",
        "listing_url": url,
        "bays": None,
        "sqft": None,
        "listing_code": slug[:24],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Calder Capital car wash listing links...")
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
