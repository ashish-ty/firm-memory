# firm-mem0

The firm's memory namespace contract over **self-hosted mem0 OSS**.

Every AI application imports `FirmMemory` from here instead of constructing
`mem0.Memory` directly. That single choke point is what lets the taxonomy,
retrieval thresholds, vector store, and scoping rules change once rather than in
each application.

## Why a wrapper at all

mem0's defaults are user-centric; code-heavy workloads are repo-centric. Left to
itself, each application invents its own keys and the memory pool becomes
unqueryable within a quarter. This package fixes the contract and enforces it in
code — an unscoped write is not expressible through the facade.

## The namespace contract

mem0 OSS gives three entity axes plus free-form metadata. There is no `app_id`
(Platform-only), so the axes are spent like this:

| mem0 axis | Meaning | Example |
|---|---|---|
| `user_id` | the memory **owner**, prefixed per layer | `repo:acme-billing-svc`, `firm` |
| `agent_id` | the **repo slug**, derived from the git remote | `acme-billing-svc` |
| `run_id` | the **task** | `gitlab-issue-4821` |
| metadata | filterable axes | `type`, `layer`, `repo`, `branch`, `source` |

**An owner is never a person or a team.** There is no per-engineer and no
per-team pool: the bot is stateless and serves every engineer identically, and
no team owns a set of use cases, so knowledge belongs to the codebase and to the
firm. Per-person or per-team pools would fragment the same fact into copies no
single query can reach.

### Two layers

| Layer | Owner | Scope | Written by |
|---|---|---|---|
| `Layer.REPO` | `repo:<slug>` | repo, optionally task | agents (the bulk) |
| `Layer.FIRM` | `firm` | cross-repo conventions | leads, curated |

Only `REPO` is task-scoped. `FIRM` holds facts that must stay retrievable on the
*next* task, so binding it to a `run_id` would strand them.

`recall()` unions both layers into **one** `search()` call.

## Repo identity is derived, never configured

`resolve_repo_slug()` reads `git config --get remote.origin.url` and reduces it to
`owner-repo`, dropping the host so SSH, HTTPS, and host-aliased clones agree.
The algorithm deliberately matches
`integrations/mem0-plugin/scripts/_project.py` in the mem0 repo, so memory
written by the editor plugin and by application code lands in the same
namespace. Order: `FIRM_MEM0_REPO` override → git remote → directory basename.

## Usage

```python
from firm_mem0 import FirmMemory, Layer

memory = FirmMemory.from_env()          # scoping resolved from env + git checkout

# Retrieve before acting — this codebase's conventions plus firm standards.
context = memory.recall("how do we handle database migrations here")

# Capture a durable decision. Scoping is injected; the caller cannot forget it.
memory.remember(
    "Rejected the write-through cache: cache-stampede risk under bulk imports.",
    memory_type="architecture_decisions",
    branch="main",
)

# Curated firm-wide convention, stored verbatim rather than LLM-extracted.
memory.remember("This firm uses pnpm exclusively; never npm or yarn.",
                layer=Layer.FIRM, infer=False)

# Issue-scoped work (GitLab boards).
issue = memory.for_task("gitlab-issue-4821")
issue.remember("Reviewer rejected the retry-on-500 approach; use idempotency keys.",
               memory_type="review_feedback", source="gitlab")
```

`FirmMemory` is immutable: `for_task()` returns a new facade sharing the backend.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `FIRM_MEM0_PG_DSN` | yes | — | pgvector connection string |
| `FIRM_MEM0_REPO` | no | git remote | pin the repo slug |
| `FIRM_MEM0_FIRM_OWNER` | no | `firm` | firm layer owner |
| `FIRM_MEM0_COLLECTION` | no | `mem0_firm` | pgvector collection |
| `FIRM_MEM0_RERANK` | no | `on` | `off`/`false` to disable |
| `FIRM_MEM0_TOP_K` | no | `5` | recall breadth |
| `FIRM_MEM0_THRESHOLD` | no | `0.3` | recall floor |

All invalid values raise `ConfigurationError` at startup, not mid-request.

## Two OSS constraints this package works around

Both were found by testing against mem0's real filter pipeline, and both fail
*silently* (zero rows, no exception) if you get them wrong:

1. **`OR` branches must be flat dicts.** Top-level `AND` is flattened by
   `Memory._process_metadata_filters`, but an `AND` nested inside `OR` passes
   through verbatim and pgvector compiles it to `payload->>'AND' = ANY(...)`,
   which matches nothing. Keys within a branch are implicitly ANDed anyway.
2. **Metadata filters are flat top-level keys.** `_create_memory` flattens
   metadata into the payload, so the Platform's nested
   `{"metadata": {"type": ...}}` form raises
   `Unsupported metadata filter operator: type` on OSS.

`tests/test_oss_filter_contract.py` locks both in against the installed mem0,
down to the generated SQL. Run it after any mem0 upgrade.

## Taxonomy

mem0's stock fact extraction is tuned for consumer assistants (food, hobbies,
music). OSS has no project-scoped `custom_categories`, so the coding taxonomy is
expressed through `MemoryConfig.custom_instructions` instead — see
`taxonomy.py`. Category names match the mem0 plugin's so both writers share one
vocabulary.

The instructions also carry explicit exclusions. **Memory is not RAG over your
code**: no file contents, diffs, stack traces, secrets, or transient state —
only the conclusions drawn from them. Without these, transcripts flood the pool
and drown the signal.

## Development

```bash
uv venv --python 3.12
uv pip install -e ".[dev,pgvector]"
.venv/bin/python -m pytest --cov=firm_mem0 --cov-report=term-missing
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

92 tests, 97% coverage. The contract tests exercise the installed mem0 and are
the ones that matter on upgrade.

## Operating notes

- Measure **recall hit rate**, not memory count. A pool that grows while
  retrieval quality flatlines means capture rules are too loose.
- Reranking is a local cross-encoder (`sentence_transformer`), so it adds
  retrieval quality without any egress. ~150–200ms per call.
- `infer=False` for curated facts, `infer=True` for conversational capture.
