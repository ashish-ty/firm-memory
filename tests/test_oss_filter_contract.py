"""Contract tests against the real mem0 OSS filter pipeline.

Unit tests only prove we emit the shape we *intended*. These prove the shape
survives ``Memory._process_metadata_filters`` and reaches the vector store with
scoping intact — the failure mode that silently returns zero rows instead of
raising.
"""

from __future__ import annotations

import pytest

from firm_mem0 import Layer, Namespace, layered_filters

mem0_main = pytest.importorskip("mem0.memory.main", reason="mem0ai not installed")

NS = Namespace(repo="acme-billing-svc")


def process(filters: dict) -> dict:
    """Run filters through mem0's preprocessor without touching a database."""
    return mem0_main.Memory._process_metadata_filters(object(), filters)


def test_single_layer_survives_preprocessing():
    assert process(layered_filters(NS, [Layer.REPO])) == {
        "user_id": "repo:acme-billing-svc",
        "agent_id": "acme-billing-svc",
    }


def test_layered_filter_becomes_a_proper_or_of_scoped_branches():
    processed = process(layered_filters(NS, [Layer.REPO, Layer.FIRM]))
    assert processed == {
        "$or": [
            {"user_id": "repo:acme-billing-svc", "agent_id": "acme-billing-svc"},
            {"user_id": "firm"},
        ]
    }


def test_no_logical_operator_leaks_into_a_branch_as_a_literal_key():
    """Regression: an ``AND`` nested inside ``OR`` is passed through verbatim by
    the preprocessor and compiles to ``payload->>'AND' = ANY(...)`` in pgvector,
    matching nothing. Branches must therefore stay flat."""
    processed = process(layered_filters(NS, [Layer.REPO, Layer.FIRM]))
    for branch in processed["$or"]:
        assert not {"AND", "OR", "NOT"} & set(branch), f"logical operator leaked into {branch}"


def test_metadata_filters_survive_preprocessing():
    processed = process(layered_filters(NS, [Layer.REPO], metadata_filters={"type": "coding_conventions"}))
    assert processed["type"] == "coding_conventions"
    assert processed["user_id"] == "repo:acme-billing-svc"


def test_nested_platform_metadata_form_would_have_been_rejected():
    """Documents why metadata filters are flat: the Platform form is invalid on OSS."""
    with pytest.raises(ValueError, match="Unsupported metadata filter operator"):
        process({"AND": [{"user_id": "firm"}, {"metadata": {"type": "x"}}]})


def test_filters_compile_to_sql_with_scoping_preserved():
    """End of the pipeline: pgvector must turn our filter into real conditions."""
    pgvector = pytest.importorskip("mem0.vector_stores.pgvector", reason="pgvector extra not installed")
    processed = process(layered_filters(NS, [Layer.REPO, Layer.FIRM]))
    conditions, params = pgvector._build_filter_conditions(processed)

    assert conditions, "filter produced no SQL conditions"
    sql = " ".join(conditions)
    assert " OR " in sql
    # Scoping values must appear as bound parameters, not be lost in a literal key.
    assert "repo:acme-billing-svc" in params
    assert "acme-billing-svc" in params
    assert "firm" in params
    assert "AND" not in params, "logical operator was bound as a payload key"
