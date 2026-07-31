# carwash-market-scrapers

Public scraper rig for **The Car Wash Market** network vertical. Aggregates
real, public-source car-wash-for-sale listings from dedicated car wash M&A /
business brokers and publishes a single canonical `listings.json` that the
live site consumes.

**Live site fed by this repo:**
- https://thecarwashmarket.com (Vercel project `carwash`)

Everything in this repo is scraper code + public listing data. **No secrets,
no tokens, no seller PII.** Broker office contact details that appear on
public broker listing pages are public business contact info.

## What it does

```
run_all.py  ->  per-source scrapers (carwashbrokers, commercialplus,
                caldercapital)  ->  output/*_raw.csv  ->  normalizer.py
             ->  listings.json  (canonical, TCWM-XXXXX siteIds, deduped)
```

- `utils.py` — real UA + polite 1.5–3.5s delays + price/state helpers.
- `broker_codes.json` — source registry, `site_prefix = TCWM`.
- `site_id_registry.json` — persistent TCWM- id map. **Never renumber.**
- `listings.json` — the canonical dataset. Tracked on purpose; the daily
  Action regenerates and commits it back here.

## Sources — every one is a dedicated brokerage's OWN site, never an aggregator

| Source key       | Broker                | Listings page                                              | Region     |
|-------------------|------------------------|-------------------------------------------------------------|------------|
| `carwashbrokers`  | Car Wash Brokers Inc   | https://www.carwashbrokers.com/property-listing/            | AZ, TX     |
| `commercialplus`  | Commercial Plus, LLC   | https://commercialplus.com/properties/ (Car Wash M&A line)  | TX, GA     |
| `caldercapital`   | Calder Capital         | https://caldergr.com/businesses-for-sale/ (Car Wash vertical)| Midwest    |

**Never scraped, never linked, never will be:** BizBuySell, BizQuest,
LoopNet, DealStream, BusinessBroker.net, or any other multi-broker
aggregator/marketplace — even ones with car wash listings. Every source in
`broker_codes.json` must be a single brokerage's own domain. A general-purpose
car-wash "marketplace" that itself cross-lists other brokers' inventory
(confirmed by inconsistent broker-of-record attribution across its own
listings) was evaluated and deliberately excluded for this reason.

**No minimum listing count.** Per network policy, this rig silos every real,
currently-listed car wash it can verify — one, two, or zero from any given
source on any given day. Nothing is ever padded to hit a round number, and a
source legitimately returning 0 rows on a given run is not an error.

## Auto-refresh pipeline (refresh -> live)

`.github/workflows/scrape-carwash.yml` runs **daily at 10:00 UTC** (staggered
after the network's other same-day vertical launches, plus manual
`workflow_dispatch`). This repo is **PUBLIC**, so GitHub Actions minutes are
unlimited/free.

The Action is **self-contained — it only ever writes to THIS repo:**

1. checkout -> install deps -> `python run_all.py` (scrape + normalize).
2. **Sanity guard:** if `listings.json` collapses to 0 listings (e.g. every
   runner IP gets blocked in the same run), the job **fails and refuses to
   commit**, preserving the last-good dataset. Unlike higher-volume verticals,
   this floor is deliberately **not** a large fixed number — the honest
   inventory here may legitimately be small — it only trips on a total wipe.
3. commit `listings.json` + `output/*.csv` + `site_id_registry.json` back to
   this repo using the default `GITHUB_TOKEN` (`permissions: contents:
   write`). No PAT.

**Why no cross-repo push:** the site repo is a SEPARATE git repo. Instead of
this Action reaching into it, the site **pulls `listings.json` from this
repo's public raw URL at build time**:

```
https://raw.githubusercontent.com/DentalAI22/carwash-market-scrapers/main/listings.json
```

So the refresh-to-live path is:

```
daily Action scrapes  ->  commits listings.json to THIS repo
       ->  a site rebuild (`vercel --prod`, or the site's prebuild fetch step)
           pulls the fresh raw listings.json  ->  republishes.
```

The public raw file is the single source of truth. No cross-repo push
credentials are required anywhere.

## Re-run locally

```bash
pip install -r requirements.txt
python run_all.py                       # scrape all sources + normalize -> listings.json
python run_all.py --only carwashbrokers # one source
python run_all.py --normalize           # re-normalize existing CSVs (no network)
```

See `REFRESH.md` for the exact refresh -> commit -> redeploy command sequence.

## Constraints honored

- Read-only against public broker pages only; real browser UA; 1.5–3.5s delays.
- Blocked aggregators (BizBuySell / BizQuest / LoopNet / DealStream /
  BusinessBroker.net) are **never** scraped, and no multi-broker marketplace
  is added even if it isn't on that named list.
- Every listing kept is a currently-active, operating car wash business for
  sale (raw land / "development site" / "entitled" offerings with no
  operating business are filtered out, not relabeled).
- Honest counts; deduped; no fabricated data. Any field not published by the
  broker (price, revenue, bay count, city) is left blank/null rather than
  guessed.
