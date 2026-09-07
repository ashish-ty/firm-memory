# Firm Memory — Team Overview

**A memory layer that lets our AI coding agents remember how this firm builds
software, so they stop relearning the same things on every call.**

---

## 1. The problem

Our OpenCode GitLab bot runs **stateless**. Every MR review, every plan, every
generated PR starts from zero. Its only context is the checkout — `git diff`,
`grep`, and the repo's `.open-code/` folder.

That means the bot can read *what* the code does, but it can never see:

- why a design is the way it is, and what was already tried and rejected
- that MCX orders must route through Risk Engine A
- that cash strategies stop sending at 15:20 because the exchange rejects after
  that — the code shows the time check, nothing records the reason
- that a module has caused three prior regressions
- what CBE or a DA branch means

That knowledge lives in a handful of senior engineers' heads. We pay for it
every time — in review comments that re-explain the same thing, in on-call
re-deriving a fix that already exists, in AI-authored code that matches the
code's current shape rather than how we actually build.

Incremental reviews make it concrete: today a repeat review resends the full
diff plus every prior comment, because there is no per-MR state at all.

---

## 2. Where it sits — the boundary that makes this work

> **CodeGraph** answers *"what is the code doing?"*
> **Firm Memory** answers *"why do we build it this way?"*

Two systems, queried **in parallel**. There is no RAG layer.

| | CodeGraph | Firm Memory |
| --- | --- | --- |
| Owns | symbols, call graph, imports, dependencies, line numbers, config | business rules, rationale, rejected approaches, ownership, debug history, review patterns, terminology |
| Shape | structural truth, always current | judgement, may go stale |
| Rule | — | **when memory and the code disagree, the code wins** |

**Nothing CodeGraph can answer goes into memory.** Source code, function bodies,
diffs, stack traces, AST, line numbers, config — all dynamic, all guaranteed to
drift. Also excluded: secrets, and facts about individual engineers.

Memory holds only what is expensive for a human to keep re-explaining — which is
exactly what never gets written down anywhere.

---

## 3. Architecture

```text
  OpenCode bot     On-call        Future agent
      └───────────────┼───────────────┘
                      │  MCP  (memory_search · memory_get
                      │        memory_propose · memory_correct)
             ┌────────▼─────────┐
             │  Firm Memory MCP │   thin transport adapter
             └────────┬─────────┘
                      │
             ┌────────▼─────────┐
             │   Firm Memory    │   taxonomy · scope · tiers
             │                  │   provenance · lifecycle
             └────────┬─────────┘
                      │  MemoryProvider interface
             ┌────────▼─────────┐
             │   mem0 + pgvector│   embeddings · vector search · reranking
             └──────────────────┘
```

The platform owns **what a firm memory means**. The provider owns **how it is
stored and retrieved**. MCP owns **how agents access it**. A second provider can
be swapped in without touching the bot or the MCP contract — there is an
integration test that proves it.

### The five things the platform owns

**1. Taxonomy.** Thirteen types tuned for trading systems, not consumer
assistants: `ARCHITECTURE_DECISION`, `REJECTED_APPROACH`, `CONVENTION`,
`REVIEW_PATTERN`, `BUG_FIX`, `TASK_LEARNING`, `TOOLING_SETUP`,
`DEPENDENCY_DECISION`, `PERFORMANCE_FINDING`, `BUSINESS_RULE`,
`PRODUCTION_ISSUE`, `OWNERSHIP`, `TERMINOLOGY`. Enforced in code before anything
reaches a provider — mem0's stock extraction is tuned for "user has a dog named
Max"; anything that still comes back in that shape is dropped by the taxonomy.

**2. Scope** — three *independent* attributes, not a hierarchy:

```json
{"firm": true, "domains": ["execution"], "repos": ["oms", "gateway"]}
```

Knowledge spanning Gateway → Risk → OMS → DropCopy is **stored once** and
reachable from each repo. All of it is searched in a single database call.

There is deliberately **no per-engineer and no per-team scope**. The bot is
stateless and must answer everyone identically, and we have no team-to-use-case
segregation to model. Either axis would split one fact into copies that drift
apart and that no single query can reach. A test asserts the scope object has
exactly those three fields — that is the guard against an identity axis
reappearing.

**3. Tiers** — the lifetime axis, orthogonal to approval:

| Tier | Holds | Task-scoped |
| --- | --- | --- |
| `EPISODIC` | per-MR working memory: findings and their dispositions | yes |
| `DURABLE` | distilled knowledge, written through the approval gate | never |
| `INDEX` | one verbatim card per closed issue/MR, document-shaped | never |

Search excludes `EPISODIC` by default, and that default is load bearing: to a
vector store an absent task filter means *"don't care"*, not *"unset"*, so
without it every MR's scratch state joins ordinary recall. It has a contract
test.

**4. Provenance and lifecycle.** Every memory records the MR, issue, or
interview it came from. The bot **cites the id** when it uses one, so any
engineer can check it and correct it with a comment.

```text
raw material ─► extraction ─► candidate ─► taxonomy/scope/provenance checks
             ─► human approval ─► provider.insert()
```

`ingest()` never writes. Extracting nothing is a normal and frequent outcome.
V1 is **fully human-approved**; confidence is recorded from day one so
automation can be enabled later without a migration. Business rules,
architecture decisions, firm conventions and production-critical knowledge
always need a person, whatever the confidence.

**Nothing deletes.** A correction demotes and flags; supersession names the
replacement. The record that a decision was made — and unmade — survives.

**5. Reliability.** Memory is best effort. **Reads never raise**: a provider
outage or a breach of the 2s timeout yields an empty result and a recorded
metric, so a failed recall can never fail a code review. **Writes do raise** —
silently dropping a memory an engineer just approved would be worse.

### Deployment

Self-hosted, no egress: pgvector plus a local cross-encoder reranker. Business
rules like *"MCX orders always route through Risk Engine A"* are closer to
strategy IP than to code comments, and the pool inherits the union of access
control across every repo feeding it. The embedding-model choice is the one
thing that could quietly break that, so it is config we control.

---

## 4. Where the knowledge comes from

- **Senior-engineer interviews** — the pilot repo has no ADR history, so the
  authoritative tier is seeded by interviewing two senior engineers per domain.
- **Closed issues and merged MRs** — each becomes an index card.
- **Reverted commits** — with no ADRs, `git log --grep=revert` is the only
  written record that a decision was made and unmade.
- **Repeated review comments** — a correction made three times is a convention.
- **ADRs, CHANGELOG, runbooks** as we write them — we ingest the *decision* plus
  an evidence path pointer, never the document body.

---

## 5. How this helps us

| | Today | With memory |
| --- | --- | --- |
| **Repeat MR reviews** | full diff + every past comment resent each time | only what changed, and no re-raising points the author already answered |
| **On-call** | re-derive a fix that may already exist | *"this looks like #4821"* — prior root cause and fix, before exploring |
| **Tribal knowledge** | a few heads; lost when they move on | captured once into one firm-wide pool, with no per-team copies to drift |
| **AI-authored code** | matches the code's current shape | matches how this firm builds — conventions, rejected designs, migrations in flight |
| **New joiners** | weeks of asking the same questions | the bot answers *"why is it like this?"* from day one |
| **Cross-repo changes** | each repo reviewed in isolation | a Gateway change surfaces what OMS and Risk know about it |

**The strategic point:** CodeGraph makes the bot fast at reading our code. This
makes it *knowledgeable about our firm* — and unlike a code index, it
**compounds**. Every review, every resolved incident, every correction an
engineer makes leaves the system permanently better. That asset is ours, it
stays on our infrastructure, and no vendor can take it away.

---

## 6. Status

**The platform is built and tested** — 336 passing tests, 93% coverage, covering
unit, integration (including a provider swap), MCP, and contract tests that run
our filters through mem0's real preprocessing and pgvector's SQL builder so a
mem0 upgrade fails loudly instead of the pool quietly going empty.

Working today: taxonomy, scope, tiers, lifecycle, the approval queue, the
mem0/pgvector provider, a dependency-free in-memory provider, two extractors,
and the MCP server exposing `memory_search` / `memory_get` / `memory_propose` /
`memory_correct`.

What remains is **seeding the pool and wiring it into the bot** — see
[BRIEF_2_DELIVERY_PLAN.md](BRIEF_2_DELIVERY_PLAN.md). Roughly 16–18 engineering
days across seven phases on one pilot repo, with value visible from week one.

**The cheap proof point is backfilling closed issues** (about a day). We can
demo recall against issues the team already remembers, before any
judgement-dependent machinery exists. If recall quality is poor there, we learn
it cheaply and stop.

---

## 7. Try it in five minutes

```bash
uv sync
cp .env.example .env          # fill in the two credentials
python examples/review_comment.py
```

It takes one real PR review comment and shows you what it would propose — and
that nothing is stored until a human approves it. See
[examples/](examples/) and [README.md](README.md) for the full API and
configuration.
