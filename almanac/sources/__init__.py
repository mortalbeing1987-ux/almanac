"""One fetcher per source. Each exposes fetch(ctx) -> list[Observation] and
raises model.FetchError when the source is down or answers unexpectedly."""
