"""Repo slug resolution must be deterministic across machines and checkouts."""

import subprocess

import pytest

from firm_mem0.repo import DEFAULT_REPO_SLUG, resolve_repo_slug, slug_from_remote_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/mem0ai/mem0.git", "mem0ai-mem0"),
        ("https://gitlab.com/acme/platform/billing-svc.git", "platform-billing-svc"),
        ("git@gitlab.com:acme/billing-svc.git", "acme-billing-svc"),
        ("git@github.com-work:acme/billing-svc.git", "acme-billing-svc"),
        ("ssh://git@gitlab.com/acme/billing-svc.git", "acme-billing-svc"),
        ("git://gitlab.com/acme/billing-svc", "acme-billing-svc"),
        ("https://gitlab.com/acme/Billing-SVC.git", "acme-billing-svc"),
        ("  git@gitlab.com:acme/billing-svc.git  ", "acme-billing-svc"),
    ],
)
def test_slug_from_remote_url_is_host_independent(url, expected):
    assert slug_from_remote_url(url) == expected


def test_slug_from_remote_url_rejects_unusable_input():
    assert slug_from_remote_url("") is None
    assert slug_from_remote_url("   ") is None
    assert slug_from_remote_url("https://") is None


def test_env_override_wins_over_git():
    def runner(_cwd):  # pragma: no cover - must not be called
        raise AssertionError("git should not be consulted when the override is set")

    slug = resolve_repo_slug(cwd="/x/y", env={"FIRM_MEM0_REPO": "pinned-repo"}, runner=runner)
    assert slug == "pinned-repo"


def test_resolves_from_git_remote():
    slug = resolve_repo_slug(
        cwd="/x/y",
        env={},
        runner=lambda _cwd: "git@gitlab.com:acme/billing-svc.git",
    )
    assert slug == "acme-billing-svc"


def test_falls_back_to_directory_basename_without_remote():
    slug = resolve_repo_slug(cwd="/x/y/Billing_Svc", env={}, runner=lambda _cwd: "")
    assert slug == "billing_svc"


def test_falls_back_when_git_is_unavailable():
    def runner(_cwd):
        raise subprocess.CalledProcessError(128, "git")

    assert resolve_repo_slug(cwd="/x/y/svc", env={}, runner=runner) == "svc"


def test_last_resort_slug_is_stable():
    assert resolve_repo_slug(cwd="/", env={}, runner=lambda _cwd: "") == DEFAULT_REPO_SLUG
