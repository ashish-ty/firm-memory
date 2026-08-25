"""Contract tests against the real mem0 OSS filter pipeline.

Unit tests only prove we emit the shape we *intended*. These prove the shape
survives ``Memory.search``'s validation and ``_process_metadata_filters``, and
reaches the vector store with scoping intact — the failure mode that silently
returns zero rows, or raises, instead of working.

Every constraint asserted here was found by reading mem0's source. If a mem0
upgrade changes one, these fail loudly rather than the pool quietly going empty.
"""

from __future__ import annotations

import pytest

from firm_memory.models import DEFAULT_SEARCH_STATUSES, DEFAULT_SEARCH_TIERS
from firm_memory.providers.mem0.filters import search_filters
from firm_memory.scope import MemoryScope

mem0_main = pytest.importorskip("mem0.memory.main", reason="mem0ai not installed")

ENTITY_KEYS = ("user_id", "agent_id", "run_id")
POOL_OWNER = "firm"
SCOPE = MemoryScope.for_query("acme-billing-svc", domains=("execution",))


def provider_filters(scope: MemoryScope = SCOPE, **kwargs) -> dict:
    """Exactly what Mem0Provider.search sends to mem0."""
    kwargs.setdefault("tiers", DEFAULT_SEARCH_TIERS)
    kwargs.setdefault("statuses", DEFAULT_SEARCH_STATUSES)
    return {"user_id": POOL_OWNER, **search_filters(scope, **kwargs)}


def process(filters: dict) -> dict:
    """Run filters through mem0's preprocessor without touching a database."""
    return mem0_main.Memory._process_metadata_filters(object(), filters)


def test_mem0_requires_a_top_level_entity_key():
    """The precondition that forces user_id to name the pool, not the partition.

    A filter of ``{"OR": [...]}`` alone — the obvious way to union scopes — is
    rejected by ``Memory.search`` before it reaches the store.
    """
    bare = search_filters(SCOPE)
    assert not any(key in bare for key in ENTITY_KEYS), "precondition assumed by the next assertion"
    assert any(key in provider_filters() for key in ENTITY_KEYS)


def test_the_pool_key_is_preserved_alongside_the_scope_union():
    processed = process(provider_filters())
    assert processed["user_id"] == POOL_OWNER
    assert "$or" in processed


def test_a_scope_union_becomes_a_proper_or_of_flat_branches():
    processed = process(provider_filters())
    assert [set(branch) - {"tier", "status"} for branch in processed["$or"]] == [
        {"scope_firm"},
        {"scope_domain_execution"},
        {"scope_repo_acme-billing-svc"},
    ]


def test_no_logical_operator_leaks_into_a_branch_as_a_literal_key():
    """An ``AND`` nested inside ``OR`` is passed through verbatim by the
    preprocessor and compiles to ``payload->>'AND' = ANY(...)`` in pgvector,
    matching nothing. Branches must therefore stay flat."""
    for branch in process(provider_filters())["$or"]:
        assert not {"AND", "OR", "NOT"} & set(branch), f"logical operator leaked into {branch}"


def test_multi_valued_filters_survive_as_one_of_semantics():
    """What makes tier and status filtering expressible inside a flat branch."""
    for branch in process(provider_filters())["$or"]:
        assert branch["tier"] == ["durable", "index"]


def test_a_single_scope_atom_needs_no_or_at_all():
    processed = process(provider_filters(MemoryScope.for_repo("oms")))
    assert "$or" not in processed
    assert processed["scope_repo_oms"] == "1"


def test_the_nested_platform_metadata_form_would_have_been_rejected():
    """Documents why every filter key is flat: the Platform form is invalid on OSS."""
    with pytest.raises(ValueError, match="Unsupported metadata filter operator"):
        process({"user_id": POOL_OWNER, "AND": [{"metadata": {"type": "x"}}]})


def test_filters_compile_to_sql_with_scoping_preserved():
    """End of the pipeline: pgvector must turn our filter into real conditions."""
    pgvector = pytest.importorskip("mem0.vector_stores.pgvector", reason="pgvector extra not installed")
    conditions, params = pgvector._build_filter_conditions(process(provider_filters()))

    assert conditions, "filter produced no SQL conditions"
    sql = " ".join(conditions)
    assert " OR " in sql

    # Scoping must appear as bound parameters, not be lost in a literal key.
    assert POOL_OWNER in params
    for key in ("scope_firm", "scope_domain_execution", "scope_repo_acme-billing-svc"):
        assert key in params, f"{key} was not bound"
    assert "AND" not in params, "logical operator was bound as a payload key"


def test_the_tier_guard_compiles_to_an_any_clause():
    """Without this, every task's episodic scratch state joins ordinary recall."""
    pgvector = pytest.importorskip("mem0.vector_stores.pgvector", reason="pgvector extra not installed")
    conditions, params = pgvector._build_filter_conditions(process(provider_filters()))

    assert "payload->>%s = ANY(%s)" in " ".join(conditions)
    assert ["durable", "index"] in params
    assert not any(param == "episodic" for param in params)
