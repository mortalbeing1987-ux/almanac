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
| **E. Positioning & CB balance sheets** | CFTC Commitments of Traders (currency futures), SNB sight deposits |
| **F. Long-run backdrop** | CPI (headline/core) — CAPE dropped in step 0 |

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
  because it is derived from the data.
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

Manifest: `contract_version`, `kind`, `run_id`, `started_at`, `finished_at`,
`files` (name, sha256, rows), per-source status (`ok` / `empty` / `outage` /
`error` + reason), and the freshness summary. Revisions are delivered as new
rows with `revision` > 0 — never by rewriting old bundles.

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

## Build steps (stop for owner review after each)

0. **Scaffold + source probe (done by the initial commit).** Run the
   `probe-sources` workflow manually; it writes a reachability table to the run
   summary. Fill in the TBD sources in `series.toml` from its results; drop
   anything unreliable.
1. **Fetchers + bundle writer**, with offline tests. Start with use cases A
   and B.
2. **Schedule + freshness + canary + delivery.** Daily cron (after US close
   and again early Asia), weekly for CFTC/SNB. Push to the private data repo
   (secret `DATA_REPO_TOKEN`, a fine-grained token with contents:write on
   that one repo only — the owner creates it). Status in `status.json`.
3. *(Home side, not in this repo)* importer.
4. **Event calendar** (`cal-` bundles), 60 days ahead.
5. Remaining series for C, E, F.

## Non-goals

No brokerage connection. No trading logic, signals or portfolio data. No
earnings calendar (the consumer already has one). No paid or
redistribution-restricted data committed to this repo.
