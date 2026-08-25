"""The namespace is the firm-wide contract: it must be strict and immutable."""

import pytest

from firm_mem0.errors import NamespaceError
from firm_mem0.namespace import Layer, Namespace


def make_ns(**overrides):
    defaults = {"repo": "acme-billing-svc"}
    return Namespace(**{**defaults, **overrides})


def test_normalises_identity_values():
    ns = Namespace(repo="Acme-Billing-SVC", firm_owner="  ACME-Eng ")
    assert ns.repo == "acme-billing-svc"
    assert ns.firm_owner == "acme-eng"


def test_repo_is_optional_but_validated_when_present():
    assert make_ns(repo=None).repo is None
    with pytest.raises(NamespaceError):
        make_ns(repo="bad slug")


@pytest.mark.parametrize("bad", ["", "   ", "has space", "bad/slash", "Ünicode"])
def test_rejects_unusable_firm_owner(bad):
    with pytest.raises(NamespaceError):
        make_ns(firm_owner=bad)


def test_there_is_no_per_engineer_or_per_team_layer():
    """Ownership is the codebase and the firm — never a person or a team."""
    assert set(Layer) == {Layer.REPO, Layer.FIRM}


def test_owner_per_layer_is_prefixed_to_prevent_collisions():
    ns = make_ns()
    assert ns.owner_for(Layer.REPO) == "repo:acme-billing-svc"
    assert ns.owner_for(Layer.FIRM) == "firm"


def test_repo_owner_is_derived_from_the_repo_not_from_who_is_calling():
    assert make_ns(repo="quant-oms-service").owner_for(Layer.REPO) == "repo:quant-oms-service"


def test_firm_owner_is_configurable():
    ns = make_ns(firm_owner="acme-eng")
    assert ns.owner_for(Layer.FIRM) == "acme-eng"


def test_repo_layer_maps_repo_to_agent_id():
    ns = make_ns(task="gitlab-issue-4821")
    assert ns.entity_kwargs(Layer.REPO) == {
        "user_id": "repo:acme-billing-svc",
        "agent_id": "acme-billing-svc",
        "run_id": "gitlab-issue-4821",
    }


def test_firm_layer_carries_no_repo_or_task():
    assert make_ns(task="t1").entity_kwargs(Layer.FIRM) == {"user_id": "firm"}


def test_repo_layer_requires_a_repo():
    with pytest.raises(NamespaceError, match="repo"):
        make_ns(repo=None).entity_kwargs(Layer.REPO)


def test_repo_owner_requires_a_repo():
    with pytest.raises(NamespaceError, match="repo"):
        make_ns(repo=None).owner_for(Layer.REPO)


def test_with_task_does_not_mutate_the_original():
    ns = make_ns()
    derived = ns.with_task("gitlab-issue-99")
    assert derived.task == "gitlab-issue-99"
    assert ns.task is None
    assert derived is not ns


def test_with_metadata_merges_immutably():
    ns = make_ns(metadata={"branch": "main"})
    derived = ns.with_metadata(branch="feature/x", service="billing")
    assert derived.metadata == {"branch": "feature/x", "service": "billing"}
    assert ns.metadata == {"branch": "main"}


def test_metadata_is_not_mutable_through_the_namespace():
    ns = make_ns(metadata={"branch": "main"})
    with pytest.raises(TypeError):
        ns.metadata["branch"] = "hacked"  # type: ignore[index]
