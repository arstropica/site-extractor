"""Canonical fixture table for compute_pipeline_stages.

The TS twin at services/ui/src/lib/pipelineStages.test.ts MUST mirror
this table. If you add a case here, add the same case there. The
JSON-encoded fixtures live at tests/fixtures/pipeline_stages.json so
both languages can load the same data.
"""

import json
from pathlib import Path

import pytest

from services.shared.pipeline_stages import (
    PipelineStages,
    StageInfo,
    StageStatus,
    compute_pipeline_stages,
    next_stage_for_job,
)


# ── next_stage_for_job — landing-stage picker ────────────────────────────────
NEXT_STAGE_CASES = [
    # (name, job, expected_stage)
    ("none_job → config", None, "config"),
    ("created_no_seeds → config",
     {"status": "created", "scrape_config": {}},
     "config"),
    ("created_with_seeds → scrape",
     {"status": "created", "scrape_config": {"seed_urls": ["https://x/"]}},
     "scrape"),
    ("scraping → scrape",
     {"status": "scraping", "scrape_config": {"seed_urls": ["https://x/"]}},
     "scrape"),
    ("scraped_no_extraction → schema",
     {"status": "scraped", "scrape_config": {"seed_urls": ["https://x/"]}},
     "schema"),
    ("scraped_with_warnings → schema (warning is complete-enough)",
     {"status": "scraped", "scrape_config": {"seed_urls": ["https://x/"]},
      "resources_errored": 12},
     "schema"),
    ("scraped_with_schema_only → mapper",
     {"status": "scraped", "scrape_config": {"seed_urls": ["https://x/"]},
      "extraction_config": {"mode": "document", "schema_id": "abc"}},
     "mapper"),
    ("scraped_with_full_config → results (ready to run extract)",
     {"status": "scraped", "scrape_config": {"seed_urls": ["https://x/"]},
      "extraction_config": {"mode": "document", "schema_id": "abc",
                            "document": {"field_mappings": [
                                {"field_path": "x", "selector": "p"}]}}},
     "results"),
    ("extracting → results",
     {"status": "extracting", "scrape_config": {"seed_urls": ["https://x/"]},
      "extraction_config": {"mode": "document", "schema_id": "abc",
                            "document": {"field_mappings": [
                                {"field_path": "x", "selector": "p"}]}}},
     "results"),
    ("completed → results",
     {"status": "completed", "scrape_config": {"seed_urls": ["https://x/"]},
      "extraction_config": {"mode": "document", "schema_id": "abc",
                            "document": {"field_mappings": [
                                {"field_path": "x", "selector": "p"}]}}},
     "results"),
    ("failed_at_scrape → scrape (retry there)",
     {"status": "failed", "failed_stage": "scrape",
      "scrape_config": {"seed_urls": ["https://x/"]}},
     "scrape"),
    ("failed_at_extract → results (retry there)",
     {"status": "failed", "failed_stage": "extract",
      "scrape_config": {"seed_urls": ["https://x/"]},
      "extraction_config": {"mode": "document", "schema_id": "abc",
                            "document": {"field_mappings": [
                                {"field_path": "x", "selector": "p"}]}}},
     "results"),
]


@pytest.mark.parametrize("name,job,expected", NEXT_STAGE_CASES, ids=lambda v: v if isinstance(v, str) else None)
def test_next_stage_for_job(name, job, expected):
    assert next_stage_for_job(job) == expected, f"[{name}] expected {expected}"


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pipeline_stages.json"


def _load_fixtures():
    with FIXTURE_PATH.open() as f:
        cases = json.load(f)
    # Each case: {name, job, expected: {config, scrape, schema, mapper, results}}
    return [(c["name"], c["job"], c["expected"]) for c in cases]


@pytest.mark.parametrize("name,job,expected", _load_fixtures(), ids=lambda v: v if isinstance(v, str) else None)
def test_compute_pipeline_stages(name, job, expected):
    """Each fixture row asserts the full PipelineStages output for one job shape."""
    result = compute_pipeline_stages(job).to_dict()
    # Fixtures may specify only the stages that matter for the case; skip
    # missing keys so cases stay focused on what they're asserting.
    for stage, exp in expected.items():
        assert result[stage]["status"] == exp["status"], (
            f"[{name}] {stage} status: expected {exp['status']}, "
            f"got {result[stage]['status']}"
        )
        if "message" in exp:
            assert result[stage]["message"] == exp["message"], (
                f"[{name}] {stage} message: expected {exp['message']!r}, "
                f"got {result[stage]['message']!r}"
            )


def test_none_job_returns_all_pending():
    result = compute_pipeline_stages(None)
    for stage in ("config", "scrape", "schema", "mapper", "results"):
        assert getattr(result, stage).status == StageStatus.PENDING


def test_pipeline_stages_to_dict_roundtrip():
    info = StageInfo(StageStatus.WARNING, "3 page error(s)")
    assert info.to_dict() == {"status": "warning", "message": "3 page error(s)"}

    stages = PipelineStages(
        config=StageInfo(StageStatus.COMPLETE),
        scrape=StageInfo(StageStatus.IN_PROGRESS),
        schema=StageInfo(StageStatus.PENDING),
        mapper=StageInfo(StageStatus.PENDING),
        results=StageInfo(StageStatus.PENDING),
    )
    d = stages.to_dict()
    assert set(d.keys()) == {"config", "scrape", "schema", "mapper", "results"}
    assert d["config"] == {"status": "complete", "message": None}
