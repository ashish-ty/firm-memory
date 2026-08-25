# firm-memory

**A memory layer that lets our AI coding agents remember how this firm builds
software, so they stop relearning the same things on every call.**

> CodeGraph answers *"what is the code doing?"*
> Firm Memory answers *"why do we build it this way?"*

CodeGraph stays authoritative for current code behaviour. Memory holds
contextual engineering knowledge — and may go stale, which is why **when memory
and the current code disagree, the code wins.**

---

## The shape of it

```text
  OpenCode        On-call        Future agent
      └───────────────┼───────────────┘
                      │  MCP
             ┌────────▼─────────┐
             │  Firm Memory MCP │   thin transport adapter
             └────────┬─────────┘
                      │
             ┌────────▼─────────┐
             │   Firm Memory    │   taxonomy · scope · provenance · lifecycle
             └────────┬─────────┘
                      │  MemoryProvider
             ┌────────▼─────────┐
             │      mem0        │   embeddings · vector search · ranking
             └──────────────────┘
```

The platform owns **what a firm memory means**. The provider owns **how it is
stored and retrieved**. MCP owns **how agents access it**. That separation is
the whole point: a second provider can be introduced without changing OpenCode
or the MCP contract.

---

## Quick start

```bash
pip install -e '.[mem0,pgvector,rerank,mcp,dev]'
export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@db.internal:5432/mem0'
export FIRM_MEMORY_DOMAINS='execution,mcx'      # this repo's domains
export FIRM_MEMORY_CANDIDATES_PATH='.firm-memory/candidates.json'
```

```python
from firm_memory import FirmMemory, MemoryScope, MemoryType

memory = FirmMemory.from_env()          # scoped to this checkout + its domains + the firm

for hit in memory.search("why does OMS reject orders after 15:20"):
    print(hit.id, hit.content, hit.provenance.reference)

proposal = memory.propose(
    "Cash strategies stop sending at 15:20 because the exchange rejects after that.",
    type=MemoryType.BUSINESS_RULE,
    scope=MemoryScope(domains=("execution",), repos=("oms", "gateway")),
    reference="mr-4821",
)
# Not stored as knowledge yet — it is queued for a human:
print(proposal.accepted, proposal.candidate_id, proposal.decision.reason)

memory.approvals.approve(proposal.candidate_id, approver="ashish")
```

Run the MCP server for agents:

```bash
firm-memory-mcp        # stdio; exposes memory_search / memory_get / memory_propose / memory_correct
```

---

## The five things this package owns

### 1. Taxonomy

A provider's stock extraction is tuned for consumer assistants (food, hobbies,
music). Ours is tuned for trading systems. Thirteen types, each with the
description that drives extraction:

`ARCHITECTURE_DECISION` · `REJECTED_APPROACH` · `CONVENTION` · `REVIEW_PATTERN` ·
`BUG_FIX` · `TASK_LEARNING` · `TOOLING_SETUP` · `DEPENDENCY_DECISION` ·
`PERFORMANCE_FINDING` · `BUSINESS_RULE` · `PRODUCTION_ISSUE` · `OWNERSHIP` ·
`TERMINOLOGY`

Enforced before anything reaches a provider. Both spellings resolve — the member
name (`BUSINESS_RULE`) and the stable wire slug (`business_rules`).

Just as important are the **exclusions**: no source code, diffs or stack traces;
no secrets; no facts about individual engineers; no transient state.

### 2. Scope

Independent attributes, not a hierarchy — because firm knowledge does not
respect a tree:

```json
{"firm": true, "domains": ["execution"], "repos": ["oms", "gateway"]}
```

A memory spanning three repos is **stored once** and reachable from each of
them. There is deliberately **no engineer-level and no team-level scope**: the
same question must return the same firm knowledge whoever asks, and an identity
axis would split one fact into copies that drift apart.

### 3. Tiers

The lifetime axis, orthogonal to approval status:

| Tier | What it holds | Task-scoped? |
| --- | --- | --- |
| `EPISODIC` | Per-MR working memory — findings and their dispositions | Yes, required |
| `DURABLE` | Distilled knowledge, written through the approval gate | Never |
| `INDEX` | One verbatim card per closed issue/MR, kept document-shaped | Never |

Search excludes `EPISODIC` by default. That default is load bearing: to a vector
store an absent task filter means *"don't care"*, not *"unset"*, so without it
every MR's scratch state joins ordinary recall. It has a contract test.

### 4. Provenance and lifecycle

Every memory carries where it came from, so an engineer can trace a citation
back to the MR, issue or interview behind it — and correct it.

```text
Candidate ─► taxonomy / scope / provenance checks ─► human approval ─► provider.insert()
```

V1 is **fully human approved**; confidence is recorded from the start so
automation can be switched on later without a migration. Business rules,
architecture decisions, firm conventions and production-critical knowledge
always need a person, whatever the confidence.

**Nothing deletes.** A correction demotes and flags; supersession names the
replacement. The record that a decision was made — and unmade — survives.

### 5. Reliability

Memory is **best effort**. Reads never raise: a provider outage or a breach of
the bounded timeout yields an empty result and a recorded metric, so a failed
recall cannot fail a code review. Writes *do* raise — silently dropping a memory
an engineer just approved would be worse than an error.

---

## Configuration

Platform settings are provider-independent; provider settings are read by the
provider itself. That split is what keeps a provider swap a config change.

| Variable | Default | Meaning |
| --- | --- | --- |
| `FIRM_MEMORY_PROVIDER` | `mem0` | Which provider to use |
| `FIRM_MEMORY_LIMIT` | `5` | Results per search |
| `FIRM_MEMORY_MIN_SCORE` | `0.3` | Relevance floor |
| `FIRM_MEMORY_TIMEOUT_SECONDS` | `2.0` | Bounded wait before giving up |
| `FIRM_MEMORY_DOMAINS` | — | Domains this checkout belongs to |
| `FIRM_MEMORY_REPO` | *(git remote)* | Override the repo slug |
| `FIRM_MEMORY_CANDIDATES_PATH` | *(in-process)* | Where proposals wait for a human |
| `FIRM_MEMORY_AUTO_APPROVE` | `off` | Confidence-based automation |
| `FIRM_MEM0_PG_DSN` | **required** | pgvector connection string |
| `FIRM_MEM0_COLLECTION` | `mem0_firm` | Collection name |
| `FIRM_MEM0_RERANK` | `on` | Local cross-encoder reranking |

`FIRM_MEM0_REPO`, `FIRM_MEM0_TOP_K` and `FIRM_MEM0_THRESHOLD` are still honoured
so an existing deployment does not change behaviour on upgrade.

The deployment is self-hosted with no egress. Business rules like *"MCX orders
always route through Risk Engine A"* are closer to strategy IP than to code
comments, and the pool inherits the union of access control across every repo
feeding it.

---

## Layout

```text
src/firm_memory/
├── models.py          canonical Memory · status · tier
├── taxonomy.py        the firm's vocabulary and its exclusions
├── scope.py           firm / domains / repos
├── provenance.py      where a memory came from
├── lifecycle.py       approval policy and status transitions
├── memory.py          the API agents and applications import
├── config.py          platform settings
├── metrics.py         failure and latency counters
├── repo.py            deterministic repo identity
├── providers/
│   ├── base.py        the interface: insert · search · get · update
│   ├── registry.py    configuration-driven selection
│   ├── inmemory.py    dependency-free provider for tests and local use
│   └── mem0/          namespace · filters · mapping · settings · provider
├── ingestion/
│   ├── approval.py    the human gate
│   └── store.py       where candidates wait
└── mcp/
    ├── tools.py       the four tools (no SDK dependency)
    └── server.py      thin transport adapter

tests/
├── unit/          modules in isolation
├── integration/   the API across layers, incl. provider swap
├── contract/      against the real mem0 filter pipeline
└── mcp/           the agent-facing surface
```

---

## Development

```bash
.venv/bin/python -m pytest -q                      # 261 tests
.venv/bin/python -m pytest --cov --cov-report=term  # 94% coverage
.venv/bin/python -m ruff check src tests
```

The **contract tests** are the ones to watch. They run our filters through
mem0's real preprocessing and pgvector's SQL builder, pinning constraints found
by reading its source — flat `OR` branches, flat metadata keys, list values
meaning *one of*, and the top-level entity key `Memory.search` requires. If a
mem0 upgrade breaks one, they fail loudly instead of the pool quietly going
empty.

---

## Planning documents

`PLAN_OPENCODE_MEMORY.md`, `PLAN_OBSERVABILITY_EVAL.md`, `BRIEF_1_ARCHITECTURE.md`
and `BRIEF_2_DELIVERY_PLAN.md` predate this refactor. Their strategy still holds;
their code examples describe the earlier `firm_mem0` API and no longer match the
package. `docs.md` is the current reference.
