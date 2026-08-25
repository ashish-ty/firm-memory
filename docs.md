# firm-mem0 — Design Brief & Rollout Plan

**Date:** 2026-07-26
**Status:** wrapper package complete and tested; not yet run against a live database
**Location:** `/Users/ashishtygai/repos/quant/firm-mem0/`

---

## 1. TL;DR

You are rolling memory out across a firm whose AI applications mostly work on
codebases, with some working on GitLab issue boards. mem0 is the right layer for
this, but its defaults are **user-centric** while your workload is
**repo-centric**. Left alone, each application invents its own memory keys and
the pool becomes unqueryable within a quarter.

The recommendation is therefore not "call mem0 from each app." It is:

> **One internal package owns the namespace contract and the backend config.
> Every application imports that package and never touches mem0 directly.**

That package is `firm-mem0`. It is built, tested (95 tests, 97% coverage), and
lints clean. It runs on **self-hosted mem0 OSS** — pgvector store, local
cross-encoder reranker, nothing leaving your network.

---

## 2. The recommended approach

### 2.1 Why a wrapper rather than direct mem0 calls

| Problem if each app calls mem0 directly | How the wrapper solves it |
|---|---|
| Every app picks its own `user_id`/`agent_id` meaning | One namespace contract, enforced in code |
| An agent forgets to scope a write; memory is orphaned | Scoping is injected — an unscoped write is not expressible |
| Changing embedder/store/threshold means editing N apps | One choke point: `config.py` |
| Consumer-oriented auto-categories make code memory untaggable | Coding taxonomy shipped in `taxonomy.py` |
| Platform-shaped filters silently return zero rows on OSS | Correct OSS shapes, locked by contract tests |

### 2.2 The namespace contract

mem0 OSS exposes three entity axes plus free-form metadata. There is **no
`app_id`** on OSS (that is Platform-only), so the axes are spent like this:

| mem0 axis | firm-mem0 meaning | Example |
|---|---|---|
| `user_id` | the memory **owner**, prefixed per layer | `repo:acme-billing-svc`, `firm` |
| `agent_id` | the **repo slug**, derived from the git remote | `acme-billing-svc` |
| `run_id` | the **task** | `gitlab-issue-4821` |
| metadata | filterable axes | `type`, `layer`, `repo`, `branch`, `source` |

**An owner is never a person or a team.** The bot is stateless and serves every
engineer identically, and no team owns a set of use cases, so knowledge belongs
to the codebase and to the firm — not to whoever happened to trigger the call.
A per-person or per-team pool would fragment the same fact into copies no single
query can reach, and would make every answer depend on who asked.

Owners are prefixed (`repo:`) so a bare `user_id` scan cannot silently mix
layers or repos.

### 2.3 Two memory layers

| Layer | Owner | Scope | Written by | Holds |
|---|---|---|---|---|
| `Layer.REPO` | `repo:<slug>` | repo, optionally task | agents (the bulk) | conventions, decisions, gotchas for one codebase |
| `Layer.FIRM` | `firm` | cross-repo conventions | leads, curated | "pnpm only", "Ruff not black" |

`Layer.REPO` sets `user_id` and `agent_id` from the same slug: the first
partitions the layer, the second is the repo scoping axis every filter already
speaks.

**Only `REPO` is task-scoped.** `FIRM` holds facts that must stay retrievable on
the *next* task, so binding it to a `run_id` would strand them. (This was caught
by a failing test during development — my first design had every layer carrying
`run_id`.)

`recall()` unions both layers into **one** `search()` call, not two.

### 2.4 Repo identity is derived, never configured

The repo slug is the primary partition key for code memory, so it must resolve
identically on every machine and survive a checkout being moved or renamed.
`resolve_repo_slug()` reads `git config --get remote.origin.url` and reduces it
to `owner-repo`, dropping the host so SSH, HTTPS, and host-aliased clones all
agree. Resolution order: `FIRM_MEM0_REPO` override → git remote → directory
basename → `unknown-repo`.

Zero per-developer setup. No config file to drift.

### 2.5 Memory is not RAG over your code

The failure mode I most expect at a firm doing code-heavy AI is dumping file
contents and issue bodies into mem0. Memory stores **durable judgements** — why
a library was chosen, which approach failed, what the team agreed. Code
retrieval stays in your embedding/grep layer.

`taxonomy.py` encodes this as explicit exclusions in the fact-extraction
instructions: no source bodies, diffs, stack traces, secrets, or transient
state — only conclusions drawn from them. Without these, transcripts flood the
pool and drown the signal.

---

## 3. What was built

820 lines of source across 8 focused modules, plus 7 test modules.

### 3.1 Source

| File | Lines | Responsibility |
|---|---|---|
| `src/firm_mem0/errors.py` | 28 | Exception hierarchy — all failures derive from `FirmMem0Error` |
| `src/firm_mem0/repo.py` | 112 | Deterministic repo-slug resolution from the git remote |
| `src/firm_mem0/namespace.py` | 129 | The contract: frozen `Namespace`, `Layer`, axis mapping, validation |
| `src/firm_mem0/layers.py` | 76 | OSS-correct filter trees for layered retrieval |
| `src/firm_mem0/taxonomy.py` | 91 | Coding categories + fact-extraction instructions |
| `src/firm_mem0/config.py` | 152 | `Settings.from_env()` + `build_memory_config()` — the choke point |
| `src/firm_mem0/client.py` | 180 | `FirmMemory` facade: `remember` / `recall` / `forget` / `for_task` |
| `src/firm_mem0/__init__.py` | 52 | Public API surface |

Design constraints honoured throughout: immutable data (frozen dataclasses,
`with_*` copy methods, `MappingProxyType` metadata), validation at boundaries,
no silently swallowed errors, every file well under the 800-line limit.

### 3.2 Tests — 95 passing, 97% coverage

| File | Covers |
|---|---|
| `test_repo.py` | Slug algorithm across SSH/HTTPS/host-alias/`git://` forms, fallbacks |
| `test_namespace.py` | Validation, normalisation, axis mapping per layer, immutability |
| `test_layers.py` | Flat filter shapes, layer unioning, scope-hijack rejection |
| `test_config.py` | Env parsing, fail-fast validation, generated config shape |
| `test_client.py` | Injected scoping, provenance metadata, response normalisation, error wrapping |
| `test_bootstrap.py` | `from_env()` end-to-end, real `git init` + remote read |
| `test_oss_filter_contract.py` | **Filters run through mem0's real pipeline down to generated SQL** |

The last file is the one that matters on upgrade. See §6.

### 3.3 Honest gap: not yet run against live infrastructure

Everything above is unit- and contract-tested. What has **not** happened:

- No live PostgreSQL/pgvector connection
- No real embedding or LLM call
- No end-to-end `add()` → `search()` round trip returning actual rows

The generated config was validated against mem0's real Pydantic models
(`MemoryConfig`, `PGVectorConfig`, `RerankerConfig` all accept it), and the
filter shapes were validated through mem0's real preprocessor and pgvector's
real SQL builder. But a smoke test against a live pgvector instance is the
first thing to do before piloting.

---

## 4. What comes from the parent mem0 repo

Three distinct relationships. Worth keeping them separate in your head.

### 4.1 Consumed at runtime — the `mem0ai` PyPI package (a dependency)

`firm-mem0` depends on `mem0ai>=2.0.14`. No mem0 source is vendored.

| What we use | Where it lives upstream |
|---|---|
| `Memory.from_config()`, `.add()`, `.search()`, `.delete()` | `mem0/memory/main.py` |
| `MemoryConfig` — `vector_store`, `llm`, `embedder`, `reranker`, `custom_instructions` | `mem0/configs/base.py` |
| pgvector store (`connection_string`, `collection_name`, `embedding_model_dims`) | `mem0/vector_stores/pgvector.py` |
| `sentence_transformer` reranker (local cross-encoder) | `mem0/reranker/sentence_transformer_reranker.py` |
| Filter pipeline we must satisfy | `Memory._process_metadata_filters`, `pgvector._build_filter_conditions` |

### 4.2 Ported — ideas and algorithms reimplemented, not copied

These came from `integrations/mem0-plugin/`, which is mem0's own production
reference implementation for code-centric memory. Reading it saved considerable
design work.

| Upstream source | What we took |
|---|---|
| `scripts/_project.py` | The repo-slug algorithm, **matched deliberately** so plugin-written and app-written memory share one namespace |
| `scripts/setup_coding_categories.py` | The coding category vocabulary (`architecture_decisions`, `anti_patterns`, `bug_fixes`, …) |
| `scripts/enforce_metadata_defaults.sh` | The principle that scoping is injected mechanically, never documented-and-hoped |
| `scripts/_search.py` | Layered retrieval, reranking on the injection path, small `top_k` with a threshold floor |

> ⚠️ **The plugin talks to the hosted Platform REST API.** Its *filter shapes* are
> Platform-only and silently break on OSS. We took its structure, not its
> filters. See §6.

### 4.3 To be picked up later — the rollout roadmap

Nothing here is wired up yet. This is what to pull from mem0 next, in order:

| Phase | What to take from mem0 | Why |
|---|---|---|
| **1. Now** | — | Smoke-test the wrapper against live pgvector |
| **2. Devs** | Fork `integrations/mem0-plugin/` — `hooks.json` (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PreCompact`, `Stop`) | Automatic capture and injection in Claude Code / Cursor / Codex. Do **not** rebuild this; it already works |
| **3. Apps** | Nothing new — apps import `firm-mem0` | 2–3 applications onto the shared wrapper |
| **4. GitLab** | Nothing new — same namespace, `source="gitlab"`, `run_id=gitlab-issue-<n>` | Webhook → issue/MR *decisions* (not thread bodies) |
| **5. Firm layer** | — | Curate `Layer.FIRM` conventions once recurring memories are observable |
| **Optional** | `server/docker-compose.yml` | Reference topology for self-hosted Postgres/pgvector + Neo4j |
| **Optional** | `mem0/graphs/` (Neo4j, Memgraph, Kuzu, Apache AGE) | Only if you need cross-service relationship queries. Skip initially |
| **Optional** | `skills/mem0-integrate/`, `skills/mem0-test-integration/` | TDD pipeline skills for wiring mem0 into an existing repo |
| **Optional** | `openmemory/api/` MCP server | If you want a self-hosted MCP endpoint instead of `mcp.mem0.ai` |

### 4.4 Deliberately not used

| Not used | Why |
|---|---|
| `MemoryClient` / `AsyncMemoryClient` (hosted Platform) | You chose self-hosted OSS |
| `app_id` | Platform-only; `agent_id` carries the repo instead |
| `client.project.update()` — `custom_categories` / `custom_instructions` | Platform-only; OSS uses `MemoryConfig.custom_instructions` |
| `mcp.mem0.ai` remote MCP server | Off-network |
| Platform filter shapes (nested `AND`, nested `metadata`) | Silently broken on OSS — §6 |

### 4.5 Why the wrapper lives outside the mem0 checkout

`/Users/ashishtygai/repos/quant/mem0` is a clean checkout of **upstream
`mem0ai/mem0`** (`origin` → `https://github.com/mem0ai/mem0.git`), not a fork.
Firm-internal code there would pollute a repo you will want to keep pulling
from. `firm-mem0` is a sibling directory and depends on `mem0ai` from PyPI like
any other consumer.

---

## 5. Corrections to my initial advice

Two things I said early on, before verifying against the source, were wrong in
ways that affect the design:

1. **"Self-hosted OSS costs you managed reranking."** False. `MemoryConfig.reranker`
   plus `search(rerank=True)` is built into OSS, and the `sentence_transformer`
   cross-encoder runs **locally** — retrieval quality with zero egress.
2. **"OSS loses project-scoped taxonomy."** Partly false.
   `MemoryConfig.custom_instructions` steers fact extraction; it is
   config-scoped rather than project-scoped, which is equivalent once a wrapper
   owns config.

The only genuine OSS loss versus Platform is **`app_id`**.

---

## 6. Two OSS constraints that fail silently

Both were found by testing against mem0's real filter pipeline. Both return
**zero rows with no exception** — the worst possible failure mode, because
retrieval just goes quiet and the agent looks merely unhelpful.

**1. `OR` branches must be flat dicts.**
`Memory._process_metadata_filters` flattens a *top-level* `AND` correctly, but
an `AND` nested inside `OR` is passed through verbatim; pgvector then compiles
the literal key into `payload->>'AND' = ANY(...)`, matching nothing. Keys within
a branch are implicitly ANDed anyway, so flat is both correct and simpler.

```python
# BROKEN on OSS (this is the Platform shape the plugin uses)
{"OR": [{"AND": [{"user_id": "repo:svc"}, {"agent_id": "svc"}]}]}

# CORRECT on OSS
{"OR": [{"user_id": "repo:svc", "agent_id": "svc"}]}
```

**2. Metadata filters are flat top-level keys.**
`_create_memory` flattens caller metadata straight into the payload. The
Platform's nested form raises `Unsupported metadata filter operator: type`.

```python
{"metadata": {"type": "coding_conventions"}}   # BROKEN on OSS — raises
{"type": "coding_conventions"}                 # CORRECT on OSS
```

`tests/test_oss_filter_contract.py` locks both in against the installed mem0,
asserting down to generated SQL that scoping values arrive as bound parameters.

> **Run that file after every mem0 upgrade.** It is the difference between
> finding this in CI and finding it six weeks into a rollout.

---

## 7. Usage

```python
from firm_mem0 import FirmMemory, Layer

memory = FirmMemory.from_env()          # scoping from env + git checkout

# Retrieve before acting — this codebase's conventions + firm standards.
context = memory.recall("how do we handle database migrations here")

# Capture a durable decision. Scoping is injected; the caller cannot forget it.
memory.remember(
    "Rejected the write-through cache: cache-stampede risk under bulk imports.",
    memory_type="architecture_decisions",
    branch="main",
)

# Curated firm convention, stored verbatim rather than LLM-extracted.
memory.remember(
    "This firm uses pnpm exclusively; never npm or yarn.",
    layer=Layer.FIRM,
    infer=False,
)

# Issue-scoped work (GitLab boards).
issue = memory.for_task("gitlab-issue-4821")
issue.remember(
    "Reviewer rejected retry-on-500; use idempotency keys.",
    memory_type="review_feedback",
    source="gitlab",
)
```

`FirmMemory` is immutable — `for_task()` returns a new facade sharing the backend.

### Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `FIRM_MEM0_PG_DSN` | **yes** | — | pgvector connection string |
| `FIRM_MEM0_REPO` | no | git remote | pin the repo slug |
| `FIRM_MEM0_FIRM_OWNER` | no | `firm` | firm layer owner |
| `FIRM_MEM0_COLLECTION` | no | `mem0_firm` | pgvector collection |
| `FIRM_MEM0_RERANK` | no | `on` | `off`/`false` to disable |
| `FIRM_MEM0_TOP_K` | no | `5` | recall breadth |
| `FIRM_MEM0_THRESHOLD` | no | `0.3` | recall floor |

Invalid values raise `ConfigurationError` at **startup**, not mid-request.

### Development

```bash
uv venv --python 3.12
uv pip install -e ".[dev,pgvector]"
.venv/bin/python -m pytest --cov=firm_mem0 --cov-report=term-missing
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

---

## 8. Risks worth tracking

| Risk | Mitigation |
|---|---|
| Forked plugin's slug logic diverges from `repo.py`, and cross-reads go quiet | Keep the two algorithms in sync; they are matched deliberately |
| `embedding_dims` default (1536, for `text-embedding-3-small`) changes later | Changing embedder means a new collection and a re-index. pgvector will not warn you |
| mem0 upgrade changes filter preprocessing | `test_oss_filter_contract.py` in CI |
| Capture rules too loose; pool grows, quality flatlines | **Measure recall hit rate, not memory count.** Catch this on one pilot repo, not across the firm |
| Someone copies Platform filter shapes from the plugin into OSS code | §6, plus the contract tests |

---

## 9. Immediate next steps

1. **Smoke-test against live pgvector** — one `remember()` → `recall()` round trip.
   This is the only untested link in the chain.
2. **Pilot on one repo.** Instrument recall hit rate from day one.
3. **Fork `integrations/mem0-plugin/`** for developer-facing agents once the
   wrapper is proven.
4. `git init` + initial commit for `firm-mem0` (not yet done — no repo
   initialized).
