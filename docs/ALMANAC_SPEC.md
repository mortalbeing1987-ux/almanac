# Almanac — build spec

## Purpose

Keep a complete, current, point-in-time-honest store of public macro and
market-reference data that trading, bond-pricing, FX and risk tools need but a
brokerage API doesn't provide (or provides poorly):

| Use | Data |
|---|---|
| **A. Curve & credit** | Treasury par yield curve incl. true bills (1M–30Y), constant-maturity Treasury history, ICE BofA OAS by rating band, real yields, breakevens |
| **B. Funding & carry inputs** | Overnight rates: SOFR, EFFR (USD), €STR (EUR), SARON (CHF), TONA (JPY), RBA cash rate (AUD), SORA (SGD) |
| **C. Regime** | VIX, VIX3M, VVIX, HY OAS, 10y–3m and 10y–2y spreads, NFCI, broad USD index, initial jobless claims |
| **D. Event calendar** | FOMC; SNB, ECB, BoJ, RBA and MAS policy decisions; US CPI, payrolls, GDP release dates |
| **E. Positioning & CB balance sheets** | CFTC Commitments of Traders (currency futures: non-commercial long/short/net, open interest), SNB sight deposits (total, domestic banks) |
| **F. Long-run backdrop** | CPI (headline/core) — CAPE dropped in step 0 |
| **G. Trade** | US exports, imports and balance plus services by category (BEA monthly release workbook; FRED kept as a cross-check only); goods by partner incl. South Korea (Census API, monthly); goods and services by partner (BEA ITA API, quarterly; EU from BEA's geo workbook) — inputs for GDP, FX and sector views |

Consumers (analysis, dashboards, reports) live **outside** this repo. Almanac
only collects, validates and delivers.

## Architecture

```
GitHub Actions (cron, daily + weekly)
  -> almanac fetchers (one per source)
  -> validate -> state (what's been delivered, per series)
  -> bundle (parquet + manifest)
  -> push to PRIVATE data repo (token = Actions secret)
Home importer (not in this repo) pulls bundles -> its own DB
```

- `almanac/series.toml` — the registry: every series with its source, source
  key, frequency, units, use-case tag, and expected release lag.
- `almanac/sources/<source>.py` — one fetcher per source, returning tidy rows.
- `almanac/state` — per-series delivery state (last observation delivered,
  last fetch, last error). Kept in the PRIVATE data repo next to the bundles,
  because it is derived from the data. It also records, per delivered id, the
  backfill depth fetched (`backfilled_from`): raising a series' `backfill` in
  the registry makes the next run fetch the older history once (delivered as
  new rows; values already delivered are dropped as unchanged), and the record
  is updated only when that source's fetch succeeded.
- `almanac/freshness.py` — the "gap score": for each series, is it behind its
  expected release schedule (`fresh`), not yet backfilled to source start
  (`depth`), or is the calendar short of the next 60 days (`calendar_ahead`)?
  Written to `status.json` in each delivery.
- **Canary:** before a batch, fetch one known-good series per source. If the
  canary fails, record the source as `outage` and write nothing for it.

## Delivery contract (Almanac's own versioning, independent of other bots)

A bundle is a folder `<kind>-<run_id>/` (`run_id` = UTC start,
`YYYYMMDDTHHMMSSZ`) with data files and a `manifest.json` written **last**
(no manifest = incomplete, importer skips it).

**`macro-` (contract_version 1): `observations.parquet`**

| column | type | notes |
|---|---|---|
| `series_id` | string | registry id |
| `obs_date` | string | `YYYY-MM-DD` (period start for monthly/weekly, per registry) |
| `value` | float64 | in the registry's units |
| `source` | string | registry source name |
| `fetched_at` | string | UTC ISO-8601 |
| `revision` | int | 0 = first delivery of this (series, date); n = nth changed value |

**`cal-` (contract_version 1): `events.parquet`**

| column | type | notes |
|---|---|---|
| `event_id` | string | stable: `<source>:<kind>:<date>` |
| `event_date` | string | `YYYY-MM-DD` |
| `event_time_utc` | string, nullable | `HH:MM` if published |
| `country` | string | ISO-2 or `EA` |
| `kind` | string | `cb_decision` \| `release` |
| `name` | string | e.g. `FOMC decision`, `CPI` |
| `source` | string | |
| `fetched_at` | string | |

Calendar events cover the past 30 days to 60 days ahead and are insert-only by
`event_id`: an id is delivered once. If an institution moves an event, the new
date has a new id and arrives as a new row in a later `cal-` bundle; earlier
bundles are never rewritten (the old row stays, as it was known at the time).
`event_time_utc` is the institution's published standard announcement time,
converted to UTC for that date (null where none is published, e.g. BoJ).
Sources: FOMC (federalreserve.gov), SNB (snb.ch event schedule), ECB
(ecb.europa.eu Governing Council calendar), BoJ (boj.or.jp MPM schedule), RBA
(rba.gov.au board meeting schedule), GDP (BEA release schedule); CPI and the
Employment Situation from the St. Louis Fed's FRED release calendar, which
republishes the BLS schedule (bls.gov refuses scripted clients). MAS publishes
Monetary Policy Statement dates only about a week ahead, so it is not yet
covered.

`status.json` also carries `cal_bundle`, `events_delivered`,
`calendar_sources` (per source: status, events in horizon, furthest event) and
`freshness.calendar_ahead`: per calendar source, the furthest event found and
`ok = false` when it is less than 60 days out (listed under `short`).

Withdrawn events: when a source's status is `ok`, its
`calendar_sources.<source>` entry also has `event_ids_in_window` (sorted ids the
source lists right now) and `window_from` / `window_to` (the date range that
list speaks for, inclusive). A previously delivered id of that source whose
date is inside `window_from..window_to` but which is not in the list has been
withdrawn (moved or cancelled); a moved event's new date arrives as a new row.
The range is 30 days back to 60 ahead, except for pages that list only upcoming
events (ECB, SNB), whose range starts today so that a past meeting the page no
longer shows is not mistaken for a cancellation. On `outage`, `error` or
`empty` these three fields are absent: a failed fetch says nothing about
cancellations.

Manifest: `contract_version`, `kind`, `run_id`, `started_at`, `finished_at`,
`files` (name, sha256, rows), per-source status (`ok` / `empty` / `outage` /
`error` + reason), and the freshness summary. Revisions are delivered as new
rows with `revision` > 0 — never by rewriting old bundles.

`status.json` reports each run: `bundle` and `rows_delivered` (of which
`revisions_delivered` have `revision` > 0), per-source status, and the freshness
summary (per delivered series: last/first observation, age and its max age).

The contract is the bundles plus the root `status.json`. `state/` in the data
repo is Almanac's private bookkeeping; its layout may change without a
contract bump, and consumers must not read it.

## Sources (verify in step 0 — URLs drift)

Keyless and reachable as of 2026-09-28 from a residential connection:
Treasury.gov daily par curve CSV, NY Fed rates API (SOFR/EFFR), ECB data API
(€STR), SNB data cube, CBOE index history CSVs, CFTC `deafut.txt`, Federal
Reserve FOMC calendar page, FRED `fredgraph.csv` (keyless). **FRED rejected
requests that used a fake browser User-Agent** (connection timed out) but
answered instantly to an honest script User-Agent — always send
`almanac/<version>` as the UA. Step 0 re-tests all of this from GitHub's
runners. If FRED ever requires its (free) API key, the owner adds it as secret
`FRED_API_KEY`. BLS's schedule
page returned 403 (use another official schedule source or FRED release
dates). Step 0 outcome (2026-09-28, from GitHub's runners): TONA from the BoJ
Time-Series Data Search API (keyless CSV), RBA cash rate from statistical
table F1 (keyless CSV), SARON from SNB cube `snbgwdzid`, SORA from the MAS API
gateway with the owner's free key (secret `MAS_API_KEY`). CAPE dropped.

Use case E (probe 2026-09-28): CFTC legacy futures-only COT, contracts
identified by CFTC contract market code (CHF 092741, JPY 097741, EUR 099741,
AUD 232741, GBP 096742, CAD 090741; names changed over time, codes did not).
`deafut.txt` holds the latest report only; history comes from CFTC's yearly
archives `files/dea/history/deacot<year>.zip` (1986 onwards, one `annual.txt`
each, the current year updated weekly). Delivered as
`COT_FX_<ccy>_{NC_LONG,NC_SHORT,NC_NET,OI}`, `obs_date` = the as-of Tuesday.
SNB sight deposits from cube `snbgwdchfsgw`, D0 codes `TG` (total) and `GI`
(domestic banks), weekly, dated the Friday, published the following Monday.
Both run in the daily delivery (a routine pass re-reads only the current
year's COT zip, ~2 MB, and the SNB cube, ~0.4 MB).

## Build steps (stop for owner review after each)

0. **Scaffold + source probe (done by the initial commit).** Run the
   `probe-sources` workflow manually; it writes a reachability table to the run
   summary. Fill in the TBD sources in `series.toml` from its results; drop
   anything unreliable.
1. **Fetchers + bundle writer**, with offline tests. Start with use cases A
   and B.
2. **Schedule + freshness + canary + delivery.** Daily cron (after US close
   and again early Asia); the weekly CFTC/SNB series ride the same runs. Push to the private data repo
   (secret `DATA_REPO_TOKEN`, a fine-grained token with contents:write on
   that one repo only — the owner creates it). Status in `status.json`.
3. *(Home side, not in this repo)* importer.
4. **Event calendar** (`cal-` bundles), 60 days ahead.
5. Remaining series for C, E, F.

## Non-goals

No brokerage connection. No trading logic, signals or portfolio data. No
earnings calendar (the consumer already has one). No paid or
redistribution-restricted data committed to this repo.
