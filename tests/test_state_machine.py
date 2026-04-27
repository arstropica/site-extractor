"""Legal transition graph tests.

The graph itself is small enough that we can table-test it
exhaustively: every (src, dst) pair across the full JobStatus
universe, asserting True/False against the spec.
"""

import pytest

from services.shared.state_machine import (
    LEGAL_TRANSITIONS,
    IllegalTransition,
    is_legal_transition,
)


ALL_STATUSES = (
    "created", "scraping", "paused", "scraped",
    "extracting", "completed", "failed", "cancelled",
)


# Expected legal pairs (src, dst). Includes same-status idempotents.
LEGAL_PAIRS = {
    # idempotent — every status to itself
    *((s, s) for s in ALL_STATUSES),
    # explicit graph edges (mirror of LEGAL_TRANSITIONS in state_machine.py)
    ("created", "scraping"),
    ("scraping", "scraped"),
    ("scraping", "paused"),
    ("scraping", "failed"),
    ("scraping", "cancelled"),
    ("paused", "scraping"),
    ("paused", "scraped"),
    ("paused", "failed"),
    ("paused", "cancelled"),
    ("scraped", "scraping"),
    ("scraped", "extracting"),
    ("extracting", "completed"),
    ("extracting", "failed"),
    ("extracting", "cancelled"),
    ("completed", "scraping"),
    ("completed", "extracting"),
    ("failed", "scraping"),
    ("failed", "extracting"),
    ("cancelled", "scraping"),
}


@pytest.mark.parametrize(
    "src,dst",
    [(s, d) for s in ALL_STATUSES for d in ALL_STATUSES],
    ids=lambda v: v,
)
def test_transition_matrix(src, dst):
    expected = (src, dst) in LEGAL_PAIRS
    assert is_legal_transition(src, dst) == expected, (
        f"{src} → {dst}: expected {expected}, got {is_legal_transition(src, dst)}"
    )


def test_unknown_source_status_rejects_everything():
    # An unrecognized status (legacy / corrupted row) should never be
    # silently extended — the validator returns False for any forward
    # transition (same-status still True so cleanup writes work).
    assert is_legal_transition("weird", "weird") is True
    assert is_legal_transition("weird", "scraping") is False
    assert is_legal_transition("weird", "completed") is False


def test_illegal_transition_exception_carries_src_dst():
    err = IllegalTransition("created", "completed")
    assert err.src == "created"
    assert err.dst == "completed"
    msg = str(err)
    assert "created" in msg and "completed" in msg


def test_illegal_transition_lists_allowed_destinations():
    err = IllegalTransition("created", "completed")
    msg = str(err)
    # The error message must surface the legal targets so the caller
    # can fix their request without grepping the source.
    assert "scraping" in msg


def test_legal_transitions_table_keys_match_status_universe():
    # Catches drift if someone adds a status to JobStatus but forgets
    # to give it an entry in the transition graph.
    assert set(LEGAL_TRANSITIONS.keys()) == set(ALL_STATUSES) - {"completed", "failed", "cancelled", "scraped", "extracting", "paused"} | {
        "created", "scraping", "paused", "scraped", "extracting", "completed", "failed", "cancelled",
    }
    # Less convoluted: just be exhaustive.
    assert set(LEGAL_TRANSITIONS.keys()) == set(ALL_STATUSES)
