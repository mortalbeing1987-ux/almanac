# Almanac

Collects public macro and market-reference data -- Treasury curve, overnight
funding rates, credit spreads, volatility indexes, central-bank data, CFTC
positioning and a macro event calendar -- on a GitHub Actions schedule, and
delivers it as versioned bundles to a private destination.

This repository holds **code only**. Collected data is never committed here
(some sources restrict redistribution).

- Spec and build steps: [`docs/ALMANAC_SPEC.md`](docs/ALMANAC_SPEC.md)
- What is collected: [`almanac/series.toml`](almanac/series.toml)
- Rules for contributors and coding agents: [`CLAUDE.md`](CLAUDE.md)

Step 0: run the **probe-sources** workflow (Actions tab -> Run workflow) to
check every source is reachable from GitHub's runners.
