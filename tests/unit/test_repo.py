"""Repo slug resolution must be deterministic across machines and checkouts."""

import subprocess

import pytest

from firm_memory.repo import DEFAULT_REPO_SLUG, resolve_repo_slug, slug_from_remote_url


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
    """The same project over SSH, HTTPS or a host alias must be one identity."""
    assert slug_from_remote_url(url) == expected


def test_slug_from_remote_url_rejects_unusable_input():
    assert slug_from_remote_url("") is None
    assert slug_from_remote_url("   ") is None
    assert slug_from_remote_url("https://") is None


def test_env_override_wins_over_git():
    def runner(_cwd):  # pragma: no cover - must not be called
        raise AssertionError("git should not be consulted when the override is set")

    assert resolve_repo_slug(cwd="/x/y", env={"FIRM_MEMORY_REPO": "pinned-repo"}, runner=runner) == "pinned-repo"


def test_the_pre_platform_override_still_works():
    """An existing deployment must not silently repartition on upgrade."""
    slug = resolve_repo_slug(cwd="/x/y", env={"FIRM_MEM0_REPO": "legacy-repo"}, runner=lambda _c: "")
    assert slug == "legacy-repo"


def test_resolves_from_git_remote():
    slug = resolve_repo_slug(cwd="/x/y", env={}, runner=lambda _cwd: "git@gitlab.com:acme/billing-svc.git")
    assert slug == "acme-billing-svc"


def test_falls_back_to_directory_basename_without_remote():
    assert resolve_repo_slug(cwd="/x/y/Billing_Svc", env={}, runner=lambda _cwd: "") == "billing_svc"


def test_falls_back_when_git_is_unavailable():
    def runner(_cwd):
        raise subprocess.CalledProcessError(128, "git")

    assert resolve_repo_slug(cwd="/x/y/svc", env={}, runner=runner) == "svc"


def test_last_resort_slug_is_stable():
    assert resolve_repo_slug(cwd="/", env={}, runner=lambda _cwd: "") == DEFAULT_REPO_SLUG


def test_git_remote_reader_works_against_a_real_checkout(tmp_path):
    """The default runner must actually read a git remote, not just be mockable."""
    from firm_memory.repo import _read_git_remote

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@gitlab.com:acme/billing-svc.git"], cwd=tmp_path, check=True
    )
    assert _read_git_remote(str(tmp_path)) == "git@gitlab.com:acme/billing-svc.git"


def test_git_remote_reader_raises_without_a_remote(tmp_path):
    from firm_memory.repo import _read_git_remote

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(subprocess.CalledProcessError):
        _read_git_remote(str(tmp_path))
