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
)


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
