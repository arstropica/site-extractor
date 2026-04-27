"""Job status state machine — legal transitions only.

Every gateway code path that wants to change a job's `status` must go
through `Database.update_status`, which validates the transition against
this table before writing. Illegal writes raise `IllegalTransition`,
which the HTTP boundary translates to 409 Conflict.

The graph is intentionally permissive about retries (re-scrape after
completed, re-extract after failed, etc.) but strict about progress
order — e.g. you cannot jump straight from `created` to `completed`
without a real scrape and extract happening.

Transition graph:

    created     → scraping
    scraping    → scraped | paused | failed | cancelled
    paused      → scraping | scraped | failed | cancelled
    scraped     → scraping (re-scrape) | extracting
    extracting  → completed | failed | cancelled
    completed   → scraping (re-scrape) | extracting (re-extract)
    failed      → scraping (retry scrape) | extracting (retry extract)
    cancelled   → scraping (resume from scratch)

Same-status writes (status === current) are treated as no-ops by
`update_status` so callers don't need to special-case idempotent
events (e.g. duplicate pause clicks, redelivered Redis events).
"""

from typing import Dict, FrozenSet


LEGAL_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "created":    frozenset({"scraping"}),
    "scraping":   frozenset({"scraped", "paused", "failed", "cancelled"}),
    "paused":     frozenset({"scraping", "scraped", "failed", "cancelled"}),
    "scraped":    frozenset({"scraping", "extracting"}),
    "extracting": frozenset({"completed", "failed", "cancelled"}),
    "completed":  frozenset({"scraping", "extracting"}),
    "failed":     frozenset({"scraping", "extracting"}),
    "cancelled":  frozenset({"scraping"}),
}


def is_legal_transition(src: str, dst: str) -> bool:
    """Return True iff `src → dst` is in the legal-transitions graph.

    Same-status writes (src == dst) return True so callers can stay
    idempotent without branching.
    """
    if src == dst:
        return True
    return dst in LEGAL_TRANSITIONS.get(src, frozenset())


class IllegalTransition(Exception):
    """Raised by Database.update_status when the requested transition is
    not in the legal graph. The HTTP boundary translates this to 409.

    `src` and `dst` are the canonical lowercase status strings — the
    same values used in the LEGAL_TRANSITIONS table and the JobStatus
    enum.
    """

    def __init__(self, src: str, dst: str):
        self.src = src
        self.dst = dst
        super().__init__(
            f"Illegal status transition: {src!r} → {dst!r}. "
            f"Allowed from {src!r}: {sorted(LEGAL_TRANSITIONS.get(src, []))}"
        )
