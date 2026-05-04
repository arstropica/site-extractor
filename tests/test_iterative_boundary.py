"""End-to-end and unit tests for the iterative-boundary extraction primitive.

Covers:
- Iterator-driven record fan-out against the column-projected example.html
  (records live across separate <tr> parents — no shared ancestor below
  <table>).
- _normalize_selector for leading combinators.
- _resolve_selector for index substitution.
- Anchor-skip for sparse iterations (single + multi-anchor OR-of-presence).
- merge_by collapsing two iterations on one page that share a key.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# Make the extraction-service package importable from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "extraction-service"))

from app.extractor.engine import (  # noqa: E402
    ExtractionEngine,
    _normalize_selector,
    _resolve_selector,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_HTML = FIXTURE_DIR / "example_iterative.html"


# ── helpers ────────────────────────────────────────────────────────────────

def _stage_page(tmp_path: Path, job_id: str, fixture: Path, local_path: str = "page.html") -> dict:
    """Drop a fixture HTML into the engine's expected on-disk layout.

    extract_from_pages reads `<data_dir>/jobs/<job_id>/<local_path>`.
    """
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / local_path
    shutil.copy(fixture, target)
    return {"url": f"https://example.com/{local_path}", "local_path": local_path}


def _engine(tmp_path: Path) -> ExtractionEngine:
    return ExtractionEngine(data_dir=str(tmp_path))


SCHEMA_FIELDS = [
    {"name": "title", "field_type": "string", "is_array": False},
    {"name": "details", "field_type": "string", "is_array": False},
    {"name": "images", "field_type": "string", "is_array": True},
    {"name": "performer", "field_type": "string", "is_array": False},
]


def _example_config(extra_doc: dict | None = None) -> dict:
    doc = {
        "root_boundary": "table[border='7']",
        "iterators": [
            {
                "name": "i",
                "count_selector": "tr:first-of-type > td",
                "anchor": "title",
            }
        ],
        "field_mappings": [
            {"field_path": "title", "selector": "tr:nth-of-type(1) > td:nth-of-type({i}) span"},
            {"field_path": "details", "selector": "tr:nth-of-type(2) > td:nth-of-type({i}) blockquote"},
            {"field_path": "images", "selector": "tr:nth-of-type(3) > td:nth-of-type({i}) img", "attribute": "src"},
            {"field_path": "performer", "url_regex": r"member_videos_(\w+)\.htm"},
        ],
    }
    if extra_doc:
        doc.update(extra_doc)
    return {"document": doc}


# ── _normalize_selector ────────────────────────────────────────────────────

class TestNormalizeSelector:
    def test_passthrough_for_normal_selector(self):
        assert _normalize_selector("tr") == "tr"
        assert _normalize_selector("div.foo > span") == "div.foo > span"

    def test_injects_scope_for_leading_combinators(self):
        assert _normalize_selector("> tr") == ":scope > tr"
        assert _normalize_selector("+ td") == ":scope + td"
        assert _normalize_selector("~ p") == ":scope ~ p"

    def test_handles_leading_whitespace(self):
        assert _normalize_selector("  > tr") == ":scope > tr"

    def test_passthrough_when_scope_already_present(self):
        assert _normalize_selector(":scope > tr") == ":scope > tr"

    def test_empty_string(self):
        assert _normalize_selector("") == ""


# ── _resolve_selector ──────────────────────────────────────────────────────

class TestResolveSelector:
    def test_substitutes_index_var(self):
        assert _resolve_selector("td:nth-of-type({i})", {"i": 3}) == "td:nth-of-type(3)"

    def test_combines_substitution_with_normalization(self):
        assert _resolve_selector("> td:nth-of-type({i})", {"i": 2}) == ":scope > td:nth-of-type(2)"

    def test_no_subs_passes_through_normalize(self):
        assert _resolve_selector("> td", {}) == ":scope > td"
        assert _resolve_selector("> td", None) == ":scope > td"

    def test_format_failure_leaves_template(self):
        # Missing key: silently keeps original (caller surfaces via select failure)
        assert _resolve_selector("td:nth-of-type({k})", {"i": 1}) == "td:nth-of-type({k})"


# ── End-to-end: example.html (column-projected records) ──────────────────

class TestExampleColumnProjected:
    def test_emits_four_records_three_full_one_sparse(self, tmp_path):
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML, "member_videos_carmen.htm")
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=_example_config(),
        )

        # 4 records: 3 from the first record-table (full), 1 from the second
        # (only column 1 has a title; columns 2 & 3 are &nbsp; placeholders).
        assert len(results) == 4, results

        titles = [r["data"]["title"] for r in results]
        # Titles are uppercase via CSS but extracted text preserves source case.
        assert titles[0].lower() == "carmen's first video"
        assert "drivers test" in titles[1].lower()
        assert "maintenance discipline" in titles[2].lower()
        assert "discipline continues" in titles[3].lower()

    def test_performer_url_regex_broadcasts_to_all_records(self, tmp_path):
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML, "member_videos_carmen.htm")
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=_example_config(),
        )
        # url_regex captures slug — same across all iteration-produced records.
        for r in results:
            assert r["data"]["performer"] == "carmen"

    def test_first_record_has_three_images(self, tmp_path):
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML, "member_videos_carmen.htm")
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=_example_config(),
        )
        # Column 1 of the first table contains 3 images: carmen1/2/3-gif.gif.
        first_imgs = results[0]["data"]["images"]
        assert isinstance(first_imgs, list)
        assert len(first_imgs) == 3
        assert all("carmen" in src and ".gif" in src.lower() for src in first_imgs)

    def test_anchor_skip_filters_sparse_columns(self, tmp_path):
        """The 2nd table has empty columns 2 & 3 (anchor=title is &nbsp;).

        Without anchor-skip we'd get 6 records (3 + 3); with it we get 4.
        """
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML)

        # Run WITHOUT anchor (default falls back to first declared field = title,
        # which IS empty in cols 2/3 of table 2 — so default behaves same as
        # explicit anchor: title).
        config_no_anchor = _example_config()
        config_no_anchor["document"]["iterators"][0].pop("anchor")
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=config_no_anchor,
        )
        assert len(results) == 4


# ── Anchor skip — multi-field OR-of-presence ──────────────────────────────

class TestMultiAnchor:
    def test_multi_anchor_keeps_record_when_any_field_present(self, tmp_path):
        """Use a multi-anchor [title, details]. The 2nd table's column 1 has both.
        Should still emit 4 records as before.
        """
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML)
        cfg = _example_config()
        cfg["document"]["iterators"][0]["anchor"] = ["title", "details"]
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=cfg,
        )
        assert len(results) == 4

    def test_multi_anchor_skips_when_all_empty(self, tmp_path):
        """Anchor on fields that are all empty in cols 2-3 of table 2 →
        same skip behavior."""
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML)
        cfg = _example_config()
        # Both title and details are empty in cols 2-3 of table 2.
        cfg["document"]["iterators"][0]["anchor"] = ["title", "details"]
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=cfg,
        )
        assert len(results) == 4


# ── Multi-iterator Cartesian + nested boundaries ──────────────────────────

CARTESIAN_HTML = """<!DOCTYPE html>
<html><body>
  <div class="grid">
    <div class="row" data-row="1">
      <span class="cell">A1</span><span class="cell">A2</span>
    </div>
    <div class="row" data-row="2">
      <span class="cell">B1</span><span class="cell">B2</span>
    </div>
    <div class="row" data-row="3">
      <span class="cell">C1</span><span class="cell">C2</span>
    </div>
  </div>
</body></html>
"""


class TestCartesianIteration:
    def test_two_iterators_produce_n_times_m_records(self, tmp_path):
        eng = _engine(tmp_path)
        # Stage the synthetic HTML.
        job_dir = tmp_path / "jobs" / "job1"
        job_dir.mkdir(parents=True)
        (job_dir / "page.html").write_text(CARTESIAN_HTML)
        page = {"url": "https://example.com/page.html", "local_path": "page.html"}

        config = {
            "document": {
                "root_boundary": "div.grid",
                "iterators": [
                    {"name": "i", "count_selector": "div.row"},                 # 3 rows
                    {"name": "j", "count_selector": "div.row:first-of-type > span.cell"},  # 2 cols
                ],
                "field_mappings": [
                    {"field_path": "value",
                     "selector": "div.row:nth-of-type({i}) > span.cell:nth-of-type({j})"},
                ],
            },
        }
        schema = [{"name": "value", "field_type": "string", "is_array": False}]

        results = eng.extract_from_pages(
            job_id="job1", pages=[page], schema_fields=schema, config=config,
        )
        # 3 rows × 2 cols = 6 records, in row-major order.
        values = [r["data"]["value"] for r in results]
        assert values == ["A1", "A2", "B1", "B2", "C1", "C2"]


# ── merge_by interaction ───────────────────────────────────────────────────

class TestMergeByWithIterators:
    def test_merge_collapses_iterations_sharing_key(self, tmp_path):
        """merge_by="performer" — all 4 iterator-produced records share
        performer="carmen", so they collapse into a single merged record.
        """
        eng = _engine(tmp_path)
        page = _stage_page(tmp_path, "job1", EXAMPLE_HTML, "member_videos_carmen.htm")
        cfg = _example_config({"merge_by": "performer"})
        results = eng.extract_from_pages(
            job_id="job1",
            pages=[page],
            schema_fields=SCHEMA_FIELDS,
            config=cfg,
        )
        # All 4 share performer="carmen" → single merged record (first-non-null).
        assert len(results) == 1
        assert results[0]["data"]["performer"] == "carmen"
        # First-non-null per field — title is "carmen's first video" (record 1)
        assert "first video" in results[0]["data"]["title"].lower()
