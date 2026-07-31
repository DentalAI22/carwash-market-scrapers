# Refreshing car wash listings and redeploying the live site

Two independent steps: (1) refresh this repo's dataset, (2) get the site to
pick it up. The daily GitHub Action already does step 1 automatically at
10:00 UTC; the commands below are for a manual/on-demand refresh (e.g. right
after finding a new broker source, or debugging a scraper).

## 1. Refresh the dataset (this repo)

```bash
cd ~/market-network/carwash-scrapers
pip install -r requirements.txt        # first time / after dependency changes
python run_all.py                      # scrape all sources + normalize -> listings.json

# Optional: run a single source while iterating on its scraper
python run_all.py --only carwashbrokers
python run_all.py --only commercialplus
python run_all.py --only caldercapital

# Optional: re-normalize existing CSVs without re-scraping (fast, no network)
python run_all.py --normalize
```

Commit and push the refreshed dataset:

```bash
cd ~/market-network/carwash-scrapers
git add listings.json output/*.csv site_id_registry.json
git commit -m "chore(carwash): refresh listings $(date -u +%Y-%m-%dT%H:%MZ)"
git push
```

(This is exactly what `.github/workflows/scrape-carwash.yml` does on its own,
daily — manual runs are only needed for an out-of-band refresh.)

## 2. Redeploy the live site to pick up the fresh dataset

The site's `prebuild` step (`scripts/fetch-listings.mjs`) pulls this repo's
`listings.json` from the public raw GitHub URL at build time, so any new
production build republishes with the latest data:

```bash
cd ~/market-network/carwash
vercel --prod --yes
```

If you want to confirm what the site's build will fetch before deploying,
check the raw URL directly:

```bash
curl -s https://raw.githubusercontent.com/DentalAI22/carwash-market-scrapers/main/listings.json | python3 -c "import json,sys;print(len(json.load(sys.stdin)), 'listings')"
```

## Adding a new broker source

1. Add a `<source>.py` scraper module (copy `carwashbrokers.py` or
   `commercialplus.py` as a starting template depending on whether the site
   exposes clean CSS classes or is better parsed with text regex).
2. Register it in `broker_codes.json` under `sources` (never mark
   `aggregator: true` — if a source is a multi-broker marketplace, don't add
   it at all; see README.md).
3. Add it to the `SCRAPERS` list in `run_all.py`.
4. Add its raw CSV filename to `stem_to_source` in `normalizer.py` if the
   module name doesn't already match the CSV stem.
5. Run `python run_all.py --only <newsource>` to verify it produces sane
   rows in `output/<newsource>_raw.csv`, then `python run_all.py` for a full
   refresh + commit as above.
