"""The review service fails to start rather than starting insecurely.

The token does two jobs — it admits you, and it says who is approving — so the
configuration has to refuse anything that would leave either one ambiguous.
"""

import pytest

from firm_memory.errors import ConfigurationError
from firm_memory.review.settings import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_RATE_LIMIT,
    MIN_TOKEN_LENGTH,
    ReviewSettings,
)

TOKEN = "a" * 64
OTHER = "b" * 64
BASE = {"FIRM_MEMORY_REVIEW_TOKEN": TOKEN, "FIRM_MEMORY_REVIEW_APPROVER": "ashish"}


# --- refusing to start -------------------------------------------------------


def test_no_token_at_all_refuses_to_start_and_says_how_to_fix_it():
    """Approving is a write to firm knowledge; it never runs unauthenticated."""
    with pytest.raises(ConfigurationError, match="openssl rand"):
        ReviewSettings.from_env({})


def test_a_token_with_no_reviewer_name_is_refused():
    """An approval nobody can be traced to is the thing the token exists to prevent."""
    with pytest.raises(ConfigurationError, match="FIRM_MEMORY_REVIEW_APPROVER is required"):
        ReviewSettings.from_env({"FIRM_MEMORY_REVIEW_TOKEN": TOKEN})


def test_a_short_token_is_refused():
    with pytest.raises(ConfigurationError, match=f"at least {MIN_TOKEN_LENGTH}"):
        ReviewSettings.from_env({**BASE, "FIRM_MEMORY_REVIEW_TOKEN": "hunter2"})


def test_two_reviewers_sharing_one_token_is_refused():
    """It would make an approval untraceable to either of them."""
    with pytest.raises(ConfigurationError, match="One token per reviewer"):
        ReviewSettings.from_env({"FIRM_MEMORY_REVIEW_TOKENS": f"ashish:{TOKEN},priya:{TOKEN}"})


def test_an_entry_that_is_not_name_colon_token_is_refused():
    with pytest.raises(ConfigurationError, match="is not 'name:token'"):
        ReviewSettings.from_env({"FIRM_MEMORY_REVIEW_TOKENS": TOKEN})


def test_a_token_with_a_blank_name_is_refused():
    with pytest.raises(ConfigurationError, match="no reviewer name"):
        ReviewSettings.from_env({"FIRM_MEMORY_REVIEW_TOKENS": f"  :{TOKEN}"})


# --- resolving a reviewer ----------------------------------------------------


def test_a_single_token_names_its_one_reviewer():
    settings = ReviewSettings.from_env(BASE)
    assert settings.reviewer_for(TOKEN) == "ashish"


def test_a_team_gets_one_token_each():
    settings = ReviewSettings.from_env(
        {"FIRM_MEMORY_REVIEW_TOKENS": f"ashish:{TOKEN}, priya:{OTHER}"}
    )
    assert settings.reviewer_for(TOKEN) == "ashish"
    assert settings.reviewer_for(OTHER) == "priya"


def test_an_unissued_token_names_nobody():
    assert ReviewSettings.from_env(BASE).reviewer_for("c" * 64) is None


def test_the_list_form_wins_over_the_single_form():
    """One place to look when both are set, rather than a silent merge."""
    settings = ReviewSettings.from_env(
        {**BASE, "FIRM_MEMORY_REVIEW_TOKENS": f"priya:{OTHER}"}
    )
    assert settings.reviewer_for(OTHER) == "priya"
    assert settings.reviewer_for(TOKEN) is None


def test_the_reviewer_map_cannot_be_mutated_after_construction():
    settings = ReviewSettings.from_env(BASE)
    with pytest.raises(TypeError):
        settings.reviewers["forged"] = "someone"


# --- the rest of the configuration -------------------------------------------


def test_it_binds_to_localhost_unless_told_otherwise():
    """Exposing an internal review tool must be an explicit act."""
    settings = ReviewSettings.from_env(BASE)
    assert settings.host == DEFAULT_HOST
    assert settings.port == DEFAULT_PORT
    assert settings.rate_limit_per_minute == DEFAULT_RATE_LIMIT


def test_host_and_port_are_configurable():
    settings = ReviewSettings.from_env(
        {**BASE, "FIRM_MEMORY_REVIEW_HOST": "0.0.0.0", "FIRM_MEMORY_REVIEW_PORT": "9000"}
    )
    assert (settings.host, settings.port) == ("0.0.0.0", 9000)


@pytest.mark.parametrize("port", ["not-a-port", "0", "70000"])
def test_an_unusable_port_is_refused_at_startup(port):
    with pytest.raises(ConfigurationError, match="FIRM_MEMORY_REVIEW_PORT"):
        ReviewSettings.from_env({**BASE, "FIRM_MEMORY_REVIEW_PORT": port})


@pytest.mark.parametrize("limit", ["nope", "0", "-5"])
def test_an_unusable_rate_limit_is_refused_at_startup(limit):
    with pytest.raises(ConfigurationError, match="FIRM_MEMORY_REVIEW_RATE_LIMIT"):
        ReviewSettings.from_env({**BASE, "FIRM_MEMORY_REVIEW_RATE_LIMIT": limit})


def test_no_reviewer_identity_becomes_a_scoping_axis():
    """A person may be attributed on a memory, never used to partition the pool."""
    fields = set(ReviewSettings.__dataclass_fields__)
    assert not any(term in field for field in fields for term in ("engineer", "team", "scope"))
