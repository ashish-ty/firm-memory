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
  OpenCode        On-call        Future agent                 An engineer
      └───────────────┼───────────────┘                            │
                      │  MCP                                       │  browser
             ┌────────▼─────────┐                        ┌─────────▼────────┐
             │  Firm Memory MCP │  transport adapter     │   Review UI      │
             └────────┬─────────┘                        └─────────┬────────┘
                      │                                            │
                      │   search · get                             │
                      │   ingest · propose ──► candidate queue ──►  │  approve
                      │   correct                (shared)          │  amend
                      │                                            │  reject
             ┌────────▼────────────────────────────────────────────▼────────┐
             │   Firm Memory      taxonomy · scope · provenance · lifecycle  │
             └────────────────────────────┬─────────────────────────────────┘
                                          │  MemoryProvider
                              ┌───────────▼────────────┐
                              │         mem0           │  embeddings · search
                              └────────────────────────┘
```

Agents propose; they never write. Everything they put forward waits in the
candidate queue until a person opens the review UI and endorses it — that gate
is the only path into the retrievable pool.

The platform owns **what a firm memory means**. The provider owns **how it is
stored and retrieved**. MCP owns **how agents access it**, and the review UI
owns **how a human decides**. That separation is the whole point: a second
provider can be introduced without changing OpenCode or the MCP contract.

---

## Quick start

```bash
uv sync                       # installs everything, including the dev group
cp .env.example .env          # then fill in the two credentials
```

```ini
# .env  (gitignored)
OPENROUTER_API_KEY=sk-or-...
FIRM_MEM0_PG_DSN=postgresql://mem0:pw@localhost:5432/mem0
```

A database with pgvector, if you do not already have one:

```bash
docker compose -f examples/docker-compose.yml up -d
```

mem0 creates the `vector` extension and its table on first use, so there is no
schema step.

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
firm-memory-mcp        # stdio; five tools, listed under "The agent surface" below
```

Run the review UI for the humans who approve what those agents propose:

```bash
export FIRM_MEMORY_REVIEW_TOKEN=$(openssl rand -hex 32)
firm-memory-review     # http://127.0.0.1:8765
```

### Ingesting raw material

Nothing is stored directly. Raw material is distilled into candidates, and a
candidate becomes firm knowledge only when a person approves it:

```python
from firm_memory import SourceDocument, MemoryScope, Provenance

candidates = memory.ingest(SourceDocument(
    content=mr_discussion_text,
    scope=MemoryScope(domains=("execution",), repos=("oms",)),
    provenance=Provenance(source="merge-request", reference="mr-4821"),
    kind="merge request discussion",
))

for candidate in memory.approvals.pending():
    print(candidate.id, candidate.memory.type, candidate.memory.content)

memory.approvals.approve(candidates[0].id, approver="ashish")   # only now is it retrievable
```

`ingest()` never writes to the provider. Extracting nothing is a normal and
frequent outcome — most discussions contain no durable knowledge.

**Two extractors**, chosen with `FIRM_MEMORY_EXTRACTOR`:

| Value | What runs |
| --- | --- |
| `provider` *(default)* | mem0's own extractor, including its deduplication against memories already in the pool — but only its read-only phases, so it never writes. A second batched call types each fact against the firm taxonomy. |
| `llm` | The platform's own prompt, written for engineering memory from the start and returning type and confidence directly. One call. |
| `none` | Ingestion disabled. Search and hand-curation still work. |

mem0's extraction prompt is written for a consumer assistant — its examples are
"User has a dog named Max". The firm's instructions steer it, but anything that
still comes back in that shape is caught by the taxonomy and dropped. Run both
against real MRs before committing to either.

### Try it now

Point it at one PR review comment and see what it proposes:

```bash
uv sync
cp .env.example .env          # then fill in the two credentials

python examples/review_comment.py
```

See [`examples/`](examples/) for details.

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

## The agent surface

Five MCP tools. None of them writes to the pool.

| Tool | What it does |
| --- | --- |
| `memory_search` | Find knowledge relevant to the task in hand. |
| `memory_get` | Fetch one memory by id, to cite it or verify a claim. |
| `memory_ingest` | Hand over **raw material** — an MR discussion, an incident write-up, an interview — to be distilled into candidates. |
| `memory_propose` | Put forward **one already-distilled fact**. |
| `memory_correct` | Report a memory as stale or wrong. It is demoted and flagged, never deleted. |

`memory_ingest` and `memory_propose` are the same gate reached from two
distances. An agent that has *read* a discussion should hand over the discussion
and let the platform's extractor and taxonomy do the judging; an agent that has
already concluded something specific proposes that. Neither stores anything: the
raw text is never kept, only the facts extracted from it, and each of those waits
for a person.

Extracting nothing is a normal outcome, and the tool description says so — an
agent that reads `count: 0` as a failure will retry and just burn tokens.

---

## Reviewing what agents propose

The gate is only real if someone can see the queue. `firm-memory-review` serves
one page that lists every candidate with its type, scope, confidence and the
provenance needed to check it, and lets a reviewer **approve, reject, or correct
it first**.

```bash
export FIRM_MEMORY_REVIEW_TOKEN=$(openssl rand -hex 32)
export FIRM_MEMORY_REVIEW_APPROVER=ashish
export FIRM_MEMORY_CANDIDATES_URL=postgresql://mem0:pw@localhost:5432/mem0
firm-memory-review
```

Keep the token constant — in `.env` rather than generated per run — so it is
pasted once. For a team, issue one each and skip the two variables above:

```bash
export FIRM_MEMORY_REVIEW_TOKENS="ashish:$(openssl rand -hex 32),priya:$(openssl rand -hex 32)"
```

For the whole loop on a fresh machine — agent ingests over MCP, human approves
here — follow [`examples/MCP_ROUND_TRIP.md`](examples/MCP_ROUND_TRIP.md).

An extracted fact is frequently 90% right — the rule is real but the wording is
the model's, the type is one category off, or the scope is narrower than the fact
actually is. So a reviewer may amend **content, type, scope and confidence** on
the way through, and the amended memory records who changed it and which fields
moved. `tier` and `task` are deliberately not amendable: they describe how a
memory was written, not what a reviewer thinks of it.

Four behaviours are worth knowing about, because each one is a place where a
comfortable lie would leave firm knowledge in a state nobody intended:

- **An unreachable queue is not an empty queue.** It returns 503 and says so.
  Showing "nothing to review" for a queue that is actually full is this tool's
  worst possible failure.
- **Approval claims the candidate atomically** (`DELETE ... RETURNING`). Two
  reviewers on one candidate is a race exactly one wins; the other gets a 409
  telling them to reload.
- **Any failure after that claim puts the candidate back** — a provider outage
  and a mistyped amendment alike. A reviewer's edit must not be able to destroy
  the candidate it was meant to improve.
- **Every decision is attributed, by the token.** There is no name field. A
  token is issued to a person, so presenting it both admits you and says who is
  approving — a name in the request body is refused outright. That is stronger
  than a typed name, which is a claim rather than a fact, and it is attribution
  rather than authorisation: every reviewer can do the same things, and a memory
  is owned by the repo and the firm, never by a person.

The page loads nothing from the internet — no CDN, no web font, no analytics.
Candidates are unreviewed statements about the firm's trading systems, and a
review tool that reached out to third parties while displaying them would undo
the self-hosted, no-egress deployment. There is a test on it.

The token is held in the tab's `sessionStorage` and never in a URL. The service
refuses to start without one, refuses to start a token with no reviewer name
attached, refuses two reviewers sharing a token — that would make an approval
untraceable to either — binds to `127.0.0.1` unless told otherwise, and warns
when it is bound anywhere wider.

### Where the queue lives

A candidate is proposed by a bot in a CI job that ends minutes later, and
approved by an engineer somewhere else hours after that. A queue that cannot
span those two processes is a queue nothing ever gets reviewed from — so the
location is configured as a URL whose scheme picks the store:

| `FIRM_MEMORY_CANDIDATES_URL` | Store | Reaches |
| --- | --- | --- |
| `postgresql://…` | Postgres table, separate from the memory pool | Any machine |
| `file:///srv/queue.json` | One JSON file, written atomically | One host |
| `memory://` *(default)* | A dict | One process |

Unset with no `FIRM_MEMORY_CANDIDATES_PATH` means in-process, which is right for
tests and wrong for a deployment. An unrecognised scheme is refused rather than
quietly downgraded: a misconfigured queue that silently became a dict would
accept everything the bot proposed and lose all of it, with no error anywhere.

The table is created on first use, so there is no migration step — and it is
deliberately **not** part of the memory pool. An unreviewed candidate is not firm
knowledge, and putting it in the retrievable pool would leave every recall one
status-filter bug away from returning things nobody approved.

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
│                      · extraction (mem0's extractor, read-only)
│                      · embedders (fastembed correction)
├── ingestion/
│   ├── extraction.py  raw material -> candidates (the only way in)
│   ├── llm.py         the completion client (LiteLLM by default)
│   ├── approval.py    the human gate
│   ├── amendment.py   what a reviewer may correct on the way through
│   ├── store.py       where candidates wait (dict, file)
│   ├── postgres_store.py  ...and across machines
│   └── selection.py   which of those a queue URL names
├── mcp/
│   ├── tools.py       the five tools (no SDK dependency)
│   └── server.py      thin transport adapter
└── review/
    ├── service.py     the review operations, framework free
    ├── app.py         the HTTP surface and its status codes
    ├── settings.py    token, bind address, rate limit
    ├── auth.py        access (a token) vs attribution (a typed name)
    ├── throttle.py    a per-client bound
    └── static/        one self-contained page; no CDN, no web font

examples/
├── review_comment.py   one review comment, end to end
├── PIPELINE.md         how it works, with diagrams
└── docker-compose.yml  Postgres with pgvector

tests/
├── unit/          modules in isolation
├── integration/   the API across layers, incl. provider swap
├── contract/      against the real mem0 filter pipeline
└── mcp/           the agent-facing surface
```

---

## Development

```bash
uv run pytest -q                          # 438 tests (1 skipped without the mcp extra)
uv run pytest --cov --cov-report=term     # 94% coverage
uv run ruff check src tests examples
```

CI runs the same three on Python 3.11 and 3.12.

The Postgres candidate queue is held to the same suite as the other two stores,
but only when a database is reachable:

```bash
docker compose -f examples/docker-compose.yml up -d
FIRM_MEMORY_TEST_PG_DSN=postgresql://mem0:pw@localhost:5432/mem0 uv run pytest -q
```

Without it those cases skip rather than fail, so a clone still runs green.

The **contract tests** are the ones to watch. They run our filters through
mem0's real preprocessing and pgvector's SQL builder, pinning constraints found
by reading its source — flat `OR` branches, flat metadata keys, list values
meaning *one of*, and the top-level entity key `Memory.search` requires. If a
mem0 upgrade breaks one, they fail loudly instead of the pool quietly going
empty.
