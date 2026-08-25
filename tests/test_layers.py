"""Layered retrieval builds one OSS filter tree so recall stays a single search call."""

import pytest

from firm_mem0.errors import NamespaceError
from firm_mem0.layers import layer_filter, layered_filters
from firm_mem0.namespace import Layer, Namespace

NS = Namespace(repo="acme-billing-svc")


def test_repo_layer_filter_is_flat():
    assert layer_filter(NS, Layer.REPO) == {
        "user_id": "repo:acme-billing-svc",
        "agent_id": "acme-billing-svc",
    }


def test_firm_layer_filter_has_no_agent_clause():
    assert layer_filter(NS, Layer.FIRM) == {"user_id": "firm"}


def test_task_scoped_filter_includes_run_id():
    ns = NS.with_task("gitlab-issue-4821")
    assert layer_filter(ns, Layer.REPO) == {
        "user_id": "repo:acme-billing-svc",
        "agent_id": "acme-billing-svc",
        "run_id": "gitlab-issue-4821",
    }


def test_metadata_filters_are_flat_keys_not_nested():
    """OSS flattens metadata into the payload; the nested Platform form raises."""
    assert layer_filter(NS, Layer.FIRM, metadata_filters={"type": "coding_conventions"}) == {
        "user_id": "firm",
        "type": "coding_conventions",
    }


def test_metadata_filters_cannot_hijack_scoping():
    with pytest.raises(NamespaceError, match="user_id"):
        layer_filter(NS, Layer.FIRM, metadata_filters={"user_id": "repo:some-other-repo"})


def test_single_layer_avoids_a_pointless_or_wrapper():
    assert layered_filters(NS, [Layer.FIRM]) == {"user_id": "firm"}


def test_multiple_layers_are_unioned_as_flat_branches():
    assert layered_filters(NS, [Layer.REPO, Layer.FIRM]) == {
        "OR": [
            {"user_id": "repo:acme-billing-svc", "agent_id": "acme-billing-svc"},
            {"user_id": "firm"},
        ]
    }


def test_duplicate_layers_are_collapsed_but_order_preserved():
    result = layered_filters(NS, [Layer.FIRM, Layer.REPO, Layer.FIRM])
    assert result["OR"] == [
        {"user_id": "firm"},
        {"user_id": "repo:acme-billing-svc", "agent_id": "acme-billing-svc"},
    ]


def test_repo_layer_without_repo_fails_loudly():
    with pytest.raises(NamespaceError):
        layered_filters(Namespace(), [Layer.REPO])


def test_at_least_one_layer_is_required():
    with pytest.raises(ValueError):
        layered_filters(NS, [])
