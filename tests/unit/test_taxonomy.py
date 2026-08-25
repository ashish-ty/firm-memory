"""The taxonomy is the firm's vocabulary: enforced, described, and stable."""

import pytest

from firm_memory.errors import TaxonomyError
from firm_memory.taxonomy import (
    CODING_CATEGORIES,
    EXCLUSIONS,
    MemoryType,
    coerce_type,
    describe,
    fact_extraction_instructions,
)


def test_every_type_has_a_description_driving_extraction():
    """A type with no description would be untaggable by the extractor."""
    assert {category.type for category in CODING_CATEGORIES} == set(MemoryType)
    assert all(describe(member) for member in MemoryType)


def test_wire_values_are_preserved_for_memory_already_written():
    """Renaming a value would orphan every memory the plugin has stored."""
    assert MemoryType.ARCHITECTURE_DECISION.value == "architecture_decisions"
    assert MemoryType.REJECTED_APPROACH.value == "anti_patterns"
    assert MemoryType.CONVENTION.value == "coding_conventions"
    assert MemoryType.REVIEW_PATTERN.value == "review_feedback"


def test_platform_types_from_the_design_are_present():
    for member in ("BUSINESS_RULE", "PRODUCTION_ISSUE", "OWNERSHIP", "TERMINOLOGY"):
        assert hasattr(MemoryType, member)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("anti_patterns", MemoryType.REJECTED_APPROACH),
        ("REJECTED_APPROACH", MemoryType.REJECTED_APPROACH),
        ("  Business_Rules  ", MemoryType.BUSINESS_RULE),
        (MemoryType.OWNERSHIP, MemoryType.OWNERSHIP),
    ],
)
def test_both_spellings_of_a_type_resolve(given, expected):
    assert coerce_type(given) is expected


@pytest.mark.parametrize("bad", ["", "   ", "food_preferences", "hobbies"])
def test_types_outside_the_taxonomy_are_rejected(bad):
    with pytest.raises(TaxonomyError):
        coerce_type(bad)


def test_instructions_carry_every_category_and_every_exclusion():
    instructions = fact_extraction_instructions()
    for category in CODING_CATEGORIES:
        assert category.name in instructions
    for exclusion in EXCLUSIONS:
        assert exclusion in instructions


def test_instructions_forbid_code_secrets_and_per_person_facts():
    """These three exclusions are the ones that keep the pool queryable."""
    instructions = fact_extraction_instructions().lower()
    assert "source code" in instructions
    assert "secret" in instructions
    assert "individual engineers or teams" in instructions
