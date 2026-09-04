"""mem0's extractor, used for extraction only — never for its write."""

import pytest

from firm_memory.errors import ExtractionError
from firm_memory.ingestion.extraction import SourceDocument
from firm_memory.models import MemoryStatus, MemoryTier
from firm_memory.provenance import Provenance, Source
from firm_memory.providers.mem0.extraction import Mem0FactExtractor
from firm_memory.scope import MemoryScope
from firm_memory.taxonomy import MemoryType

DISCUSSION = "The exchange rejects cash-strategy orders after 15:20, so the OMS stops sending."

pytest.importorskip("mem0.configs.prompts", reason="mem0ai not installed")


class FakeLLM:
    """Returns scripted responses and records every prompt it was given."""

    def __init__(self, *responses, raises=None):
        self.responses = list(responses)
        self.calls = []
        self._raises = raises

    def generate_response(self, messages, **kwargs):
        if self._raises:
            raise self._raises
        self.calls.append(messages)
        return self.responses.pop(0) if self.responses else "{}"


class FakeProvider:
    """A provider stand-in that records whether anything tried to write."""

    def __init__(self, llm, context=()):
        self.backend = type("Backend", (), {"llm": llm})()
        self.extraction_instructions = "FIRM TAXONOMY INSTRUCTIONS"
        self._context = list(context)
        self.writes = []

    def extraction_context(self, scope, query, *, limit=10):
        return self._context

    def insert(self, memory):  # pragma: no cover - must never be called
        self.writes.append(memory)
        raise AssertionError("extraction must not write to the provider")


def document(**overrides):
    defaults = {
        "content": DISCUSSION,
        "scope": MemoryScope(domains=("execution",), repos=("oms",)),
        "provenance": Provenance(source=Source.MERGE_REQUEST, reference="mr-4821"),
    }
    return SourceDocument(**{**defaults, **overrides})


def extracted(*texts):
    return '{"memory": [' + ", ".join(f'{{"id": "{i}", "text": "{t}"}}' for i, t in enumerate(texts)) + "]}"


def classified(*entries):
    body = ", ".join(
        f'{{"index": {i}, "type": "{t}", "confidence": {c}}}' for i, t, c in entries
    )
    return '{"classified": [' + body + "]}"


# --- the guarantee -----------------------------------------------------------


def test_extraction_never_writes_to_the_provider():
    """The whole point of splitting mem0's add() in half."""
    provider = FakeProvider(
        FakeLLM(extracted("Cash strategies stop at 15:20."), classified((0, "business_rules", 0.9)))
    )
    Mem0FactExtractor(provider).extract(document())
    assert provider.writes == []


def test_candidates_are_proposed_and_unsaved():
    provider = FakeProvider(
        FakeLLM(extracted("Cash strategies stop at 15:20."), classified((0, "business_rules", 0.9)))
    )
    [candidate] = Mem0FactExtractor(provider).extract(document())

    assert candidate.status is MemoryStatus.PROPOSED
    assert candidate.id is None


# --- mem0's own extractor is what runs ---------------------------------------


def test_mem0s_own_extraction_prompt_is_used():
    from mem0.configs.prompts import ADDITIVE_EXTRACTION_PROMPT

    llm = FakeLLM(extracted("Cash strategies stop at 15:20."), classified((0, "business_rules", 0.9)))
    Mem0FactExtractor(FakeProvider(llm)).extract(document())

    system_prompt = llm.calls[0][0]["content"]
    assert system_prompt == ADDITIVE_EXTRACTION_PROMPT


def test_the_firm_taxonomy_steers_mem0s_extractor():
    llm = FakeLLM(extracted("x"), classified((0, "business_rules", 0.9)))
    Mem0FactExtractor(FakeProvider(llm)).extract(document())

    assert "FIRM TAXONOMY INSTRUCTIONS" in llm.calls[0][1]["content"]


def test_existing_memories_are_shown_so_extraction_deduplicates():
    """The dedup-aware behaviour that makes mem0's extractor worth reusing."""
    llm = FakeLLM(extracted("A genuinely new fact."), classified((0, "business_rules", 0.9)))
    provider = FakeProvider(llm, context=[("m1", "Cash strategies stop at 15:20.")])
    Mem0FactExtractor(provider).extract(document())

    assert "Cash strategies stop at 15:20." in llm.calls[0][1]["content"]


def test_real_ids_are_hidden_behind_sequential_indices():
    """mem0's anti-hallucination measure, preserved."""
    llm = FakeLLM(extracted("new"), classified((0, "business_rules", 0.9)))
    provider = FakeProvider(llm, context=[("d9f1-uuid-secret", "An existing memory.")])
    Mem0FactExtractor(provider).extract(document())

    assert "d9f1-uuid-secret" not in llm.calls[0][1]["content"]


def test_extraction_proceeds_when_context_cannot_be_loaded():
    """Duplicates the approval queue catches; a lost run it cannot."""

    class NoContext(FakeProvider):
        def extraction_context(self, scope, query, *, limit=10):
            return []

    provider = NoContext(FakeLLM(extracted("A fact."), classified((0, "business_rules", 0.9))))
    assert len(Mem0FactExtractor(provider).extract(document())) == 1


# --- typing against the firm taxonomy ----------------------------------------


def test_extracted_facts_are_typed_by_a_second_call():
    llm = FakeLLM(extracted("Cash strategies stop at 15:20."), classified((0, "business_rules", 0.95)))
    [candidate] = Mem0FactExtractor(FakeProvider(llm)).extract(document())

    assert candidate.type is MemoryType.BUSINESS_RULE
    assert candidate.confidence == 0.95


def test_all_facts_are_classified_in_one_batched_call():
    """Extraction already costs a round trip; per-fact typing would multiply it."""
    llm = FakeLLM(
        extracted("Fact one.", "Fact two.", "Fact three."),
        classified((0, "business_rules", 0.9), (1, "bug_fixes", 0.8), (2, "coding_conventions", 0.7)),
    )
    candidates = Mem0FactExtractor(FakeProvider(llm)).extract(document())

    assert len(candidates) == 3
    assert len(llm.calls) == 2


def test_a_personal_fact_is_dropped_because_it_fits_no_category():
    """mem0's prompt is tuned for personal profiles; the taxonomy is the guard."""
    llm = FakeLLM(
        extracted("User prefers tabs over spaces.", "Cash strategies stop at 15:20."),
        classified((0, "none", 0.9), (1, "business_rules", 0.9)),
    )
    [candidate] = Mem0FactExtractor(FakeProvider(llm)).extract(document())
    assert candidate.type is MemoryType.BUSINESS_RULE


def test_low_confidence_facts_do_not_reach_a_reviewer():
    llm = FakeLLM(
        extracted("Maybe the cache.", "Cash strategies stop at 15:20."),
        classified((0, "bug_fixes", 0.05), (1, "business_rules", 0.9)),
    )
    [candidate] = Mem0FactExtractor(FakeProvider(llm), min_confidence=0.35).extract(document())
    assert candidate.confidence == 0.9


def test_a_fact_the_classifier_skipped_is_dropped_not_guessed():
    llm = FakeLLM(extracted("One.", "Two."), classified((0, "business_rules", 0.9)))
    assert len(Mem0FactExtractor(FakeProvider(llm)).extract(document())) == 1


# --- shape and failure -------------------------------------------------------


def test_candidates_inherit_scope_provenance_and_tier():
    llm = FakeLLM(extracted("A fact."), classified((0, "review_feedback", 0.9)))
    [candidate] = Mem0FactExtractor(FakeProvider(llm)).extract(
        document(tier=MemoryTier.EPISODIC, task="mr-4821")
    )

    assert candidate.scope == MemoryScope(domains=("execution",), repos=("oms",))
    assert candidate.provenance.reference == "mr-4821"
    assert candidate.tier is MemoryTier.EPISODIC and candidate.task == "mr-4821"


def test_extracting_nothing_costs_no_classification_call():
    llm = FakeLLM('{"memory": []}')
    assert Mem0FactExtractor(FakeProvider(llm)).extract(document()) == []
    assert len(llm.calls) == 1


def test_the_number_of_facts_is_bounded():
    llm = FakeLLM(
        extracted(*[f"Fact {i}." for i in range(30)]),
        classified(*[(i, "task_learnings", 0.9) for i in range(30)]),
    )
    assert len(Mem0FactExtractor(FakeProvider(llm), max_facts=5).extract(document())) == 5


def test_a_fenced_response_is_still_parsed():
    llm = FakeLLM(
        f"```json\n{extracted('A fact.')}\n```",
        classified((0, "business_rules", 0.9)),
    )
    assert len(Mem0FactExtractor(FakeProvider(llm)).extract(document())) == 1


def test_a_response_without_a_memory_list_is_a_typed_error():
    with pytest.raises(ExtractionError, match="no 'memory' list"):
        Mem0FactExtractor(FakeProvider(FakeLLM('{"result": "ok"}'))).extract(document())


def test_an_llm_failure_is_wrapped_with_context():
    provider = FakeProvider(FakeLLM(raises=RuntimeError("gateway timeout")))
    with pytest.raises(ExtractionError, match="gateway timeout"):
        Mem0FactExtractor(provider).extract(document())
