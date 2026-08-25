"""Bootstrap path: env + git checkout -> a fully scoped facade."""

import subprocess

import pytest

from firm_mem0 import FirmMemory, Layer
from firm_mem0.errors import InvalidInputError, MemoryBackendError
from firm_mem0.repo import _read_git_remote

ENV = {
    "FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0",
    "FIRM_MEM0_REPO": "acme-billing-svc",
}


class RecordingMemory:
    def __init__(self):
        self.add_calls = []

    def add(self, messages, **kwargs):
        self.add_calls.append((messages, kwargs))
        return {"results": []}

    def delete(self, memory_id):
        raise RuntimeError("backend down")


def test_from_env_builds_a_scoped_facade():
    captured = {}

    def factory(config):
        captured["config"] = config
        return RecordingMemory()

    facade = FirmMemory.from_env(env=ENV, memory_factory=factory)

    assert facade.namespace.repo == "acme-billing-svc"
    assert captured["config"]["vector_store"]["provider"] == "pgvector"


def test_from_env_writes_are_scoped_without_any_caller_effort():
    facade = FirmMemory.from_env(env=ENV, memory_factory=lambda _c: RecordingMemory())
    facade.remember("We pin psycopg to 3.x")

    _messages, kwargs = facade._memory.add_calls[0]
    assert kwargs["user_id"] == "repo:acme-billing-svc"
    assert kwargs["agent_id"] == "acme-billing-svc"


def test_from_env_surfaces_backend_init_failure_as_typed_error():
    def factory(_config):
        raise OSError("could not connect to pgvector")

    with pytest.raises(MemoryBackendError, match="could not connect"):
        FirmMemory.from_env(env=ENV, memory_factory=factory)


def test_from_env_propagates_config_errors_unwrapped():
    from firm_mem0.errors import ConfigurationError

    env = {k: v for k, v in ENV.items() if k != "FIRM_MEM0_PG_DSN"}
    with pytest.raises(ConfigurationError):
        FirmMemory.from_env(env=env, memory_factory=lambda _c: RecordingMemory())


def test_forget_wraps_backend_failure():
    facade = FirmMemory.from_env(env=ENV, memory_factory=lambda _c: RecordingMemory())
    with pytest.raises(MemoryBackendError, match="backend down"):
        facade.forget("m1")


def test_forget_rejects_empty_id():
    facade = FirmMemory.from_env(env=ENV, memory_factory=lambda _c: RecordingMemory())
    with pytest.raises(InvalidInputError):
        facade.forget("  ")


def test_remember_wraps_backend_failure():
    class Broken:
        def add(self, *_a, **_k):
            raise RuntimeError("write rejected")

    facade = FirmMemory.from_env(env=ENV, memory_factory=lambda _c: Broken())
    with pytest.raises(MemoryBackendError, match="write rejected"):
        facade.remember("x", layer=Layer.FIRM)


def test_git_remote_reader_works_against_a_real_checkout(tmp_path):
    """The default runner must actually read a git remote, not just be mockable."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@gitlab.com:acme/billing-svc.git"],
        cwd=tmp_path,
        check=True,
    )
    assert _read_git_remote(str(tmp_path)) == "git@gitlab.com:acme/billing-svc.git"


def test_git_remote_reader_raises_without_a_remote(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with pytest.raises(subprocess.CalledProcessError):
        _read_git_remote(str(tmp_path))
