"""The agent-facing MCP surface.

Tested against the tool functions directly: they hold the whole contract, and
the server above them is only transport.
"""

import pytest

from firm_memory import MemoryScope, MemoryType
from firm_memory.lifecycle import approve
from firm_memory.mcp.tools import TOOL_DESCRIPTIONS, MemoryTools, taxonomy_reference
from tests.conftest import REPO, make_memory

RULE = "Cash strategies stop sending at 15:20 because the exchange rejects after that."


@pytest.fixture
def tools(memory):
    return MemoryTools(memory)


def store(memory, **overrides):
    return memory.commit(approve(make_memory(**overrides), approver="lead"))


# --- surface -----------------------------------------------------------------


def test_the_v1_tool_surface_is_exactly_the_four_designed_tools():
    assert set(TOOL_DESCRIPTIONS) == {"memory_search", "memory_get", "memory_propose", "memory_correct"}


def test_the_search_description_tells_the_agent_the_codebase_wins():
    """It is the only place an agent learns this rule."""
    description = TOOL_DESCRIPTIONS["memory_search"].lower()
    assert "the code is right" in description
    assert "does not describe current code" in description


def test_the_propose_description_says_it_does_not_write():
    assert "does NOT create an active memory" in TOOL_DESCRIPTIONS["memory_propose"]


def test_the_taxonomy_is_published_so_an_agent_need_not_guess_a_type():
    reference = taxonomy_reference()
    assert {entry["type"] for entry in reference} == {member.value for member in MemoryType}
    assert all(entry["description"] for entry in reference)


# --- memory_search -----------------------------------------------------------


def test_search_returns_citable_hits(tools, memory):
    stored = store(memory, content=RULE, type=MemoryType.BUSINESS_RULE)

    result = tools.memory_search("when do cash strategies stop sending")
    assert result["count"] == 1
    [hit] = result["memories"]
    assert hit["id"] == stored.id
    assert hit["type"] == "business_rules"
    assert hit["content"] == RULE


def test_search_defaults_to_this_repo_its_domains_and_the_firm(tools):
    assert tools.memory_search("anything")["scope"] == {
        "firm": True,
        "domains": ["execution"],
        "repos": [REPO],
    }


def test_search_can_be_pointed_at_other_repos(tools, memory):
    store(memory, content="Analytics jobs are scheduled by Airflow.",
          scope=MemoryScope.for_repo("analytics"), type=MemoryType.TOOLING_SETUP)

    assert tools.memory_search("analytics jobs are scheduled by airflow", repos=["analytics"])["count"] == 1


def test_search_reports_a_bad_type_instead_of_throwing(tools):
    """A tool that throws takes the agent's whole turn with it."""
    result = tools.memory_search("q", types=["food_preferences"])
    assert "error" in result and result["error_type"] == "TaxonomyError"


def test_a_provider_outage_looks_like_an_empty_result_to_the_agent(memory):
    class Broken:
        name = "broken"

        def search(self, *_a, **_k):
            raise RuntimeError("connection refused")

        def get(self, _id):
            raise RuntimeError("connection refused")

        def insert(self, m):
            raise RuntimeError("connection refused")

        def update(self, m):
            raise RuntimeError("connection refused")

    from firm_memory import FirmMemory, Settings

    with FirmMemory(Broken(), settings=Settings(), scope=MemoryScope.firm_wide()) as api:
        result = MemoryTools(api).memory_search("anything")
        assert result["count"] == 0 and "error" not in result


# --- memory_get --------------------------------------------------------------


def test_get_resolves_a_citation(tools, memory):
    stored = store(memory, content=RULE, type=MemoryType.BUSINESS_RULE)
    result = tools.memory_get(stored.id)
    assert result["found"] is True
    assert result["memory"]["content"] == RULE


def test_get_reports_absence_rather_than_failing(tools):
    assert tools.memory_get("no-such-id") == {"found": False, "memory": None}


# --- memory_propose ----------------------------------------------------------


def test_propose_queues_a_candidate_and_says_so(tools):
    result = tools.memory_propose(RULE, "BUSINESS_RULE", reference="mr-4821")

    assert result["accepted"] is False
    assert result["requires_human_approval"] is True
    assert result["candidate_id"]
    assert result["reason"]


def test_a_proposed_memory_is_not_searchable(tools):
    tools.memory_propose(RULE, "BUSINESS_RULE")
    assert tools.memory_search("when do cash strategies stop sending")["count"] == 0


def test_propose_accepts_either_spelling_of_a_type(tools):
    assert "error" not in tools.memory_propose(RULE, "business_rules")


def test_propose_rejects_a_type_outside_the_taxonomy_with_a_readable_error(tools):
    result = tools.memory_propose("Ashish prefers tabs.", "user_preferences")
    assert result["error_type"] == "TaxonomyError"
    assert "Allowed" in result["error"]


def test_propose_rejects_empty_content(tools):
    assert tools.memory_propose("   ", "BUSINESS_RULE")["error_type"] == "InvalidInputError"


def test_an_agent_can_scope_a_proposal_across_repos(tools):
    result = tools.memory_propose(
        "Order routing always passes through the risk engine.",
        "BUSINESS_RULE",
        repos=["oms", "gateway"],
        domains=["execution"],
    )
    assert result["memory"]["scope"] == {"firm": False, "domains": ["execution"], "repos": ["oms", "gateway"]}


def test_an_episodic_proposal_must_name_its_task(tools):
    assert tools.memory_propose(RULE, "REVIEW_PATTERN", tier="episodic")["error_type"] == "InvalidInputError"


# --- memory_correct ----------------------------------------------------------


def test_correct_flags_a_memory_without_deleting_it(tools, memory):
    stored = store(memory, content=RULE, type=MemoryType.BUSINESS_RULE)

    result = tools.memory_correct(stored.id, "the cut-off moved to 15:25", reporter="eng")
    assert result["found"] is True
    assert result["memory"]["status"] == "disputed"
    assert tools.memory_get(stored.id)["found"] is True


def test_correct_requires_a_reason(tools, memory):
    stored = store(memory, content=RULE)
    assert tools.memory_correct(stored.id, "  ")["error_type"] == "InvalidInputError"


def test_correcting_an_unknown_memory_reports_absence(tools):
    assert tools.memory_correct("no-such-id", "stale") == {"found": False, "memory": None}
