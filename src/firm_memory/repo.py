"""Deterministic repo identity.

The repo slug is a scope axis (:mod:`firm_memory.scope`), so it must resolve to
the same value on every machine and survive a checkout being moved or renamed.
Deriving it from the git remote achieves that with zero per-developer setup; the
directory basename is only a last resort.

This is a **platform** concern, not a provider one: two providers must agree on
what ``oms`` means, or a migration silently repartitions the pool.

The algorithm intentionally matches the one used by the mem0 editor plugin
(``integrations/mem0-plugin/scripts/_project.py``), so memory written by the
plugin and by application code lands in the same scope.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping

DEFAULT_REPO_SLUG = "unknown-repo"
REPO_ENV_VAR = "FIRM_MEMORY_REPO"
#: Honoured so deployments predating the platform rename keep working.
LEGACY_REPO_ENV_VAR = "FIRM_MEM0_REPO"

_PROTOCOL_PREFIXES = ("https://", "http://", "ssh://", "git://")
_UNSAFE_CHARS = re.compile(r"[^a-z0-9._-]+")


def slug_from_remote_url(url: str) -> str | None:
    """Convert a git remote URL into an ``owner-repo`` slug.

    Host is deliberately dropped so the same project cloned over SSH, HTTPS, or
    through a host alias yields one identity. Returns ``None`` when the URL
    carries no usable path.
    """
    slug = (url or "").strip()
    if not slug:
        return None

    if slug.endswith(".git"):
        slug = slug[: -len(".git")]

    for prefix in _PROTOCOL_PREFIXES:
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    else:
        slug = re.sub(r"^git@", "", slug)

    # SSH form uses "host:path" — normalise that separator before splitting.
    slug = slug.replace(":", "/", 1)

    parts = [part for part in slug.split("/") if part]
    if len(parts) >= 2:
        owner, repo = parts[-2], parts[-1]
        candidate = f"{owner}-{repo}"
    elif parts:
        candidate = parts[-1]
    else:
        return None

    return _normalise(candidate) or None


def _read_git_remote(cwd: str) -> str:
    """Return the ``origin`` remote URL, or an empty string when there is none."""
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=5,
    )
    return result.stdout.strip()


def resolve_repo_slug(
    cwd: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[[str], str] | None = None,
) -> str:
    """Resolve the repo slug for *cwd*.

    Resolution order: explicit ``FIRM_MEMORY_REPO`` override (or the legacy
    ``FIRM_MEM0_REPO``), then the git ``origin`` remote, then the directory
    basename, then :data:`DEFAULT_REPO_SLUG`. ``runner`` is injectable for
    testing.
    """
    cwd = cwd if cwd is not None else os.getcwd()
    env = env if env is not None else os.environ
    runner = runner or _read_git_remote

    override = (env.get(REPO_ENV_VAR) or env.get(LEGACY_REPO_ENV_VAR) or "").strip()
    if override:
        return _normalise(override) or DEFAULT_REPO_SLUG

    try:
        remote_url = runner(cwd)
    except (subprocess.SubprocessError, OSError):
        # No git, not a repo, or no remote — fall through to the basename.
        remote_url = ""

    if remote_url:
        slug = slug_from_remote_url(remote_url)
        if slug:
            return slug

    return _normalise(os.path.basename(cwd.rstrip("/"))) or DEFAULT_REPO_SLUG


def _normalise(value: str) -> str:
    """Lowercase and strip characters that would break filter round-tripping."""
    return _UNSAFE_CHARS.sub("-", value.strip().lower()).strip("-")
