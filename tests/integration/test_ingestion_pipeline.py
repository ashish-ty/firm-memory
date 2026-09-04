"""Raw material reaches memory only through extraction and a human.

The architectural guarantee this platform exists to provide: there is no path
that takes text and stores it. These tests are the guard on that.
"""

import pytest

from firm_memory import FirmMemory, MemoryScope, MemoryStatus, Settings
from firm_memory.errors import ConfigurationError
from firm_memory.ingestion.extraction import LLMFactExtractor, SourceDocument
from firm_memory.provenance import Provenance, Source
from firm_memory.providers.inmemory import InMemoryProvider

RULE = "Cash strategies stop sending at 15:20 because the exchange rejects after that."
REJECTED = "Queueing after-cut-off orders for the next session was abandoned: it caused duplicate fills."

DISCUSSION = """
Reviewer: why does the OMS drop orders after 15:20?
Author: the exchange rejects anything sent after 15:20 for cash strategies. We tried
queueing them for the next session and it caused duplicate fills, so we dropped it.
"""


class ScriptedClient:
    def __init__(self, *entries):
        self._entries = list(entries)

    def complete_json(self, system, user):
        return {"facts": self._entries}


@pytest.fixture
def pipeline(provider):
    extractor = LLMFactExtractor(
        ScriptedClient(
            {"content": RULE, "type": "business_rules", "confidence": 0.9},
            {"content": REJECTED, "type": "anti_patterns", "confidence": 0.8},
        )
    )
    api = FirmMemory(
        provider,
        settings=Settings(min_score=0.0),
        scope=MemoryScope.for_query("oms", domains=("execution",)),
        extractor=extractor,
    )
    yield api
    api.close()


def source(**overrides):
    defaults = {
        "content": DISCUSSION,
        "scope": MemoryScope(domains=("execution",), repos=("oms",)),
        "provenance": Provenance(source=Source.MERGE_REQUEST, reference="mr-4821"),
        "kind": "merge request discussion",
    }
    return SourceDocument(**{**defaults, **overrides})


# --- the guarantee -----------------------------------------------------------


def test_ingestion_writes_nothing_to_the_pool(pipeline, provider):
    """The headline guarantee: ingest never reaches the provider."""
    pipeline.ingest(source())

    assert list(provider.export_all()) == []
    assert pipeline.search("when do cash strategies stop sending") == []


def test_ingestion_queues_candidates_for_a_human(pipeline):
    candidates = pipeline.ingest(source())

    assert len(candidates) == 2
    assert {c.memory.status for c in candidates} == {MemoryStatus.PROPOSED}
    assert len(list(pipeline.approvals.pending())) == 2


def test_only_approval_makes_an_extracted_fact_retrievable(pipeline):
    rule = next(c for c in pipeline.ingest(source()) if c.memory.type.value == "business_rules")

    assert pipeline.search("when do cash strategies stop sending") == []
    approved = pipeline.approvals.approve(rule.id, approver="ashish")
    assert approved.status is MemoryStatus.ACTIVE

    [hit] = pipeline.search("when do cash strategies stop sending")
    assert hit.id == approved.id


def test_a_rejected_candidate_never_enters_the_pool(pipeline, provider):
    rule = next(c for c in pipeline.ingest(source()) if c.memory.type.value == "business_rules")
    pipeline.approvals.reject(rule.id, reviewer="lead", reason="already in the runbook")

    assert list(provider.export_all()) == []


def test_provenance_survives_from_source_to_retrieved_memory(pipeline):
    """What makes a cited memory traceable back to the MR it came from."""
    rule = next(c for c in pipeline.ingest(source()) if c.memory.type.value == "business_rules")
    approved = pipeline.approvals.approve(rule.id, approver="ashish")

    assert approved.provenance.reference == "mr-4821"
    assert approved.provenance.source == Source.MERGE_REQUEST
    assert approved.provenance.author == "ashish"


def test_re_ingesting_the_same_source_does_not_duplicate_the_queue(pipeline):
    """A stateless bot re-runs the same MR on every push."""
    pipeline.ingest(source())
    pipeline.ingest(source())

    assert len(list(pipeline.approvals.pending())) == 2


def test_several_sources_are_ingested_in_one_call(pipeline):
    candidates = pipeline.ingest(source(), source(content="a different discussion entirely"))
    assert len(candidates) >= 2


def test_ingesting_nothing_is_harmless(pipeline):
    assert pipeline.ingest() == []


# --- configuration -----------------------------------------------------------


def test_a_deployment_without_an_extractor_says_so_rather_than_failing_obscurely():
    with (
        FirmMemory(InMemoryProvider(), scope=MemoryScope.firm_wide()) as memory,
        pytest.raises(ConfigurationError, match="extractor"),
    ):
        memory.ingest(source())


def test_search_and_curation_work_without_any_extractor_configured():
    """Ingestion is optional; a deployment may only search and curate by hand."""
    from firm_memory.lifecycle import approve
    from tests.conftest import make_memory

    settings = Settings(min_score=0.0)
    with FirmMemory(InMemoryProvider(), settings=settings, scope=MemoryScope.firm_wide()) as memory:
        stored = memory.commit(
            approve(make_memory(content=RULE, scope=MemoryScope.firm_wide()), approver="lead")
        )
        assert memory.search(RULE)[0].id == stored.id


def test_from_env_builds_no_extractor_when_no_model_is_configured():
    settings = Settings.from_env({})
    assert settings.extraction_model is None


# --- extractor selection -----------------------------------------------------


def test_the_provider_extractor_is_chosen_when_the_provider_has_one():
    """mem0 gets to do the extraction; approval still gates the write."""
    from firm_memory.memory import _default_extractor
    from firm_memory.providers.mem0.extraction import Mem0FactExtractor

    class ProviderWithExtractor(InMemoryProvider):
        backend = object()

    extractor = _default_extractor(Settings(extractor="provider"), ProviderWithExtractor())
    assert isinstance(extractor, Mem0FactExtractor)


def test_a_provider_without_an_extractor_falls_back_rather_than_refusing_to_start():
    from firm_memory.ingestion.extraction import LLMFactExtractor
    from firm_memory.memory import _default_extractor

    settings = Settings(extractor="provider", extraction_model="openrouter/anthropic/claude-3.5-sonnet")
    assert isinstance(_default_extractor(settings, InMemoryProvider()), LLMFactExtractor)


def test_extraction_can_be_disabled_entirely():
    from firm_memory.memory import _default_extractor

    assert _default_extractor(Settings(extractor="none"), InMemoryProvider()) is None
