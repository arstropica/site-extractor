"""Pure derivation of pipeline-stage status from a job record.

Single source of truth for "which wizard steps are done?" — the server
job record IS the state. The wizard's stepper is a projection of the
output of this function, not an accumulator the client writes to.

The TypeScript twin lives at `services/ui/src/lib/pipelineStages.ts` and
must produce identical outputs for the same inputs. The pytest suite at
`tests/test_pipeline_stages.py` defines the canonical fixture table; the
TS test file mirrors the same fixtures so divergence is caught.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal, Optional


class StageStatus(str, Enum):
    PENDING = "pending"          # not started; no precondition error
    IN_PROGRESS = "in_progress"  # server is actively running this stage
    COMPLETE = "complete"        # stage succeeded cleanly
    WARNING = "warning"          # stage succeeded but with per-URL errors (scrape only)
    FAILED = "failed"            # stage hit a whole-stage failure


@dataclass(frozen=True)
class StageInfo:
    status: StageStatus
    message: Optional[str] = None  # human-readable detail when status is warning/failed

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status.value, "message": self.message}


@dataclass(frozen=True)
class PipelineStages:
    config: StageInfo
    scrape: StageInfo
    schema: StageInfo
    mapper: StageInfo
    results: StageInfo

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {name: getattr(self, name).to_dict() for name in STAGES}


StageName = Literal["config", "scrape", "schema", "mapper", "results"]
STAGES: tuple = ("config", "scrape", "schema", "mapper", "results")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _scrape_config_valid(scrape_config: Optional[Dict[str, Any]]) -> bool:
    if not scrape_config:
        return False
    seeds = scrape_config.get("seed_urls") or []
    return any(isinstance(u, str) and u.strip() for u in seeds)


def _schema_complete(extraction_config: Optional[Dict[str, Any]]) -> bool:
    if not extraction_config:
        return False
    if not extraction_config.get("schema_id"):
        return False
    if not extraction_config.get("mode"):
        return False
    return True


def _mapper_complete(extraction_config: Optional[Dict[str, Any]]) -> bool:
    if not _schema_complete(extraction_config):
        return False
    doc_cfg = (extraction_config or {}).get("document") or {}
    mappings = doc_cfg.get("field_mappings") or []
    if not mappings:
        return False
    # Every mapping must have a selector or url_regex source.
    for m in mappings:
        if not isinstance(m, dict):
            return False
        if not (m.get("selector") or m.get("url_regex")):
            return False
    return True


# ── Main derivation ──────────────────────────────────────────────────────────


def compute_pipeline_stages(job: Optional[Dict[str, Any]]) -> PipelineStages:
    """Derive per-stage status from a job record.

    `job` is the dict returned by the gateway DB layer. Missing keys are
    treated as None / 0; the function never raises on shape.
    """
    if not job:
        unknown = StageInfo(StageStatus.PENDING)
        return PipelineStages(unknown, unknown, unknown, unknown, unknown)

    status = (job.get("status") or "").strip()
    failed_stage = (job.get("failed_stage") or "").strip() or None
    error_message = job.get("error_message") or None

    scrape_cfg = job.get("scrape_config") or {}
    extraction_cfg = job.get("extraction_config") or {}

    pages_errored = int(job.get("pages_errored") or 0)
    resources_errored = int(job.get("resources_errored") or 0)

    # ── config ──────────────────────────────────────────────────────────────
    if _scrape_config_valid(scrape_cfg):
        config = StageInfo(StageStatus.COMPLETE)
    else:
        # Even on a `created` job we treat missing seeds as pending — there's
        # no separate "config failed" notion (config validation rejects on
        # POST /jobs before the row is written).
        config = StageInfo(StageStatus.PENDING)

    # ── scrape ──────────────────────────────────────────────────────────────
    # A "scrape complete" candidate: the scrape ran end-to-end, regardless of
    # what happened next. Extract-stage failures don't unwind scrape state.
    scrape_succeeded = status in ("scraped", "extracting", "completed") or (
        status == "failed" and failed_stage == "extract"
    )
    if status in ("scraping", "paused"):
        scrape = StageInfo(StageStatus.IN_PROGRESS)
    elif scrape_succeeded:
        if pages_errored > 0 or resources_errored > 0:
            counts = []
            if pages_errored:
                counts.append(f"{pages_errored} page error(s)")
            if resources_errored:
                counts.append(f"{resources_errored} resource error(s)")
            scrape = StageInfo(StageStatus.WARNING, ", ".join(counts))
        else:
            scrape = StageInfo(StageStatus.COMPLETE)
    elif status == "failed":
        # Failed at scrape (or legacy row with no failed_stage attribution).
        scrape = StageInfo(StageStatus.FAILED, error_message)
    elif status == "cancelled":
        scrape = StageInfo(StageStatus.FAILED, "Cancelled by user")
    else:
        scrape = StageInfo(StageStatus.PENDING)

    # ── schema ──────────────────────────────────────────────────────────────
    if _schema_complete(extraction_cfg):
        schema = StageInfo(StageStatus.COMPLETE)
    else:
        schema = StageInfo(StageStatus.PENDING)

    # ── mapper ──────────────────────────────────────────────────────────────
    if _mapper_complete(extraction_cfg):
        mapper = StageInfo(StageStatus.COMPLETE)
    else:
        mapper = StageInfo(StageStatus.PENDING)

    # ── results ─────────────────────────────────────────────────────────────
    results: StageInfo
    if status == "extracting":
        results = StageInfo(StageStatus.IN_PROGRESS)
    elif status == "completed":
        results = StageInfo(StageStatus.COMPLETE)
    elif status == "failed" and failed_stage == "extract":
        results = StageInfo(StageStatus.FAILED, error_message)
    else:
        results = StageInfo(StageStatus.PENDING)

    return PipelineStages(config, scrape, schema, mapper, results)


def next_stage_for_job(job: Optional[Dict[str, Any]]) -> str:
    """Pick the wizard stage the user should land on for this job.

    Walks stages in pipeline order and returns the first one that is not
    complete-or-warning (i.e., still needs the user's attention: pending,
    in_progress, or failed). If every stage is complete/warning, returns
    `results` so the user lands on the output rather than being kicked
    back to the start.

    Replaces the older `stageForJobStatus` switch — same intent, but
    consumes the same PipelineStages projection the stepper uses, so
    the redirect target and the visual marks can never disagree.
    """
    stages = compute_pipeline_stages(job)
    for name in STAGES:
        info: StageInfo = getattr(stages, name)
        if info.status not in (StageStatus.COMPLETE, StageStatus.WARNING):
            return name
    return STAGES[-1]
