# Firm Memory — Reference

Companion to `README.md`. This is the working reference for the platform's
contracts, the decisions behind them, and the constraints those decisions were
forced by.

---

## 1. The division of responsibility

| Layer | Owns | Must not know about |
| --- | --- | --- |
| `firm_memory` (platform) | taxonomy, scope, provenance, lifecycle, reliability | vector stores, embeddings, entity axes |
| `firm_memory.providers.*` | storage, retrieval, ranking, filter dialects | approval policy, what a "business rule" is |
| `firm_memory.mcp` | transport | everything of substance |

If a change requires touching more than one of these, it is worth asking whether
it is in the right place.

---

## 2. Canonical model

```python
Memory(
    content, type, scope, provenance,
    confidence, status, tier, task,
    superseded_by, metadata,
    id, created_at, updated_at, score,
)
```

`to_dict()` / `from_dict()` are the migration format. Two axes are easy to
confuse:

- **`status`** — *is this fact endorsed?* `proposed → active → disputed →
  superseded | rejected`.
- **`tier`** — *how long does it live, and how was it written?* `episodic |
  durable | index`.

They are orthogonal. A memory can be `active` + `episodic` (a finding accepted
on this MR) or `proposed` + `durable` (a candidate convention awaiting a human).

### Invariants enforced at construction

| Rule | Why |
| --- | --- |
| Content is non-empty | An empty memory is noise that still costs `top_k` |
| Type is in the taxonomy | An untaggable fact is unretrievable |
| Scope names at least one of firm/domains/repos | An unscoped memory is unreachable |
| `confidence ∈ [0, 1]` | It gates automation; an out-of-range value silently changes policy |
| Only `episodic` may carry a task | Durable memory bound to a `run_id` is stranded on that task |
| `episodic` must carry a task | It is per-task working memory by definition |

---

## 3. Taxonomy

Thirteen types. Nine are carried over from the original coding taxonomy with
their **wire values unchanged**, so memory already written by the editor plugin
stays queryable; four were added by the platform design.

| Member | Wire value | Origin |
| --- | --- | --- |
| `ARCHITECTURE_DECISION` | `architecture_decisions` | carried over |
| `REJECTED_APPROACH` | `anti_patterns` | carried over |
| `CONVENTION` | `coding_conventions` | carried over |
| `REVIEW_PATTERN` | `review_feedback` | carried over |
| `BUG_FIX` | `bug_fixes` | carried over |
| `TASK_LEARNING` | `task_learnings` | carried over |
| `TOOLING_SETUP` | `tooling_setup` | carried over |
| `DEPENDENCY_DECISION` | `dependency_decisions` | carried over |
| `PERFORMANCE_FINDING` | `performance_findings` | carried over |
| `BUSINESS_RULE` | `business_rules` | added |
| `PRODUCTION_ISSUE` | `production_issues` | added |
| `OWNERSHIP` | `ownership` | added |
| `TERMINOLOGY` | `terminology` | added |

`coerce_type()` accepts either spelling. `fact_extraction_instructions()`
renders the descriptions plus the exclusions into the provider's extraction
prompt.

**Renaming a wire value orphans every memory already stored under it.** Add
members; do not re-slug existing ones.

---

## 4. Scope

```python
MemoryScope(firm=True, domains=("execution",), repos=("oms", "gateway"))
```

Independent attributes, normalised and de-duplicated at construction. The unit
of matching is the **atom**:

```text
("firm", "domain:execution", "repo:oms", "repo:gateway")
```

A memory is a candidate for a query when the two scopes share at least one atom.
This is what lets a fact spanning several repos be stored once and found from
each of them.

`MemoryScope.for_query(repo, domains=...)` always sets `firm=True`. Omitting
firm knowledge is the commonest way a recall silently loses the answer.

**No engineer and no team axis.** The firm has no team-to-use-case segregation,
and the bot is stateless and serves everyone identically. Either axis would
split one fact into copies that drift apart and that no single query can reach.
The accepted cost: individual reviewer preferences are out of scope until
curated as a firm convention. A test asserts the dataclass has exactly the three
fields.

---

## 5. Lifecycle

```text
Source ─► Candidate ─► checks ─► human approval ─► Firm Memory ─► provider.insert()
```

Candidates wait in a `CandidateStore`, **outside** the provider — an unreviewed
proposal must never be one status-filter bug away from being recalled. For a
stateless bot the store must be file-backed: the agent that proposes and the
engineer who approves are different processes, hours apart.

Candidate ids are **content-addressed** (content + scope + type), so a bot
re-running the same MR updates one queue entry instead of burying the reviewer.

Always human-approved, whatever the confidence:

- `BUSINESS_RULE`, `ARCHITECTURE_DECISION`, `CONVENTION`, `PRODUCTION_ISSUE`
- anything with firm-wide scope

**Nothing deletes.** `correct()` demotes and flags (`confidence − 0.3`);
`supersede()` names the replacement. Retention is supersession plus reduced
confidence, decided deliberately — TTLs and a sweeper are out of V1.

---

## 6. Provider interface

```python
class MemoryProvider(Protocol):
    name: str
    def insert(self, memory: Memory) -> Memory: ...
    def search(self, query, scope=None, limit=10, *, types, tiers, statuses, task) -> list[Memory]: ...
    def get(self, memory_id: str) -> Memory | None: ...
    def update(self, memory: Memory) -> Memory: ...
```

`query`, `scope` and `limit` are the substance. The keyword filters are the
firm's guarantees and every implementation must honour them — especially
`tiers`, which defaults to excluding episodic memory.

The design sketch listed only `insert` and `search`. `get` and `update` are here
because `memory_get` and `memory_correct` are in the V1 MCP surface and cannot
be served without them. That is the whole interface.

**Deliberately not abstracted in V1:** reranker, query normalisation, retrieval
engine, result validation, multi-provider federation. Extra layers get added
when an evaluation demonstrates a need.

Export/import is a **migration** capability (`MigratableProvider`), not part of
the runtime interface — requiring it would raise the bar for adding a provider.
`import_all` stores records verbatim, timestamps included; a migration that
restamped `updated_at` would rewrite the pool's history.

Selection is configuration-driven, with built-ins imported lazily:

```python
provider = get_provider(settings)      # FIRM_MEMORY_PROVIDER=mem0
register_provider("tencentdb", lambda settings: TencentProvider(...))
```

---

## 7. The mem0 projection

Everything mem0-shaped lives under `providers/mem0/`. Nothing above it imports
mem0.

### Entity axes

| Axis | Meaning here |
| --- | --- |
| `user_id` | the **pool owner** — one constant for the whole firm |
| `agent_id` | the repo slug (repo-primary memories only) |
| `run_id` | the task (episodic tier only) |

**Why `user_id` is not the partition.** It is the obvious place for `repo:oms`,
and an earlier design put it there. But `Memory.search` requires at least one of
`user_id`/`agent_id`/`run_id` **at the top level** of the filter dict, and a
top-level key is ANDed with everything below it. Spending `user_id` on the
partition therefore makes a cross-partition `OR` inexpressible: either the
search is pinned to one partition, or it carries no top-level entity key and
mem0 rejects it outright.

> This was a live bug in the pre-refactor package: `recall()` across layers
> emitted `{"OR": [...]}` with no entity key and would have been rejected by
> mem0 before reaching the store. The unit tests used a fake backend, and the
> contract test exercised only the preprocessor — so nothing caught it.
> `tests/contract/test_mem0_oss_filters.py::test_mem0_requires_a_top_level_entity_key`
> now pins it.

### Layers

A layer is which kind of atom a partition was cut from — `FIRM`, `DOMAIN`,
`REPO` — ordered broadest first. A memory spanning several atoms is filed under
the **broadest** one it has, so firm-wide knowledge never ends up recorded under
a single repo. Ownership is kept on the payload as `owner` / `layer` for
administration.

**The one-write rule:** a memory scoped to three repos is stored once, with every
atom recorded as a flat metadata key. One copy per atom would recreate exactly
the drift that rules out per-engineer scoping.

### Filters

Scope is matched on flat `scope_*` payload keys, not on entity axes, because a
memory covering `oms` and `gateway` is filed under one of them and an entity
filter would miss it from the other.

```python
{
  "user_id": "firm",                                # the top-level key mem0 requires
  "OR": [
    {"scope_firm": "1",              "tier": ["durable", "index"], "status": ["active", "disputed"]},
    {"scope_domain_execution": "1",  "tier": ["durable", "index"], "status": ["active", "disputed"]},
    {"scope_repo_oms": "1",          "tier": ["durable", "index"], "status": ["active", "disputed"]},
  ],
}
```

Four OSS constraints, all verified against `Memory._process_metadata_filters`
and `pgvector._build_filter_conditions`, all pinned by contract tests:

1. **`OR` branches must be flat.** An `AND` nested inside `OR` is passed through
   verbatim and compiles to `payload->>'AND' = ANY(...)`, matching nothing.
   Shared conditions are therefore repeated into every branch, not hoisted.
2. **Metadata filters are flat top-level keys.** The Platform's nested
   `{"metadata": {...}}` form raises `Unsupported metadata filter operator`.
3. **A list value means "one of"** — it compiles to `payload->>key = ANY(...)`.
   This is what makes multi-tier and multi-status filtering expressible inside
   a flat branch.
4. **A top-level entity key is mandatory** — see above.

Caller metadata is stripped of `scope_*` keys and re-derived from the memory's
own scope. Without that, a caller could set `scope_firm` on a repo memory and
have it answer firm-wide queries.

### Reading

`to_memory()` tolerates both response shapes (top-level keys and a nested
`metadata` dict) and drops a malformed row rather than failing the whole recall.
Memory written before the platform — by the editor plugin, or by the earlier
client — is still readable: scope falls back to `owner`, then `user_id`, then
`agent_id`.

Writes use `infer=False`. A canonical memory is already a distilled fact that
passed the taxonomy and the gate; re-extracting it would summarise a summary and
lose the provenance just attached. `infer=True` is opt-in for the one case that
needs it — reconciling a distilled fact against a near-duplicate already in the
pool.

---

## 8. MCP surface

| Tool | Contract |
| --- | --- |
| `memory_search` | Active memories relevant to a query, scoped |
| `memory_get` | One memory by id — including superseded and disputed, so a citation always resolves |
| `memory_propose` | Queues a candidate. **Does not create an active memory** |
| `memory_correct` | Reports a memory as stale or wrong. Requires a reason |

Plus a `firm-memory://taxonomy` resource, so an agent is told the vocabulary
rather than guessing — a guessed type is rejected, and a rejected proposal is
knowledge lost.

**No tool raises.** A tool that throws takes the agent's whole turn with it.
Failures come back as `{"error": ..., "error_type": ...}`.

Tool descriptions are part of the contract: they are the only place an agent
learns that the codebase outranks memory, and that proposing is not writing.

The logic lives in `mcp/tools.py`, which has no SDK dependency — so the
agent-facing contract is directly testable and a non-MCP consumer can call the
same functions. `mcp/server.py` is a thin `FastMCP` adapter.

---

## 9. Reliability

| Operation | On provider failure |
| --- | --- |
| `search`, `get` | Empty result, metric recorded, warning logged |
| `propose`, `commit`, `correct` | Raises `ProviderError` |

Reads run under a bounded timeout (`FIRM_MEMORY_TIMEOUT_SECONDS`, default 2.0s)
on a worker thread. A timed-out call is **abandoned, not cancelled** — a thread
cannot be interrupted mid-query — so the provider may still be working when the
call returns. The cost is a wasted query; the alternative is holding an agent
behind a degraded database.

`Metrics.snapshot()` gives per-operation calls, failures, timeouts and mean
latency. It is in-process and dependency-free on purpose: introducing a metrics
backend would make memory a hard dependency of the agent, which the reliability
design forbids.

---

## 10. Migration

The canonical representation is what makes migration possible:

```text
current provider ─► export_all() ─► validation ─► import_all() ─► new provider
```

Shadow mode first — run both, compare retrieval relevance, latency,
useful-memory rate, citation acceptance and token impact — then flip
`FIRM_MEMORY_PROVIDER`. No OpenCode or MCP change.

`tests/integration/test_provider_swap.py` exercises this seam directly: a
provider the platform has never heard of, registered at runtime, serving the
same API calls with the same canonical results.

---

## 11. Testing

| Suite | What it proves |
| --- | --- |
| `tests/unit` | Each module's contract in isolation |
| `tests/integration` | The API across layers: scope, tiers, approval, reliability, provider swap |
| `tests/contract` | Our filters survive mem0's real pipeline into SQL |
| `tests/mcp` | The agent-facing surface, including that no tool throws |

Most tests run against `InMemoryProvider`, so what is under test is the firm's
semantics rather than mem0's behaviour. Its scoring is lexical term overlap, not
semantics — which is why the shared fixture sets `min_score=0.0` and the
relevance floor is tested directly instead.

The contract suite is the one to watch on a mem0 upgrade.

---

## 12. Known gaps

- **No consolidation pass.** Near-duplicates crowding `top_k` is the real risk
  as the pool grows, not volume. A monthly merge that keeps originals queryable
  is designed but not built.
- **No document ingest.** Nothing yet derives memories from ADRs, CHANGELOG,
  READMEs or reverted commits. `Provenance.doc_sha` and `evidence` exist for it.
- **No `superseded_by` write path.** `lifecycle.supersede()` exists; nothing
  calls it, because nothing yet detects that a new memory replaces an old one.
- **Approval is API-only.** There is no review UI; `memory.approvals.pending()`
  is the whole interface.
- **`component` join key is not implemented.** Filtering memory by the
  components CodeGraph resolves from changed files is what makes the two systems
  compose rather than merely coexist.
