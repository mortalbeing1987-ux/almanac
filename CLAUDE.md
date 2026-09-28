# Almanac — rules for every session in this repo

Almanac collects **public macro and market-reference data** (rates, curves,
credit spreads, volatility indexes, central-bank data, event calendars) on a
schedule and delivers it to a **private** destination. A separate home system
imports it. Read `docs/ALMANAC_SPEC.md` before any work.

## This repo is PUBLIC. Hard rules

1. **Code only, never data.** Never commit downloaded data, parquet/CSV
   outputs, caches or bundles. Some sources forbid redistribution (ICE BofA
   index data on FRED, CBOE index data). Outputs go to the private
   destination described in the spec. `.gitignore` covers `data/`, `out/`,
   `*.parquet`; keep it that way.
2. **No personal or account information, ever.** No email addresses, names,
   account numbers, positions, portfolio sizes, local paths of other systems,
   or anything describing what the owner holds or trades. Keep code and docs
   generic ("a home importer", "the consumer").
3. **No secrets in code, docs, logs or workflow files.** Tokens, API keys and
   contact strings live only in GitHub Actions secrets that the owner adds
   through GitHub settings. Never ask for a secret in chat. Never print a
   secret's value, even partially, in a workflow log. GitHub's own `***`
   placeholder for a secret passed to a step is fine (it only shows that the
   secret exists).
4. **Official/public sources only**, accessed the way their terms allow. No
   scraping of commercial calendar or data sites. Be polite: one request at
   a time per host, a clear User-Agent, backoff on 429/5xx.
5. **Insert-only, point-in-time honest.** Never silently overwrite a value
   already delivered; a revised observation is delivered as a revision (see
   the spec) so backtests can use values as they were known at the time.
6. **Outage ≠ no data.** A failed or empty fetch must be recorded as retryable,
   never as "up to date".

## How to work

- **One build step at a time** (see the step list in the spec). Finish the
  step, run the tests, summarise what was built and what the owner should
  check, then **stop and wait for approval** before starting the next step.
- Python 3.11+, standard library first; add a dependency only when it clearly
  pays for itself, and pin it in `requirements.txt`.
- Every fetcher gets tests that run offline against small fixtures you write
  by hand (never commit real downloaded data as fixtures if the source
  restricts redistribution — synthesise the shape instead).
- Keep the series registry (`almanac/series.toml`) the single place that lists
  what is collected. Adding a series should mean adding one entry.
