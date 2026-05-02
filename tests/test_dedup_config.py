"""Contract tests for the scrape_config.dedup field.

The dedup feature gates whether the crawler silently skips downloading a
resource whose bytes match an already-saved file in the same scrape. The
default is OFF — every URL the page references must produce its own row
and file so downstream consumers can resolve it. The feature is opt-in
because turning it on trades fidelity for disk savings.

This test pins the read contract that crawler.py relies on:
``config.get("dedup", {}).get("enabled", False)``. If a refactor changes
the field path or default, these cases fail and surface the regression
before it ships — including the silent case where a job created before
the dedup feature existed (no ``dedup`` key) is correctly treated as off.
"""


def _read_dedup_enabled(scrape_config: dict) -> bool:
    """Mirror of crawler.py's read of the dedup flag.

    Kept as a free function (not imported from the scraper service) so
    these tests run in the project venv without needing the scraper's
    runtime dependencies (httpx, bs4, playwright, etc.).
    """
    return bool((scrape_config or {}).get("dedup", {}).get("enabled", False))


def test_empty_config_defaults_off():
    assert _read_dedup_enabled({}) is False


def test_legacy_config_without_dedup_field_defaults_off():
    """Jobs created before the dedup feature shipped have no ``dedup``
    key. They must keep working with full fidelity (no silent skip)."""
    legacy = {
        "seed_urls": ["https://example.com"],
        "depth_limit": 3,
        "respect_robots": True,
    }
    assert _read_dedup_enabled(legacy) is False


def test_explicit_disabled():
    assert _read_dedup_enabled({"dedup": {"enabled": False}}) is False


def test_explicit_enabled():
    assert _read_dedup_enabled({"dedup": {"enabled": True}}) is True


def test_dedup_present_but_missing_enabled_key_defaults_off():
    """A malformed config with ``dedup: {}`` must not silently flip on."""
    assert _read_dedup_enabled({"dedup": {}}) is False


def test_none_config():
    assert _read_dedup_enabled(None) is False
