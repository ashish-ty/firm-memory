"""Extraction turns raw material into candidates — never into memory."""

import pytest

from firm_memory.errors import ExtractionError, InvalidInputError
from firm_memory.ingestion.extraction import (
    LLMFactExtractor,
    SourceDocument,
    build_system_prompt,
    build_user_prompt,
    extract_all,
)
from firm_memory.models import MemoryStatus, MemoryTier
from firm_memory.provenance import Provenance, Source
from firm_memory.scope import MemoryScope
from firm_memory.taxonomy import CODING_CATEGORIES, EXCLUSIONS, MemoryType

DISCUSSION = """
Reviewer: why does the OMS drop orders after 15:20?
Author: the exchange rejects anything sent after 15:20 for cash strategies, so we
stop sending. We tried queueing them for the next session and it caused duplicate
fills, so we abandoned that.
"""


class FakeClient:
    """Returns a canned response and records the prompts it was given."""

    def __init__(self, response=None, raises=None):
        self.calls = []
        self._response = response if response is not None else {"facts": []}
        self._raises = raises

    def complete_json(self, system, user):
        if self._raises:
            raise self._raises
        self.calls.append((system, user))
        return self._response


def document(**overrides):
    defaults = {
        "content": DISCUSSION,
        "scope": MemoryScope(domains=("execution",), repos=("oms",)),
        "provenance": Provenance(source=Source.MERGE_REQUEST, reference="mr-4821"),
        "kind": "merge request discussion",
    }
    return SourceDocument(**{**defaults, **overrides})


def facts(*entries):
    return {"facts": list(entries)}


# --- the source document -----------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   "])
def test_an_empty_source_is_refused(bad):
    with pytest.raises(InvalidInputError):
        document(content=bad)


def test_an_oversized_source_is_truncated_rather_than_sent_whole():
    """A source this long is a document; it belongs behind an evidence pointer."""
    from firm_memory.ingestion.extraction import MAX_SOURCE_CHARS

    assert len(document(content="x" * (MAX_SOURCE_CHARS * 2)).content) == MAX_SOURCE_CHARS


# --- the prompt --------------------------------------------------------------


def test_the_prompt_carries_the_firm_taxonomy_not_a_generic_one():
    prompt = build_system_prompt()
    for category in CODING_CATEGORIES:
        assert category.name in prompt


def test_the_prompt_carries_every_exclusion():
    """Without these the model returns diffs, stack traces and transient state."""
    prompt = build_system_prompt()
    for exclusion in EXCLUSIONS:
        assert exclusion in prompt


def test_the_prompt_says_extracting_nothing_is_a_valid_outcome():
    """Otherwise the model invents durable knowledge to fill the list."""
    assert "empty list is correct" in build_system_prompt()


def test_the_user_prompt_states_the_scope_the_facts_will_inherit():
    prompt = build_user_prompt(document())
    assert "domain:execution" in prompt and "repo:oms" in prompt
    assert "merge request discussion" in prompt


# --- extraction --------------------------------------------------------------


def test_extracted_facts_are_proposed_never_active():
    """The whole point: extraction proposes, it does not write."""
    client = FakeClient(facts({"content": "Cash strategies stop at 15:20.", "type": "business_rules"}))
    [candidate] = LLMFactExtractor(client).extract(document())

    assert candidate.status is MemoryStatus.PROPOSED
    assert candidate.id is None


def test_candidates_inherit_the_scope_and_provenance_of_their_source():
    client = FakeClient(facts({"content": "Cash strategies stop at 15:20.", "type": "business_rules"}))
    [candidate] = LLMFactExtractor(client).extract(document())

    assert candidate.scope == MemoryScope(domains=("execution",), repos=("oms",))
    assert candidate.provenance.reference == "mr-4821"
    assert candidate.provenance.source == Source.MERGE_REQUEST


def test_the_taxonomy_is_closed_so_an_unknown_type_is_discarded():
    """A mistyped memory is unretrievable; guessing is worse than dropping."""
    client = FakeClient(
        facts(
            {"content": "Ashish prefers tabs.", "type": "user_preferences"},
            {"content": "Cash strategies stop at 15:20.", "type": "business_rules"},
        )
    )
    [candidate] = LLMFactExtractor(client).extract(document())
    assert candidate.type is MemoryType.BUSINESS_RULE


def test_low_confidence_candidates_do_not_reach_a_reviewer():
    client = FakeClient(
        facts(
            {"content": "Possibly the cache is the issue.", "type": "bug_fixes", "confidence": 0.1},
            {"content": "Cash strategies stop at 15:20.", "type": "business_rules", "confidence": 0.9},
        )
    )
    candidates = LLMFactExtractor(client, min_confidence=0.35).extract(document())
    assert [c.confidence for c in candidates] == [0.9]


def test_one_malformed_fact_does_not_cost_the_others():
    client = FakeClient(
        facts(
            "not a dict",
            {"content": "", "type": "business_rules"},
            {"content": "Cash strategies stop at 15:20.", "type": "business_rules"},
        )
    )
    assert len(LLMFactExtractor(client).extract(document())) == 1


def test_extracting_nothing_is_a_normal_outcome():
    assert LLMFactExtractor(FakeClient(facts())).extract(document()) == []


def test_the_number_of_facts_per_source_is_bounded():
    entries = [{"content": f"Durable fact number {i}.", "type": "task_learnings"} for i in range(50)]
    assert len(LLMFactExtractor(FakeClient(facts(*entries)), max_facts=10).extract(document())) == 10


def test_a_response_without_a_facts_list_is_an_extraction_error():
    with pytest.raises(ExtractionError, match="no 'facts' list"):
        LLMFactExtractor(FakeClient({"result": "ok"})).extract(document())


def test_episodic_ingestion_carries_its_task():
    client = FakeClient(facts({"content": "Reviewer rejected the coalescing cache.", "type": "review_feedback"}))
    [candidate] = LLMFactExtractor(client).extract(
        document(tier=MemoryTier.EPISODIC, task="mr-4821")
    )
    assert candidate.tier is MemoryTier.EPISODIC and candidate.task == "mr-4821"


# --- batches -----------------------------------------------------------------


def test_a_batch_survives_one_document_failing():
    """Fifty MRs must not be lost because one upset the model."""

    class FlakyClient(FakeClient):
        def complete_json(self, system, user):
            if "second" in user:
                raise ExtractionError("model refused")
            return facts({"content": "Cash strategies stop at 15:20.", "type": "business_rules"})

    documents = [document(content="first source"), document(content="second source")]
    assert len(extract_all(LLMFactExtractor(FlakyClient()), documents)) == 1


def test_total_failure_is_reported_not_disguised_as_an_empty_result():
    """A dead model must not look like a week with no durable knowledge."""
    client = FakeClient(raises=ExtractionError("gateway timeout"))
    with pytest.raises(ExtractionError, match="all 2 source"):
        extract_all(LLMFactExtractor(client), [document(), document(content="another source")])


def test_extracting_from_no_documents_is_not_a_failure():
    assert extract_all(LLMFactExtractor(FakeClient()), []) == []
