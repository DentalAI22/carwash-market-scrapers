"""Shared utilities for car wash business-for-sale scrapers.

Ported faithfully from the veterinary rig (~/market-network/veterinary-scrapers/
utils.py), which itself ports the dental TDPM rig. Same polite-fetch
discipline: real browser UA, 1.5-3.5s random delays, tolerant price parsing.
"""

from __future__ import annotations

import re
import logging
import time
import random
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def get_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def polite_delay(min_sec: float = 1.5, max_sec: float = 3.5) -> None:
    """Sleep a random interval to be polite to servers."""
    time.sleep(random.uniform(min_sec, max_sec))


def parse_price(text: Optional[str]) -> Optional[int]:
    """Extract a dollar amount from text like '$455,000' or '$1.2M'."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("$", "")
    # "1.2 mil" / "1.2 million" / "1.2M" / "1.2 - million" (some WP real-estate
    # themes render the unit as a separate "<span class=price-postfix>" node
    # joined with a literal " - ", e.g. "$1.85<postfix> - million</postfix>")
    m = re.search(r"([\d.]+)\s*-?\s*(?:mil(?:lion)?\b|M\b)", text, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    # "600 K" / "600k" / "600 - K"
    m = re.search(r"([\d.]+)\s*-?\s*[Kk]\b", text)
    if m:
        return int(float(m.group(1)) * 1_000)
    # plain number — only accept a full contiguous integer (avoid grabbing the
    # "13" out of "$1.35mil"). Require >= 4 digits to be a plausible dollar sum.
    m = re.fullmatch(r"\d+", text)
    if m and len(text) >= 4:
        return int(text)
    m = re.search(r"\b(\d{4,})\b", text)
    if m:
        return int(m.group(1))
    return None


def clean_text(text: Optional[str]) -> str:
    """Collapse whitespace and strip a string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def html_to_text(html_str: str) -> str:
    """Crude but dependency-free tag stripper for regex-based extraction on
    pages where hitting exact CSS classes is brittle (heavily templated /
    minified WordPress themes). Prefer BeautifulSoup selectors when a site's
    structure is stable; fall back to this for prose-style detail pages."""
    import html as _html
    text = re.sub(r"<script.*?</script>", " ", html_str, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


# --- US state helpers (car wash listings are location-coded heavily) --------

STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


STREET_SUFFIX_WORDS = {
    "hwy", "highway", "rd", "road", "st", "street", "ave", "avenue", "blvd",
    "boulevard", "dr", "drive", "ln", "lane", "cir", "circle", "ct", "court",
    "pl", "place", "way", "pkwy", "parkway",
}


def _clean_city_candidate(raw: str) -> str:
    """Some broker sites publish addresses missing the comma between the
    street and the city (e.g. '1209 S. Gregg St Big Spring, TX' or '506
    Junction HWY Kerville TX'), so a naive city match bleeds in the trailing
    street-name tokens. If a street-suffix word appears anywhere but the very
    end of the candidate, keep only what follows the LAST such word (that's
    the actual city); if it's a bare 1-2 word candidate with no street-suffix
    word, keep it as-is; otherwise it's unparseable — drop it rather than
    publish a wrong/mangled city."""
    words = raw.strip().split()
    if not words:
        return ""
    lower = [w.lower().strip(".,") for w in words]
    last_suffix_idx = None
    for i, w in enumerate(lower):
        if w in STREET_SUFFIX_WORDS:
            last_suffix_idx = i
    if last_suffix_idx is not None:
        remainder = words[last_suffix_idx + 1:]
        return " ".join(remainder).strip() if remainder else ""
    if len(words) <= 2:
        return " ".join(words)
    return ""


def parse_location(text: Optional[str]) -> Tuple[str, str]:
    """Best-effort (city, state) from a free-text location string.

    Handles 'San Antonio, TX', 'Billings, Montana', 'Central PA', 'Florida'.
    Returns ("", "") if nothing parseable.
    """
    if not text:
        return "", ""
    text = clean_text(text)

    # "City, ST"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Z]{2})\b", text)
    if m and m.group(2) in STATE_ABBRS:
        city = _clean_city_candidate(m.group(1).strip())
        return city.title(), m.group(2)

    # "City, State Name"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Za-z ]+)$", text)
    if m:
        st = STATE_NAME_TO_ABBR.get(m.group(2).strip().lower())
        if st:
            city = _clean_city_candidate(m.group(1).strip())
            return city.title(), st

    # "City ST zip" — no comma (common in plain US mailing addresses). Only
    # trust the captured city if it resolves to a short, street-suffix-free
    # token via _clean_city_candidate — a longer raw match (e.g. "Junction
    # HWY Kerville" when the source string itself lacks a comma before the
    # actual city) is almost always a street-name fragment bleeding in.
    m = re.search(r"\b([A-Za-z][A-Za-z .'-]{1,30}?)\s+([A-Z]{2})\s+\d{5}\b", text)
    if m and m.group(2) in STATE_ABBRS:
        city = _clean_city_candidate(m.group(1).strip())
        return city.title(), m.group(2)

    # "City StateFullName" — no comma, whole (isolated) string only. Safe only
    # when the caller passes a short, already-isolated address string (e.g. a
    # dedicated .address field with nothing else in it) rather than a full
    # page of prose. Tries each known state full-name as the literal suffix
    # (rather than an open-ended char class + dict lookup, which would let
    # regex backtracking grab the wrong split) so "San Antonio Texas" resolves
    # to city="San Antonio" instead of a spurious minimal-length split.
    stripped = text.rstrip(", ").strip()
    for name, abbr in sorted(STATE_NAME_TO_ABBR.items(), key=lambda kv: -len(kv[0])):
        m = re.match(r"^([A-Za-z][A-Za-z .'-]*?)\s+" + re.escape(name.title()) + r"$",
                     stripped, re.I)
        if m and m.group(1).strip():
            return m.group(1).strip().title(), abbr

    # bare state name anywhere
    low = text.lower()
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return "", abbr

    # bare 2-letter code
    m = re.search(r"\b([A-Z]{2})\b", text)
    if m and m.group(1) in STATE_ABBRS:
        return "", m.group(1)

    return "", ""


def parse_location_prose(text: Optional[str]) -> Tuple[str, str]:
    """Location from free-flowing prose like '...express car wash in Graham,
    Texas.' — looks for '<Capitalized City>, <Full State Name>' anywhere in
    the text (no end-anchor, unlike parse_location's stricter variant).
    Returns ("", "") if nothing parseable. Used only where prose is the sole
    source of location (no structured address field on the page)."""
    if not text:
        return "", ""
    for name, abbr in STATE_NAME_TO_ABBR.items():
        m = re.search(r"\b([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){0,2}),\s*" +
                      re.escape(name.title()) + r"\b", text)
        if m:
            return m.group(1).strip(), abbr
    return "", ""
