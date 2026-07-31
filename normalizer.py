#!/usr/bin/env python3
"""
Car wash listings normalizer.

Ported from the veterinary normalizer pattern. Reads every
output/<source>_raw.csv, maps each row to the site's Listing schema (mirrors
~/market-network/carwash/src/lib/types.ts), assigns a persistent TCWM-XXXXX
siteId from site_id_registry.json (never renumbers, never collides with any
other vertical's prefix), dedupes within + across sources, and writes:
  - listings.json                       (canonical, this dir)
  - ../carwash/public/data/listings.json (site consumer, LOCAL dev only)

Schema (per Listing interface):
  id, source, source_url, type, state, city, asking_price, annual_revenue,
  annual_collections, key_metric_value, broker_name, broker_company,
  broker_url, description, business_name_redacted, scraped_date, is_new
  (+ siteId, broker_ref, bays)

No minimum listing count — per JI's explicit ruling, this dataset silos
every REAL, verified, currently-listed car wash found. If a source has zero
qualifying listings today, it contributes zero rows; nothing is padded.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("normalizer")

HERE = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(HERE, "output")
BROKER_CODES_JSON = os.path.join(HERE, "broker_codes.json")
SITE_ID_REGISTRY = os.path.join(HERE, "site_id_registry.json")
LISTINGS_JSON = os.path.join(HERE, "listings.json")

# Site consumer(s) — LOCAL dev convenience only; in CI (GitHub Actions) the
# ../carwash checkout doesn't exist, and the site pulls listings.json from
# this repo's public raw URL at build time (see scrape-carwash.yml +
# ../carwash/scripts/fetch-listings.mjs).
SITE_DATA_TARGETS = [
    os.path.join(HERE, "..", "carwash", "public", "data", "listings.json"),
]

SITE_PREFIX = "TCWM"
BASE_SITE_ID = 1  # TCWM-00001 is the first

_codes = None


def load_codes() -> Dict:
    global _codes
    if _codes is None:
        with open(BROKER_CODES_JSON) as f:
            _codes = json.load(f)
    return _codes


def to_int(v) -> Optional[int]:
    if v in (None, "", "None"):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# --- siteId registry (persistent, stable, never renumber) -------------------

def load_registry():
    if os.path.exists(SITE_ID_REGISTRY):
        with open(SITE_ID_REGISTRY) as f:
            d = json.load(f)
        return d.get("next_id", BASE_SITE_ID), d.get("map", {})
    return BASE_SITE_ID, {}


def save_registry(next_id: int, id_map: Dict) -> None:
    with open(SITE_ID_REGISTRY, "w") as f:
        json.dump({"prefix": SITE_PREFIX, "base": BASE_SITE_ID,
                   "next_id": next_id, "map": id_map}, f, indent=2)


def assign_site_ids(listings: List[Dict]) -> None:
    """Assign stable TCWM-XXXXX siteIds keyed by source_id (registry-backed)."""
    next_id, id_map = load_registry()
    used = set(id_map.values())
    for l in listings:
        key = l["source_id"]
        if key in id_map:
            num = id_map[key]
        else:
            while next_id in used:
                next_id += 1
            num = next_id
            id_map[key] = num
            used.add(num)
            next_id += 1
        l["siteId"] = "{}-{:05d}".format(SITE_PREFIX, num)
    save_registry(next_id, id_map)


# --- normalization ----------------------------------------------------------

def broker_ref(source_key: str, listing_code: str) -> str:
    codes = load_codes()
    meta = codes.get("sources", {}).get(source_key, {})
    prefix = meta.get("ref_prefix", source_key.upper())
    code = (listing_code or "").strip()
    if code and not re.fullmatch(r"[A-Za-z]{1,6}\d{1,5}[A-Za-z]?", code):
        return prefix
    return "{} #{}".format(prefix, code) if code else prefix


def redacted_name(business_type: str) -> str:
    """Never store the broker's actual business/brand name. Emit a generic
    type-based descriptor — same redaction convention as every other vertical
    in the network (see types.ts: business_name_redacted)."""
    bt = (business_type or "").strip()
    if bt and bt.lower() not in ("other",):
        return "{} Car Wash".format(bt)
    return "Car Wash Business"


def normalize_row(source_key: str, row: Dict, today: str, recent_cutoff: str) -> Optional[Dict]:
    codes = load_codes()
    meta = codes.get("sources", {}).get(source_key, {})

    title = (row.get("title") or "").strip()
    state = (row.get("state") or "").strip().upper()
    if not title:
        return None

    scraped = row.get("scraped_date") or today
    is_new = scraped >= recent_cutoff

    bays = to_int(row.get("bays"))

    return {
        "source_id": row.get("source_id") or "",  # internal key (dropped before write)
        "id": row.get("source_id") or "",
        "siteId": "",  # filled by assign_site_ids
        "source": source_key,
        "source_url": row.get("listing_url") or meta.get("broker_url", ""),
        "type": row.get("business_type") or "Other",
        "state": state,
        "city": (row.get("city") or "").strip(),
        "asking_price": to_int(row.get("asking_price")),
        "annual_revenue": to_int(row.get("annual_revenue")),
        "annual_collections": None,
        "key_metric_value": bays,  # site keyMetric field = bays
        "bays": bays,
        "broker_name": row.get("broker_name") or meta.get("broker_name", ""),
        "broker_company": meta.get("broker_name", "") or row.get("broker_name", ""),
        "broker_url": meta.get("broker_url", ""),
        "broker_ref": broker_ref(source_key, row.get("listing_code", "")),
        "description": (row.get("description") or "").strip(),
        "business_name_redacted": redacted_name(row.get("business_type", "")),
        "scraped_date": scraped,
        "is_new": is_new,
    }


def dedupe(listings: List[Dict]) -> List[Dict]:
    """Cross-source dedupe. Same source_id, or same (state, asking_price,
    sqft-ish) signature with a very similar title, collapses to one (keep the
    richer)."""
    by_key: Dict[str, Dict] = {}
    order: List[str] = []
    for l in listings:
        sig_bits = [l.get("state", ""), str(l.get("asking_price") or ""),
                    str(l.get("bays") or "")]
        title_norm = re.sub(r"[^a-z0-9]", "", (l.get("title") or "").lower())[:24]
        strong = (l.get("asking_price") or l.get("annual_revenue"))
        key = l["source_id"]
        if strong and title_norm:
            key = "|".join(sig_bits + [title_norm])
        if key in by_key:
            def score(x):
                return sum(1 for k in ("asking_price", "annual_revenue",
                                       "bays", "city", "description")
                           if x.get(k))
            if score(l) > score(by_key[key]):
                by_key[key] = l
        else:
            by_key[key] = l
            order.append(key)
    return [by_key[k] for k in order]


def run() -> List[Dict]:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    recent_cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    codes = load_codes()
    known = set(codes.get("sources", {}).keys())
    stem_to_source = {
        "carwashbrokers": "carwashbrokers",
        "commercialplus": "commercialplus",
        "caldercapital": "caldercapital",
    }

    all_norm: List[Dict] = []
    if os.path.isdir(OUTPUT_DIR):
        for fname in sorted(os.listdir(OUTPUT_DIR)):
            if not fname.endswith("_raw.csv"):
                continue
            stem = fname[:-len("_raw.csv")]
            source_key = stem_to_source.get(stem, stem)
            if source_key not in known:
                logger.warning("Skipping unknown source file: %s", fname)
                continue
            path = os.path.join(OUTPUT_DIR, fname)
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            n = 0
            for r in rows:
                nr = normalize_row(source_key, r, today, recent_cutoff)
                if nr:
                    all_norm.append(nr)
                    n += 1
            logger.info("%-16s %d rows -> %d normalized", source_key, len(rows), n)

    before = len(all_norm)
    all_norm = dedupe(all_norm)
    logger.info("Deduped %d -> %d", before, len(all_norm))

    assign_site_ids(all_norm)

    # sort: new first, then by state
    all_norm.sort(key=lambda x: (not x.get("is_new"), x.get("state", "")))

    # strip the internal source_id before writing the public file
    public = []
    for l in all_norm:
        d = dict(l)
        d.pop("source_id", None)
        public.append(d)

    with open(LISTINGS_JSON, "w") as f:
        json.dump(public, f, indent=2)

    for target in SITE_DATA_TARGETS:
        site_root = os.path.dirname(os.path.dirname(os.path.dirname(target)))
        if not os.path.isdir(site_root):
            logger.info("Skipping sibling write (not present): %s",
                        os.path.relpath(target, HERE))
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            json.dump(public, f, indent=2)
        logger.info("Wrote %d listings -> %s", len(public), os.path.relpath(target, HERE))

    logger.info("Wrote %d listings -> listings.json", len(public))
    return public


if __name__ == "__main__":
    out = run()
    print("Done. {} listings normalized.".format(len(out)))
