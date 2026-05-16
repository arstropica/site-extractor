"""Tests for the style_property field source.

Covers the three style sources and the first-match lookup priority:
  1. element style="..." attribute (always wins)
  2. inline <style> rule whose selector exactly matches the field selector
  3. external stylesheet (saved to assets/) with the same exact selector

Also covers the UI-enforced source priority (url_regex > style_property >
attribute > text) when more than one is set.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "extraction-service"))

from app.extractor.engine import ExtractionEngine, _StyleResolver  # noqa: E402

from bs4 import BeautifulSoup  # noqa: E402


SCALAR_SCHEMA = [
    {"name": "bg", "field_type": "string", "is_array": False},
]
ARRAY_SCHEMA = [
    {"name": "bg", "field_type": "string", "is_array": True},
]


def _stage(
    tmp_path: Path,
    job_id: str,
    html: str,
    css: str = "",
    assets: dict = None,
) -> dict:
    """Drop an HTML page + optional site.css + optional named asset files."""
    job_dir = tmp_path / "jobs" / job_id
    pages_dir = job_dir / "pages"
    assets_dir = job_dir / "assets"
    pages_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "page.html").write_text(html, encoding="utf-8")
    if css:
        (assets_dir / "site.css").write_text(css, encoding="utf-8")
    for name, content in (assets or {}).items():
        (assets_dir / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    return {"url": "https://example.com/page", "local_path": "pages/page.html"}


def _run(tmp_path: Path, page: dict, field_mapping: dict, schema=SCALAR_SCHEMA) -> dict:
    eng = ExtractionEngine(data_dir=str(tmp_path))
    results = eng.extract_from_pages(
        job_id="job1",
        pages=[page],
        schema_fields=schema,
        config={"document": {"field_mappings": [field_mapping]}},
    )
    assert len(results) == 1
    return results[0]["data"]


# ── _StyleResolver unit ──────────────────────────────────────────────────────


class TestStyleResolverUnit:
    def test_inline_style_attr_wins_over_stylesheets(self, tmp_path):
        page_path = tmp_path / "p.html"
        page_path.write_text("<html><body></body></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><style>.x{color:red;background:url(s.png)}</style></head>'
            '<body><div class="x" style="color:blue;background:url(inline.png)"></div></body></html>',
            "lxml",
        )
        # data_dir is unused here (no external link), but the resolver still requires it.
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        assert r.resolve(el, ".x", "color") == "blue"
        assert r.resolve(el, ".x", "background") == "url(inline.png)"

    def test_inline_style_block_match(self, tmp_path):
        page_path = tmp_path / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><style>.x{color:red}</style></head><body><div class="x"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        assert r.resolve(el, ".x", "color") == "red"

    def test_external_stylesheet_match(self, tmp_path):
        job_dir = tmp_path / "jobs" / "job1"
        pages_dir = job_dir / "pages"
        assets_dir = job_dir / "assets"
        pages_dir.mkdir(parents=True)
        assets_dir.mkdir(parents=True)
        page_path = pages_dir / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        (assets_dir / "site.css").write_text(
            ".x { background-image: url(bg.png); padding: 10px; }",
            encoding="utf-8",
        )
        soup = BeautifulSoup(
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="x"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        assert r.resolve(el, ".x", "background-image") == "url(bg.png)"
        assert r.resolve(el, ".x", "padding") == "10px"

    def test_exact_selector_match_only(self, tmp_path):
        """No specificity-aware cascade — field_selector must match rule text exactly."""
        page_path = tmp_path / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><style>div.x{color:red}</style></head>'
            '<body><div class="x"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        # The element matches `div.x`, but our field selector is `.x` —
        # exact-match semantics mean we don't find anything.
        assert r.resolve(el, ".x", "color") is None
        # With the exact selector, it matches.
        assert r.resolve(el, "div.x", "color") == "red"

    def test_missing_property_returns_none(self, tmp_path):
        page_path = tmp_path / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><style>.x{color:red}</style></head><body><div class="x"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        assert r.resolve(el, ".x", "background") is None

    def test_comma_separated_selectors_register_under_each(self, tmp_path):
        page_path = tmp_path / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><style>.a, .b { color: red }</style></head>'
            '<body><div class="b"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".b")
        assert r.resolve(el, ".b", "color") == "red"
        assert r.resolve(el, ".a", "color") == "red"

    def test_comments_stripped(self, tmp_path):
        page_path = tmp_path / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><style>'
            '/* .x{color:blue} */ .x{color:red}'
            '</style></head><body><div class="x"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        assert r.resolve(el, ".x", "color") == "red"

    def test_path_escape_rejected(self, tmp_path):
        """A relative href that resolves outside the job dir must not be read."""
        job_dir = tmp_path / "jobs" / "job1"
        pages_dir = job_dir / "pages"
        pages_dir.mkdir(parents=True)
        # CSS file outside any job — should be unreachable.
        (tmp_path / "evil.css").write_text(".x{color:purple}", encoding="utf-8")
        page_path = pages_dir / "p.html"
        page_path.write_text("<html></html>", encoding="utf-8")
        soup = BeautifulSoup(
            '<html><head><link rel="stylesheet" href="../../../evil.css"></head>'
            '<body><div class="x"></div></body></html>',
            "lxml",
        )
        r = _StyleResolver(soup, page_path, tmp_path, "job1")
        el = soup.select_one(".x")
        assert r.resolve(el, ".x", "color") is None


# ── End-to-end through ExtractionEngine ──────────────────────────────────────


class TestExtractStyleEndToEnd:
    # All test CSS fixtures use ../assets/<file> form — same shape both
    # page_storage and the crawler's CSS postprocess write into rewritten
    # source. The engine emits url() values verbatim after disk-check, so
    # extraction results match the convention `<img src>` string-field
    # extraction already follows.

    def test_background_from_external_css(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = '.hero { background-image: url(../assets/hero.jpg); }'
        page = _stage(tmp_path, "job1", html, css, assets={"hero.jpg": b"\x89PNG"})
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        })
        assert data["bg"] == "../assets/hero.jpg"

    def test_inline_style_attr(self, tmp_path):
        html = (
            '<html><body><div class="hero" '
            'style="background-image:url(../assets/inline.jpg)"></div></body></html>'
        )
        page = _stage(tmp_path, "job1", html, assets={"inline.jpg": b"\x89PNG"})
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        })
        assert data["bg"] == "../assets/inline.jpg"

    def test_inline_style_attr_wins_over_stylesheet(self, tmp_path):
        html = (
            '<html><head><style>.hero{background-image:url(../assets/sheet.jpg)}</style></head>'
            '<body><div class="hero" '
            'style="background-image:url(../assets/inline.jpg)"></div></body></html>'
        )
        page = _stage(tmp_path, "job1", html, assets={
            "inline.jpg": b"i", "sheet.jpg": b"s",
        })
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        })
        assert data["bg"] == "../assets/inline.jpg"

    def test_multi_url_value_scalar_returns_first(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = (
            '.hero { background-image: '
            'url(../assets/a.jpg), url(../assets/b.jpg), url(../assets/c.jpg); }'
        )
        page = _stage(tmp_path, "job1", html, css, assets={
            "a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c",
        })
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        })
        assert data["bg"] == "../assets/a.jpg"

    def test_multi_url_value_array_returns_all(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = (
            '.hero { background-image: '
            'url(../assets/a.jpg), url(../assets/b.jpg), url(../assets/c.jpg); }'
        )
        page = _stage(tmp_path, "job1", html, css, assets={
            "a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c",
        })
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        }, schema=ARRAY_SCHEMA)
        assert data["bg"] == [
            "../assets/a.jpg",
            "../assets/b.jpg",
            "../assets/c.jpg",
        ]

    def test_array_drops_missing_assets(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = (
            '.hero { background-image: '
            'url(../assets/present.jpg), url(../assets/missing.jpg); }'
        )
        page = _stage(tmp_path, "job1", html, css, assets={"present.jpg": b"p"})
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        }, schema=ARRAY_SCHEMA)
        assert data["bg"] == ["../assets/present.jpg"]

    def test_scalar_missing_first_returns_null(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = '.hero { background-image: url(../assets/absent.jpg); }'
        page = _stage(tmp_path, "job1", html, css)  # no assets staged
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        })
        assert data["bg"] is None

    def test_external_url_dropped_no_leakage(self, tmp_path):
        """Strict policy: only ../assets/<file> refs the scraper saved emit a value."""
        html = (
            '<html><body><div class="hero" '
            'style="background-image:url(https://cdn.example.com/external.jpg)"></div></body></html>'
        )
        page = _stage(tmp_path, "job1", html)
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        })
        assert data["bg"] is None

    def test_external_url_dropped_in_array(self, tmp_path):
        """Mixed local + external in a stack — only ../assets/ refs survive."""
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = (
            '.hero { background-image: '
            'url(../assets/local.jpg), url(https://cdn.example.com/x.jpg); }'
        )
        page = _stage(tmp_path, "job1", html, css, assets={"local.jpg": b"l"})
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
        }, schema=ARRAY_SCHEMA)
        assert data["bg"] == ["../assets/local.jpg"]

    def test_non_url_value_returned_verbatim(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = '.hero { color: #ff0000; }'
        page = _stage(tmp_path, "job1", html, css)
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "color",
        })
        assert data["bg"] == "#ff0000"

    def test_non_url_value_array_wraps_single(self, tmp_path):
        html = (
            '<html><head><link rel="stylesheet" href="../assets/site.css"></head>'
            '<body><div class="hero"></div></body></html>'
        )
        css = '.hero { padding: 10px 20px; }'
        page = _stage(tmp_path, "job1", html, css)
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "padding",
        }, schema=ARRAY_SCHEMA)
        # Non-url values aren't split — array fields get a single-element list.
        assert data["bg"] == ["10px 20px"]

    def test_url_regex_wins_over_style_property(self, tmp_path):
        """Stale mapping with both set should follow the documented priority."""
        html = (
            '<html><head><style>.hero{background-image:url(../assets/wins.jpg)}</style></head>'
            '<body><div class="hero"></div></body></html>'
        )
        page = _stage(tmp_path, "job1", html, assets={"wins.jpg": b"w"})
        data = _run(tmp_path, page, {
            "field_path": "bg",
            "selector": ".hero",
            "style_property": "background-image",
            "url_regex": r"/page/(\w+)",  # won't match this URL → null
        })
        # url_regex took priority; capture missed → None.
        assert data["bg"] is None
