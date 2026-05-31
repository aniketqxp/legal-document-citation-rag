"""Unit tests for the deterministic retrieval metrics.

These guard the matching logic the whole eval rests on: if ``span_matches`` or
``first_relevant_rank`` regress, every reported number becomes meaningless. They
are pure (no DB, no network), so they run anywhere in milliseconds.
"""

from evaluation import metrics
from evaluation.metrics import RetrievedChunk


def test_normalize_collapses_case_punctuation_and_whitespace():
    assert metrics.normalize("The  STATE\nof Nevada.") == "the state of nevada"
    assert metrics.normalize("non\xa0breaking") == "non breaking"


def test_span_matches_ignores_cosmetic_differences():
    gold = "shall be governed by and construed in accordance with the laws"
    chunk = (
        "This Agreement SHALL be governed by\n"
        "and construed in accordance with the laws thereof."
    )
    assert metrics.span_matches(gold, chunk) is True


def test_span_matches_rejects_unrelated_text():
    gold = "shall be governed by and construed in accordance with the laws of Nevada"
    chunk = "The licensee shall remit all fees within thirty days of invoice."
    assert metrics.span_matches(gold, chunk) is False


def test_span_matches_skips_spans_that_are_too_short():
    # Below MIN_SPAN_CHARS once normalised — too generic to be a real match.
    assert metrics.span_matches("Yes", "Yes it does apply here") is False


def test_first_relevant_rank_finds_correct_position():
    gold = ["governed by and construed in accordance with the laws of Nevada"]
    retrieved = [
        RetrievedChunk("contract-A", "Some unrelated preamble text about parties."),
        RetrievedChunk(
            "contract-A",
            "This Agreement is governed by and construed in accordance with "
            "the laws of Nevada and the parties consent to jurisdiction there.",
        ),
    ]
    assert (
        metrics.first_relevant_rank(
            gold_spans=gold, contract_id="contract-A", retrieved=retrieved
        )
        == 2
    )


def test_first_relevant_rank_requires_matching_contract():
    # Right text, WRONG document -> not a hit (cross-document discrimination).
    gold = ["governed by and construed in accordance with the laws of Nevada"]
    retrieved = [
        RetrievedChunk(
            "some-other-contract",
            "This Agreement is governed by and construed in accordance with "
            "the laws of Nevada.",
        )
    ]
    assert (
        metrics.first_relevant_rank(
            gold_spans=gold, contract_id="contract-A", retrieved=retrieved
        )
        is None
    )


def test_hit_at_k_and_reciprocal_rank():
    assert metrics.hit_at_k(1, 1) == 1
    assert metrics.hit_at_k(3, 1) == 0
    assert metrics.hit_at_k(None, 6) == 0
    assert metrics.reciprocal_rank(2) == 0.5
    assert metrics.reciprocal_rank(None) == 0.0


def test_aggregate_means_over_eval_set():
    scores = [
        metrics.score_query(
            contract_id="c",
            category="Governing Law",
            question="q1",
            gold_spans=["governed by the laws of the state of nevada hereto"],
            retrieved=[
                RetrievedChunk(
                    "c", "governed by the laws of the state of nevada hereto"
                )
            ],
            k_values=[1, 3],
        ),
        metrics.score_query(
            contract_id="c",
            category="Parties",
            question="q2",
            gold_spans=["a clause that will not be retrieved at all anywhere"],
            retrieved=[RetrievedChunk("c", "totally different content")],
            k_values=[1, 3],
        ),
    ]
    agg = metrics.aggregate(scores, [1, 3])
    assert agg["hit@1"] == 0.5  # one of two found at rank 1
    assert agg["hit@3"] == 0.5
    assert agg["mrr"] == 0.5
    assert agg["n_questions"] == 2.0


def test_aggregate_handles_empty_eval_set():
    agg = metrics.aggregate([], [1, 3, 6])
    assert agg == {
        "hit@1": 0.0,
        "hit@3": 0.0,
        "hit@6": 0.0,
        "mrr": 0.0,
        "n_questions": 0.0,
    }
