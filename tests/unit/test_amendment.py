"""A reviewer may correct a candidate as they endorse it — visibly."""

import pytest

from firm_memory import MemoryScope, MemoryType
from firm_memory.errors import ScopeError, TaxonomyError
from firm_memory.ingestion.amendment import AMENDED_BY, AMENDED_FIELDS, Amendment
from tests.conftest import make_memory


def test_an_empty_amendment_changes_nothing():
    original = make_memory()
    assert Amendment().is_empty
    assert Amendment().apply(original, editor="ashish") is original


def test_amending_the_wording_leaves_the_original_untouched():
    """Immutability is the point: the queue still holds what the extractor said."""
    original = make_memory(content="Order IDs come from the sequencer.")

    amended = Amendment(content="Order IDs come from the sequencer, never the service.").apply(
        original, editor="ashish"
    )

    assert amended.content == "Order IDs come from the sequencer, never the service."
    assert original.content == "Order IDs come from the sequencer."


def test_the_type_can_be_corrected_by_either_spelling():
    original = make_memory(type=MemoryType.TASK_LEARNING)
    amended = Amendment(type="business_rules").apply(original, editor="ashish")
    assert amended.type is MemoryType.BUSINESS_RULE


def test_the_scope_can_be_widened_when_a_fact_reaches_further_than_extraction_thought():
    original = make_memory(scope=MemoryScope.for_repo("oms"))

    amended = Amendment(scope=MemoryScope(domains=("execution",), repos=("oms", "gateway"))).apply(
        original, editor="ashish"
    )

    assert amended.scope.repos == ("oms", "gateway")
    assert amended.scope.domains == ("execution",)


def test_provenance_pointers_are_amendable_without_losing_the_rest():
    original = make_memory()
    amended = Amendment(reference="mr-4821", evidence="docs/cutoffs.md").apply(original, editor="ashish")

    assert amended.provenance.reference == "mr-4821"
    assert amended.provenance.evidence == "docs/cutoffs.md"
    assert amended.provenance.source == original.provenance.source


def test_an_amendment_records_who_made_it_and_what_moved():
    """A memory that reads oddly later must be traceable to the edit."""
    amended = Amendment(content="A clearer statement of the rule.", type="coding_conventions").apply(
        make_memory(), editor="ashish"
    )

    assert amended.metadata[AMENDED_BY] == "ashish"
    assert amended.metadata[AMENDED_FIELDS] == "content,type"


def test_amending_preserves_metadata_the_extractor_set():
    amended = Amendment(content="A clearer statement.").apply(
        make_memory(metadata={"branch": "main"}), editor="ashish"
    )
    assert amended.metadata["branch"] == "main"


def test_an_amendment_is_validated_exactly_as_a_fresh_proposal_would_be():
    with pytest.raises(TaxonomyError):
        Amendment(type="user_preferences").apply(make_memory(), editor="ashish")


def test_an_amendment_cannot_leave_a_memory_unreachable():
    with pytest.raises(ScopeError):
        Amendment(scope=MemoryScope.from_dict({"firm": False, "domains": [], "repos": []})).apply(
            make_memory(), editor="ashish"
        )


def test_the_write_path_axis_is_not_a_review_opinion():
    """tier and task describe how a memory was written, not what a reviewer thinks of it."""
    assert not hasattr(Amendment(), "tier")
    assert not hasattr(Amendment(), "task")


# --- the JSON a review UI submits --------------------------------------------


def test_absent_and_null_fields_both_mean_unchanged():
    assert Amendment.from_dict({}).is_empty
    assert Amendment.from_dict({"content": None, "type": None}).is_empty
    assert Amendment.from_dict(None).is_empty


def test_a_blank_text_box_means_unchanged_not_erase():
    """Emptying a field is far more likely a slip than an instruction to wipe it."""
    assert Amendment.from_dict({"content": "   "}).is_empty


def test_a_submitted_amendment_round_trips_into_the_edit():
    amendment = Amendment.from_dict(
        {
            "content": "MCX orders always route through Risk Engine A.",
            "type": "business_rules",
            "scope": {"firm": False, "domains": ["execution"], "repos": ["oms"]},
            "confidence": 0.9,
            "reference": "mr-4821",
        }
    )
    amended = amendment.apply(make_memory(), editor="ashish")

    assert amended.content == "MCX orders always route through Risk Engine A."
    assert amended.type is MemoryType.BUSINESS_RULE
    assert amended.scope == MemoryScope(domains=("execution",), repos=("oms",))
    assert amended.confidence == 0.9
    assert amended.provenance.reference == "mr-4821"
