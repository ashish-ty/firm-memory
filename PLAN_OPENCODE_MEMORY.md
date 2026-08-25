# Plan: Memory for the OpenCode GitLab Bot

Building on `firm_mem0` (namespace contract, layers, taxonomy, pgvector-backed
self-hosted mem0 OSS).

> **Governing principle**
> Memory never replaces the codebase. It helps the agent search and reason over
> the codebase. CodeGraph answers *what the code is*; memory answers *why it is
> that way, who owns it, and what usually breaks*.

---

## 1. Requirements restated

The bot is **stateless**. Every trigger — MR review, plan-mode on an issue, PR
generation — is an independent call whose context is the checkout plus whatever
`.open-code/` skills the repo carries.

| # | Gap | Solved looks like |
|---|---|---|
| G1 | **No per-MR working memory.** Each review iteration replays the full diff and every prior comment. | Iteration *n* reads a compact timeline of 1..*n-1* and only the diff since the last reviewed SHA. |
| G2 | **No recall of prior resolutions.** On-call re-derives fixes for issues already closed. | Before exploring, the bot retrieves the most similar closed issues with root cause and fix location. |
| G3 | **No durable organizational knowledge.** `.open-code/` captures explicit conventions; the tacit layer — business rules, rationale, ownership, tribal build quirks — lives in senior developers' heads. | Memory accumulated automatically from merged work and human corrections, injected into every invocation. |

And the long-term objective: **AI-authored change stays in sync with how this
codebase has historically been developed** — not just with what the code
currently says.

---

## 2. The two-system split

This is the ownership boundary. Get it wrong and memory rots into a stale
mirror of the code.

**CodeGraph is a search index over the codebase and nothing more.** There is no
separate RAG system. Memory therefore owns *both* the tacit knowledge nobody
wrote down and the durable written record — ADRs, CHANGELOG, postmortems,
design docs.

| System | Owns | Nature | Staleness risk |
|---|---|---|---|
| **CodeGraph** | Structural truth: symbols, call graph, dependency graph, inheritance, imports, implementations, line numbers, config | Derived, re-indexed on every commit | none — regenerated |
| **mem0** | Everything else durable: business rules, architectural rationale (from ADRs), change history, ownership, debug history, review patterns, terminology, preferences | Part ingested from documents, part accumulated from interaction | **high — this is the risk to engineer against** |

### Never in memory

Source code · function bodies · class definitions · API contracts · imports ·
AST · call hierarchy · dependency graph · line numbers · configuration files.

All of these are dynamic and CodeGraph already answers them. Storing them
guarantees drift.

### Documents: ingest the decision, not the document

Memory is not a document store. The rule that keeps this from degenerating into
a bad RAG:

> **Store the compressed decision plus a path pointer. The agent reads the full
> document from the checkout when it needs detail.**

`ADR-104: Kafka over Redis — ordering guarantee, replay, multiple consumers.
[evidence: docs/adr/104.md]` is what belongs in memory. The 4-page ADR does not,
because the compressed form is what you inject into every review preamble and
the full text is one `Read` away in the checkout anyway. This gives you the
retrieval benefit with none of the storage bloat, and it needs no RAG system.

### Append-only documents are safe; mutable ones need a sync key

This is the distinction that actually governs drift, and it lands in your favour:

| Document | Mutability | Handling |
|---|---|---|
| ADRs | append-only by convention — superseded, never edited | ingest freely; a superseding ADR writes a new memory and marks the old one `superseded_by` |
| CHANGELOG | append-only | ingest **selectively** — see below |
| Postmortems | append-only | ingest freely |
| README, design docs, runbooks | **edited in place** | ingest with a `doc_sha`; a re-ingest job supersedes memories whose `doc_sha` no longer matches the file |

The `doc_sha` field is the whole mitigation for mutable documents: a nightly job
hashes each tracked document, and any memory carrying a stale hash is re-derived
or dropped. Without it, a memory extracted from last year's README outlives the
rewrite and nobody notices.

**CHANGELOG needs a filter, not a firehose.** A mature repo's CHANGELOG has
thousands of entries; ingesting all of them would consume the entire per-repo
memory budget (§9) with low-signal lines and collapse retrieval precision.
Ingest only **breaking changes, deprecations, and behaviour changes** — the
entries that answer *"when did this stop working the old way?"* Routine feature
and fix lines are already recoverable from git and the resolution index.

### Memory answers

*Why was this written? Who owns this? What usually breaks here? What should I
avoid? What happened last time? What does this acronym mean?*

### CodeGraph answers

*Where is `Order` created? Who calls `RiskManager`? Show the execution path.
Find all subclasses. Who imports this?*

---

## 3. Memory tiers inside mem0

The CodeGraph/mem0 split is about **kind of knowledge**. This is a second,
orthogonal axis: **lifetime and write path**.

```
Tier A — EPISODIC          Tier B — SEMANTIC            Tier C — INDEX
per-MR working memory      durable org knowledge        resolution catalogue
run_id = mr-4821           no run_id                    no run_id
tier=episodic              tier=durable                 tier=index
infer=False (verbatim)     infer=False, auto-reconcile  infer=False (verbatim)
high volume, cheap         gated + consolidated         one card per closed issue
written every iteration    written at MERGE only        written at CLOSE only
nothing is ever deleted — see "Retention" below
         │                          ▲                            ▲
         └──── DISTILLATION ────────┤                            │
                gate: 0–5 facts     │                            │
                                    │                            │
               LEARNING PIPELINE ───┤          CARD BUILDER ─────┘
          human corrections → rules │        one card per closed
                                    │        issue / merged MR
                 DOCUMENT INGEST ───┘
      ADRs, CHANGELOG, postmortems
      → decision + evidence pointer
```

Three write paths feed Tier B, and they differ in trust: document ingest is
**authoritative** (a merged ADR is a decision the team made), distillation is
**inferred** (needs the confidence gate), and the learning pipeline is
**observed** (needs the promotion ladder in §7). Confidence should be set
accordingly — `1.0`, `0.7`, `0.3` respectively — because that single number is
what decides whether something reaches the review preamble.

**With no ADR history in the pilot repo (confirmed 2026-08-01), the
authoritative tier comes from people instead of documents:** the Phase 2c
seeding session writes at `confidence=1.0`. The model is unchanged; only the
source of authority moves. This makes 2c load-bearing rather than optional — see
§11.

**Tier A vs. the "never store code" rule.** Tier A stores *findings and their
dispositions* — "flagged N+1 in the orders repository; author says intentional,
nightly batch, bounded" — never diff bodies. It is scratch state, not knowledge.

**Tier C is deliberately document-shaped**, not fact-extracted. Running mem0's
extraction over a closed issue shreds the symptom → root cause → fix structure
that is the entire reason you want it back.

### Growth: the pool should grow, but along the right axis

An earlier draft said *"a merged MR typically yields zero durable facts, and
that must be the common outcome — if every MR writes to Tier B the pool is
unqueryable within a quarter."* That framing was too absolute, and the
objection to it is correct: **the codebase grows, so its memory must grow too,
and dropping changes on the floor is not an acceptable design.** Revised
position:

**Volume is not the problem; near-duplication is.** pgvector will happily serve
millions of rows. What breaks is *retrieval precision*: with `top_k=5` and forty
memories that all say roughly "this service uses the sequencer for ID
allocation," the five returned are effectively arbitrary and the one specific
memory you needed is unreachable. That is a ranking failure, not a storage
failure, and the fix is consolidation rather than suppression.

**Nothing is missed, because the tiers divide the work.** The fear behind
"we shouldn't be missing the changes" is real, but Tier B is not where changes
are recorded:

| Question | Answered by |
|---|---|
| *What* changed, and when? | git + Tier C resolution index — complete, never gated |
| *Why* did it change, and what should I now do differently? | Tier B — gated, consolidated |

Tier C writes a card for **every** closed issue and merged MR. The full change
record is captured, in full, ungated. The gate applies only to the *judgement*
layer, where a fortieth restatement of a known convention adds nothing and costs
retrieval precision. Nothing is lost by gating Tier B, because Tier C already
has it.

**Three mechanisms keep growth healthy** — the second and third are your
suggestions, and both are now in the plan:

1. **Per-MR budget, not a binary gate.** The distiller emits 0–5 candidates
   ranked by confidence; the cap is a budget, not a prohibition. A large,
   genuinely novel MR can write five facts. A typo fix writes none — not
   because it is being suppressed but because it has nothing to say.
2. **Write-time reconciliation.** When a candidate is a near-duplicate of an
   existing memory (similarity ≳0.85), it is written with `infer=True` so mem0's
   ADD/UPDATE/DELETE pass *merges* it into the existing memory rather than
   appending a fortieth variant. The new information lands; the duplicate does
   not.
3. **Periodic consolidation.** A monthly pass per `(component, type)` cluster:
   where a group of memories has converged on the same fact, an LLM writes one
   sharper memory and marks the originals `superseded_by` it. **The originals
   are not deleted** — they remain retrievable and auditable, just ranked below
   the consolidated version. This is the summarization you described, and it is
   the mechanism that lets the pool grow indefinitely without retrieval decay.

**The metric changes accordingly.** Track *distinct facts* and *retrieval
precision*, not row count. `pool size ÷ hit rate` stays in the dashboard (§9)
as the rot detector — a pool that grows while hit rate flatlines means
consolidation is not keeping up, which is now an actionable signal rather than
a reason to write less.

### Retention: nothing expires, nothing is deleted

**Decision (2026-08-01): no expiry and no automatic deletion in v1.** Removed
from the plan: `expiration_date` on Tier A, the nightly sweeper, and
decay-driven pruning. mem0's native expiry stays available for a later phase if
retrieval pressure ever demands it, but it is not wired up now.

What replaces deletion:

| Instead of | Use |
|---|---|
| expiring Tier A after 30 days | nothing — episodic memories persist; they are excluded from normal recall by `tier` (§4), not by expiry |
| deleting stale memories | `superseded_by` + a lower `confidence` — the memory drops out of the preamble but stays queryable and auditable |
| expiring migration campaigns | an explicit `status=active\|completed` flag, flipped by a human when the migration lands |

The one deletion path that stays is **`@opencode-bot forget <id>`** — a human
explicitly correcting a wrong memory. That is a correction mechanism, not
automatic expiry, and removing it would leave no way to retract a bad fact.
Flagging it in case you want that gone too; if so, `forget` becomes a
`confidence=0` + `retracted=true` marking instead, and nothing is ever removed
from the store.

### Precedence rule (must be in the bot's system prompt)

> The codebase is ground truth; CodeGraph reports it. Memory is a hypothesis
> about the codebase. When they disagree, the code wins and the memory is
> flagged stale.

Without this, one stale memory silently degrades review quality and the team
stops trusting the bot — which is unrecoverable.

---

## 4. Namespace: the cross-repo problem

`firm_mem0` spends mem0's three axes as owner / repo / task, and only
`Layer.FIRM` is cross-repo. **That does not fit the knowledge you described.**

> "MCX orders always go through Risk Engine A."
> "Execution flow: Gateway → Risk → OMS → Exchange → DropCopy."

This knowledge spans several repos and belongs to none of them. Scoping it to
`agent_id=<repo>` makes it invisible from the other four repos in the flow.
Putting it in `Layer.FIRM` works mechanically but corrupts that layer's meaning
— it is defined as small, lead-curated, cross-repo *conventions*, and agents
should not write it unattended.

### Proposal: add `Layer.DOMAIN`

| Layer | `user_id` | `agent_id` | Scope | Written by |
|---|---|---|---|---|
| `REPO` | `repo:<slug>` | repo slug | one repo, optionally task | agents (bulk) |
| **`DOMAIN`** | **`domain:<slug>`** | **none** | **a business/system domain, spanning repos** | **agents, gated** |
| `FIRM` | `firm` | none | cross-repo conventions | leads, curated |

`domain:execution`, `domain:risk`, `domain:oms`, `domain:mcx`,
`domain:backtest`. A repo declares which domains it participates in via
`.opencode/memory.toml`; `recall()` unions REPO + its domains + FIRM in one
call. `layered_filters` already ORs flat dicts, so a DOMAIN branch is
`{"user_id": "domain:execution"}` — no change to the filter machinery.

### No layer is owned by a person or a team

Every axis above is a property of **the code or the firm**, never of the caller.
There is no `eng:<name>` layer and no `team:<team>` layer, and the library reads
no engineer or team identity from the environment. Two reasons, both structural:

- **The bot is stateless and serves everyone identically.** Nothing about an
  answer should depend on who opened the MR. A per-engineer pool makes the same
  question return different answers to different people, which is a correctness
  problem disguised as personalisation.
- **We have no team-to-use-case segregation.** No team owns a domain, a repo
  set, or a class of work — the same repos and the same use cases are worked on
  across the firm. A team axis would therefore partition the pool along a line
  that does not exist in the organisation, splitting one fact into N copies that
  no single query can reach and that drift apart independently.

`Layer.REPO` consequently owns memory as `repo:<slug>` rather than
`team:<team>`. `user_id` partitions the layer, `agent_id` carries the repo
scoping axis every filter already speaks; both derive from the same slug, which
is itself derived from the git remote and never configured.

The cost is real and accepted: reviewer-preference memory (old use case L) is
**out of scope**, because with no per-person layer one engineer's stated
preference would be applied to everyone. What survives is the firm-level form of
the same idea — a convention the codebase follows, curated into `FIRM`.

This is the largest structural change the new requirements imply, and it is
worth making before any data lands, because re-scoping a populated pool is
painful.

### Metadata axes

Existing (`type`, `layer`, `repo`, `branch`, `source`) plus:

| Key | Purpose |
|---|---|
| **`tier`** | **`episodic` \| `durable` \| `index`. Load-bearing — see the retrieval bug below.** |
| **`component`** | **the join key between a diff and its memories — see §6.** |
| `domain` | domain slug, denormalised from `user_id` for filtering within a recall |
| `service` | deployable unit, coarser than component |
| `tags` | free-form, comma-joined (pgvector `contains` matches substrings) |
| `confidence` | 0.0–1.0, numeric — gates what reaches the prompt |
| `confirmed_at` | last human/code corroboration; a ranking signal, never a delete trigger |
| `evidence` | MR/issue/ADR URL or `seed:<engineer>:<date>` — every memory auditable to its origin |
| `mr` / `issue` | exact-match filtering and citation |
| `sha` | commit the memory derives from |
| `dedupe_key` | CI retries must not double-write |
| `trigger` | symbol/path that activates a rule (§7) |
| `doc_sha` | content hash of the source document — the drift key for mutable docs (§2) |
| `superseded_by` | memory id that replaces this one — the no-delete alternative to expiry |
| `status` | `active` \| `completed`, for migration campaigns (replaces `expiration_date`) |

`expiration_date` is **not used** — see "Retention" in §3.

**Verified against the installed mem0:**

- pgvector supports `eq/ne/gt/gte/lt/lte/in/nin/contains/icontains`
  ([`pgvector.py:37-46`]), so `{"confidence": {"gte": 0.7}}` and
  `{"component": {"in": [...]}}` are expressible.
- `Memory.add(..., expiration_date=...)` exists and hides expired rows from
  `search`/`get_all` ([`main.py:744`, `main.py:1325`]). Available if retention
  is ever wanted; deliberately unused in v1.

**Caveat:** `firm_mem0.layers.layer_filter` types metadata filters as
`Mapping[str, str]` and copies them flat. Operator dicts need a type widening,
and their behaviour *inside an `OR` branch* must be locked in by
`tests/test_oss_filter_contract.py` before anything depends on it — nested-`OR`
handling is exactly where this repo has already been bitten twice, and both
failures were silent (zero rows, no exception).

### How the axes actually get spent

Two rules decide every scoping question:

> **Set `agent_id` only if the memory is about one repo and must not surface
> from another.**
> **Set `run_id` only if the memory is meaningless outside the task that wrote
> it.**

Restated as the question to ask at each write: *would I ever want this back
while working on a different task?* If yes, no `run_id`. *While working in a
different repo?* If yes, no `agent_id` — use `DOMAIN` or `FIRM` instead.

`agent_id` and `run_id` are **scoping** axes: they decide what a query can
reach. The `repo` metadata key is **provenance**: it records where a memory came
from without restricting where it can be read. A domain memory therefore carries
`repo=quant-oms-service` in metadata (audit: this came from the OMS repo) while
leaving `agent_id` unset (scope: readable from every repo in the domain). Those
two are easy to conflate and the consequences are opposite.

### Worked examples

Seven memories, one per axis combination, in your domain.

**1 · Tier A — episodic MR review state.** All three axes set.

```
user_id  : repo:quant-oms-service
agent_id : quant-oms-service
run_id   : mr-4821
memory   : "Iteration 3 @ 9f2ab1c. Flagged unbounded fetch in the order-replay
            path. Author: intentional, bounded by the sequencer window.
            Accepted, will not re-raise. Open: retry semantics in the
            drop-copy client."
metadata : tier=episodic  type=review_state  layer=repo
           repo=quant-oms-service  branch=fix/replay-window  component=order-replay
           mr=4821  sha=9f2ab1c  source=gitlab  confidence=1.0
           dedupe_key=mr-4821-iter-3
```

**2 · Tier B — repo-scoped convention.** No `run_id`: it must outlive the MR.

```
user_id  : repo:quant-oms-service
agent_id : quant-oms-service
run_id   : —
memory   : "Order IDs are allocated by the sequencer, never by the service.
            A service-generated ID breaks replay ordering."
metadata : tier=durable  type=coding_conventions  layer=repo
           repo=quant-oms-service  component=order-core  source=distill
           confidence=0.7  evidence=<mr-4903-url>  confirmed_at=2026-08-01
```

**3 · Tier C — resolution index.** *Note what is deliberately absent.*

```
user_id  : repo:quant-oms-service
agent_id : quant-oms-service
run_id   : —          ← NOT issue-4821, and this matters
memory   : "[resolved] #4821 — Order submit 504s under bulk import.
            Symptom: p99 > 30s whenever a >5k-row import ran.
            Root cause: cache stampede in the position-cache warmer.
            Fix: request coalescing. Links: #4821, !4903"
metadata : tier=index  type=issue_resolution  layer=repo
           repo=quant-oms-service  issue=4821  component=position-cache
           state=closed  source=gitlab  confidence=1.0  evidence=<issue-url>
```

**Setting `run_id=issue-4821` here would destroy the feature.** `run_id` scopes
a memory to *the task currently being worked on*. The entire point of the
resolution index is to surface issue #4821 while someone is working on issue
**#5177** — so binding it to its originating task makes it reachable only by
someone who already knows to look for it. The issue number belongs in metadata,
where it filters and cites without restricting reach.

**4 · `Layer.DOMAIN` — cross-repo business rule.** No `agent_id`.

```
user_id  : domain:execution
agent_id : —          ← so it is visible from all five repos in the flow
run_id   : —
memory   : "MCX orders always route through Risk Engine A. Risk Engine B is
            KRX-only; routing MCX through it silently drops the margin check."
metadata : tier=durable  type=business_rule  layer=domain  domain=execution
           component=risk-routing  repo=quant-oms-service   ← provenance only
           source=seed  confidence=1.0  evidence=seed:priya:2026-08-05
```

**5 · `Layer.DOMAIN` — terminology.**

```
user_id  : domain:execution
memory   : "CBE = Contract Based Execution. DA branch = Data Audit branch."
metadata : tier=durable  type=glossary  layer=domain  domain=execution
           source=seed  confidence=1.0
```

**6 · `Layer.FIRM` — curated cross-repo convention.**

```
user_id  : firm
memory   : "All Python services use uv for dependency management; never pip or
            poetry directly."
metadata : tier=durable  type=coding_conventions  layer=firm
           source=curated  confidence=1.0
```

### What one `recall()` compiles to

From the OMS repo, on a repo declaring `domains = ["execution", "risk"]`. Note
that nothing in the filter depends on who is calling:

```python
{"OR": [
  {"user_id": "repo:quant-oms-service", "agent_id": "quant-oms-service",   # REPO
   "tier": {"in": ["durable", "index"]}},
  {"user_id": "domain:execution", "tier": {"in": ["durable", "index"]}},
  {"user_id": "domain:risk",      "tier": {"in": ["durable", "index"]}},
  {"user_id": "firm",             "tier": {"in": ["durable", "index"]}},
]}
```

One `search()` call, four branches, examples 2–6 reachable and example 1 not.

### The bug this surfaced: `recall()` would drag in every MR's scratch state

Writing the examples out exposed a real defect in the design as it stood, worth
recording because it is precisely the class of failure this repo has already
been bitten by twice.

**Absence of `run_id` in a filter does not mean "`run_id` is unset" — it means
"don't care."** Filters are conjunctive over the keys present, so the REPO
branch `{"user_id": "repo:quant-oms-service", "agent_id": "quant-oms-service"}` matches Tier B
*and* Tier A: every iteration card from every MR ever reviewed in that repo.
With hundreds of MRs and no expiry (§3), episodic scratch state would
progressively crowd out durable knowledge in every recall. It fails **silently**
— plausible-looking results, quietly worse over time, and no error anywhere.

**Fix: the `tier` metadata key, filtered by default.** `recall()` defaults to
`tier in [durable, index]`; `timeline()` is the only reader of `tier=episodic`
and reaches it through `run_id`. This is why `tier` is a stored field rather
than something derived from `type` at query time — deriving it means every new
episodic type is a chance to reintroduce the same bug.

This must be locked in by `tests/test_oss_filter_contract.py` in Phase 0,
asserting that a plain `recall()` returns zero `tier=episodic` rows.

### CI identity

`FIRM_MEM0_REPO=$CI_PROJECT_PATH` — **pin it.** GitLab CI rewrites
`remote.origin.url` with a job token, so `resolve_repo_slug()` will drift
between runner types and silently partition the pool. `run_id` is
`mr-<iid>` / `issue-<iid>`.

---

## 5. Taxonomy

Your ten categories and the existing `CODING_CATEGORIES` overlap but neither is
a superset. Yours adds what matters most for a trading firm and is entirely
missing today: **business rules, terminology, ownership, environment quirks,
deployment order**.

**Recommendation: one flat `type` vocabulary (storage), grouped into your ten
categories (retrieval routing).** Two separate stored axes would be
over-engineering; a `type → category` map used at filter-construction time gets
the routing benefit for free. Keeping one flat vocabulary also preserves the
shared vocabulary with the mem0 editor plugin that `taxonomy.py` is built
around.

| Category (routing) | `type` values |
|---|---|
| Business | `business_rule`, `domain_constraint` |
| Architecture | `architecture_decisions`, `dependency_decisions` |
| Debug | `bug_fixes`, `incident`, `flaky_test`, `issue_resolution` |
| Review | `review_feedback`, `review_rule`, `anti_patterns` |
| Workflow | `deployment_order`, `release_process` |
| Environment | `tooling_setup`, `build_quirk` |
| Ownership | `ownership` (which repo/component, never which person) |
| Project | `task_learnings`, `performance_findings`, `migration_campaign`, `change_history` |
| Terminology | `glossary` |

New types to add to `taxonomy.py`: `business_rule`, `domain_constraint`,
`glossary`, `ownership`, `build_quirk`, `deployment_order`, `release_process`,
`incident`, `flaky_test`, `issue_resolution`, `review_rule`,
`migration_campaign`, `review_state`, `change_history`.

Examples in your domain, to make the categories concrete:

```
business_rule      MCX orders always route through Risk Engine A.
domain_constraint  Strategy X never trades after 15:20 — the exchange rejects.
                   The code shows the time check; only memory holds the reason.
architecture_...   ADR-104: Kafka over Redis — ordering guarantee, replay,
                   multiple consumers. [evidence: docs/adr/104.md]
change_history     v4.2 (2025-11): order-submit responses became async;
                   callers relying on a synchronous ack must migrate.
                   [evidence: CHANGELOG.md]
ownership          execution/ and broker/ are owned by the Execution team.
build_quirk        Backtest only runs under the balte env; analytics-api must
                   be up first.
deployment_order   MCX deploy: service A → service B → service C.
glossary           CBE = Contract Based Execution. DA branch = Data Audit.
anti_patterns      ExecutionService is legacy; do not modify without approval.
review_rule        Redis must only be accessed via CacheManager.
                   [trigger: redis, StrictRedis]
```

The existing exclusion block in `taxonomy.py` already forbids code bodies,
diffs, secrets, and transient state. Extend it with **"do not store anything
CodeGraph can answer"** so extraction stops emitting call-graph restatements.

---

## 6. Retrieval: memory and CodeGraph in parallel

```
                      Task / question
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
         CodeGraph                     mem0 recall
      structural search        org knowledge + doc rationale
              │                             │
              └──────────────┬──────────────┘
                             ▼
                    Merge + budget + dedupe
                             ▼
                            LLM
                             │
                             ▼
              follows an `evidence:` pointer and
              Reads the full ADR / doc from the
              checkout, only when it needs detail
```

The third arm is not a system, it is a **pointer follow**. Memory returns
`ADR-104 … [evidence: docs/adr/104.md]`; if the agent needs the full argument it
opens the file. That is why the compression rule in §2 costs nothing: no detail
is lost, it is just not paid for on every prompt.

### On the intent classifier — what I was arguing against

Your original diagram had a step between the question and the two stores:

```
   Question → [Intent Classifier] → "needs code?"   → CodeGraph
                                  → "needs memory?" → mem0
```

That classifier is a decision, made by a model, about **which stores to query at
all**. My recommendation is to delete that box and always query both:

|  | With classifier | Without |
|---|---|---|
| Cost per question | +1 LLM call before any retrieval | 0 extra |
| Latency | +300–800ms serial, before either store is touched | 0 extra; the two run in parallel |
| Savings when it works | skips one ~200ms call | — |
| Cost when it is wrong | the agent never learns the memory existed, gives a confidently wrong answer, and **nothing in the logs shows a miss** | — |

The trade is bad in both directions: you pay an LLM call to avoid a cheaper
call, and the failure mode is silent. A question like *"why does the OMS reject
orders after 15:20?"* looks purely structural — a classifier could easily route
it to CodeGraph alone, and the code would show only `if time > cutoff`, with the
*reason* sitting unread in memory.

So: fan out to both unconditionally. **The real constraint is prompt budget, not
retrieval cost**, and that constraint is better enforced at the merge step,
where you can see what both stores actually returned and rank across them, than
by a classifier guessing beforehand. If cost ever does become the binding
constraint, the cheap fix is a rules-based skip (e.g. don't recall memory for a
pure "find the definition of X" lookup) rather than a model call.

### The join key: `component` — and where it now comes from

Exact-match on `component` is what surfaces *"this module has caused 3 prior
production regressions"*; semantic search alone will not reliably find it.

**CodeGraph does not expose a component identifier** (confirmed 2026-08-01 — it
is a code search index, nothing more). So the mapping has to be maintained
declaratively, per repo, in `.opencode/memory.toml`:

```toml
[components]
"src/oms/replay/**"      = "order-replay"
"src/oms/core/**"        = "order-core"
"src/risk/routing/**"    = "risk-routing"
"src/cache/position/**"  = "position-cache"

[domains]
participates = ["execution", "risk"]
```

```
changed files (from the MR)
   → path-prefix match against .opencode/memory.toml
   → component keys
   → recall filtered by {"component": {"in": [...]}}
```

**This is a downgrade from the previous draft and worth being honest about.** A
config file drifts as directories move, needs maintenance nobody is assigned,
and starts life incomplete. Three things make it workable:

1. **Degrade, do not fail.** An unmapped path yields no component filter, and
   recall falls back to plain semantic search over the repo. Worse precision,
   never an error. The system must work at zero coverage on day one.
2. **Let the map grow from use.** When distillation writes a memory about an
   unmapped path, it proposes a component name into the review queue rather than
   inventing one silently — which is also the answer to *"who defines new
   component and domain slugs"*: **a human, at review time** (your call,
   2026-08-01).
3. **Coarse beats precise.** Ten components per repo that match how the team
   actually talks (`order-core`, `risk-routing`) beat sixty auto-derived module
   names. The map is a vocabulary, not an index — it should read like the
   whiteboard, not the directory tree.

The 2c seeding session (§9) is the natural place to draft it: while a senior
engineer is describing what breaks where, they are already naming the
components.

### Two injection modes, both needed

1. **Preamble** — always on. One `recall()` filtered to
   `{business_rule, domain_constraint, architecture_decisions, anti_patterns,
   ownership, glossary}` with `confidence >= 0.7`, top 5–8, injected as *"What
   we know about this system."* Catches what the model would never think to ask.
2. **`memory_search` tool** — on-demand when the agent hits ambiguity. Higher
   precision, but only fires if the model thinks to call it. Not a substitute
   for (1).

### Citation contract — non-negotiable

When the bot acts on a memory it emits the id:

> Use idempotency keys rather than retry-on-500 — decided in !3312
> (memory `a1b2c3`).

This buys auditability, the hit-rate metric, and a feedback surface, all at
once. Ship it with the first retrieval wiring, not later.

### Feedback commands in MR comments

- `@opencode-bot forget a1b2c3` → `forget()`, logged with who and why
- `@opencode-bot remember: <fact>` → curated write, `infer=False`, `confidence=1.0`
- `@opencode-bot why?` → dump the memories used in the last pass

This converts memory from a black box into a team artifact, and is what
sustains trust past month two.

---

## 7. The learning pipeline

This is the feature that makes the bot converge on *your firm's* engineering
practice rather than generic best practice. It deserves its own design, because
it is also the most dangerous component in the system.

### Signal sources

| Signal | Strength | Detection |
|---|---|---|
| Human corrects the bot in a thread (*"No, use UnifiedPositionService"*) | strongest | bot comment + human reply that rejects it |
| Human review comment on any MR, repeated across MRs | strong | cluster comments by similarity |
| Bot suggestion accepted and merged unchanged | moderate | confirms an existing memory → bump `confirmed_at` |
| A commit reverts bot-authored code | strong | `git log --grep=revert` on bot commits |
| Explicit `@opencode-bot remember:` | strongest | direct |

### Facts vs. rules — an important distinction

Your example is not a fact, it is a **rule**:

```
trigger:        PositionManager
recommendation: use UnifiedPositionService
type:           review_rule
```

Facts are retrieved *semantically* at reasoning time. Rules are matched
*exactly* against the symbols CodeGraph reports as changed, and fire
deterministically at review time. Store rules with a `trigger` metadata field
holding the symbol or path; review then does an exact lookup rather than hoping
the embedding lands. Far more reliable, and it is what makes "the review agent
catches it" actually true.

### Promotion ladder — do not skip this

A rule auto-learned from one comment is a durable, firm-wide behaviour change
derived from untrusted text. So:

| Stage | Trigger | Effect |
|---|---|---|
| **Observation** | 1 sighting | stored, `confidence=0.3`, retrievable but below the preamble threshold |
| **Candidate** | 3 corroborations, or 1 explicit `remember:` | `confidence=0.7`, enters the preamble |
| **Rule** | human approval in a review queue | `confidence=1.0`, fires deterministically on `trigger` |
| **Firm-wide** | lead approval | promoted to `Layer.FIRM` or `Layer.DOMAIN` |

Agents never write `FIRM` unattended. `DOMAIN` requires the candidate stage.
The queue can be a weekly digest MR against a `memory/` file — cheap to build,
and it puts curation where engineers already work.

### Distillation gate (Tier A → Tier B, at merge)

1. Load the MR's Tier A timeline + diff stat + review threads + linked issue.
2. Run the `memory-distill` skill → `[{text, type, component, confidence,
   evidence_url, scope: repo|domain|firm}]`, 0–5 items.
3. Drop `confidence < 0.7` and anything matching the exclusion rules.
4. `recall()` each survivor first:
   - near-duplicate above ~0.85 → write `infer=True` so mem0 reconciles via its
     ADD/UPDATE/DELETE pass (this is the one place that pass earns its cost)
   - otherwise → `infer=False`, distiller's sentence stored verbatim
5. `scope != repo` → promotion queue, never a direct write.

**Distiller prompt shape** (the load-bearing half):

> Decide what a developer joining this team in six months would need to know
> that they could not learn by reading the code or querying the call graph. Emit
> nothing visible in the diff. Emit nothing that restates the ticket. If the MR
> was routine, emit an empty array — this is the expected outcome for most MRs.
> Prefer facts of the form *"X was chosen/rejected because Y"* and business
> rules of the form *"A always/never B, because C."*

---

## 8. API gaps in `firm_mem0`

Current facade: `remember` / `recall` / `forget` / `for_task`. Needed:

| Addition | Why | Where |
|---|---|---|
| `timeline(task, *, limit)` | Tier A retrieval is "everything for `mr-4821`, in order", not semantic search. Wraps `get_all(filters=...)`. **Blocking for G1.** | `client.py` |
| `Layer.DOMAIN` + `domain` in `Namespace` | cross-repo business knowledge (§4) | `namespace.py`, `layers.py` |
| operator-dict filters | `confidence >= 0.7`, `component in [...]` | `layers.py` (type widening + contract test) |
| **`tier` default on `recall()`** | **excludes `episodic` from normal recall — without it every MR's scratch state pollutes every query, silently (§4)** | `client.py`, `layers.py` |
| `remember_many(items)` | backfill writes hundreds of cards | `client.py` |
| `dedupe_key` | CI retries are routine | `client.py` |
| `scrub(text)` | secret/PII filter at the write boundary — the taxonomy's "do not store secrets" is an instruction to an LLM, not a control | new `scrub.py` |
| ids in `recall()` results | citations need them; make it explicit in the contract test | `client.py` |
| async write path | review latency must not include an LLM write round trip | new `writer.py` |
| `supersede(old_id, new_id)` | the no-delete alternative to expiry (§3) | `client.py` |
| new types | §5 | `taxonomy.py` |

No `retention.py` and no `expiration_date` passthrough: retention is out of
scope for v1 (§3).

`FirmMemory` is immutable and `for_task()` returns a new facade — preserve that
for every addition.

---

## 9. Phases

### Phase 0 — Foundations · **Low** · ~2 days
`Layer.DOMAIN`; `tier` and its default filter; operator-dict filters;
`timeline()`; `remember_many()`; `dedupe_key`; `supersede()`; `scrub.py`;
taxonomy extension.

Already landed (2026-08-03): `Layer.PERSONAL` and the `engineer`/`team` axes are
removed, `Layer.REPO` is owned by `repo:<slug>`, and `FIRM_MEM0_TEAM` /
`FIRM_MEM0_ENGINEER` are gone from configuration. Doing this before any data
lands is the whole point — re-scoping a populated pool is painful.

Extend `test_oss_filter_contract.py` to cover `get_all(run_id=...)`, operator
dicts **inside `OR`**, the DOMAIN branch, and — the one that protects against a
silent, gradual failure — **a plain `recall()` returning zero `tier=episodic`
rows after episodic writes exist** (§4).

*Exit:* coverage ≥80%, contract tests green against the installed mem0.

### Phase 1 — G1: episodic MR memory · **Low** · ~2 days

```python
mr = memory.for_task(f"mr-{iid}")
mr.remember(
    "Iteration 3 @ 9f2ab1c. Flagged N+1 in the orders repository. Author: "
    "intentional, nightly batch, bounded at 200 rows. Accepted, will not "
    "re-raise. Open: retry semantics in the payments client.",
    memory_type="review_state", infer=False, tier="episodic",
    sha="9f2ab1c", mr=str(iid), component="order-replay",
    dedupe_key=f"mr-{iid}-iter-3",
)
```

Read via `timeline()`; diff from `timeline[-1].sha` rather than merge-base. Each
card must carry the **disposition** of every prior finding — `accepted`,
`rejected-with-reason`, `fixed`, `open` — because that is what stops the bot
re-raising a nit the author already argued down.

*Exit:* on a 4-iteration MR, iteration 4 sends <40% of today's tokens and
repeats no finding resolved in 1–3.

### Phase 2 — Seeding the pool · **Medium** · ~3 days

Three write paths that share a shape: all read existing artifacts or human
knowledge, all write `infer=False`, and none depends on Tier A, CodeGraph, or
the distillation gate. This is where the pool gets enough content to be worth
querying at all.

> **There are no ADRs today.** An earlier draft of this plan made ADR ingest the
> lead seeding activity. With no ADR history in the pilot repo, that payoff
> disappears and the ordering below changes accordingly: **2c, the human seeding
> session, is now the primary seed.** Document ingest still gets built — READMEs
> and runbooks exist, and ADRs may start existing (see use case P) — but it is
> no longer what makes the pool useful on day one.

#### 2a — Document ingest (build it, expect little from it now)

A job that runs on merge to the default branch whenever an allowlisted document
changes — `docs/adr/**`, `CHANGELOG*`, `docs/postmortems/**`, tracked design
docs, READMEs:

1. For each document, compute `doc_sha`.
2. Skip if a memory already carries that `doc_sha` (idempotent, so it can also
   run as a full sweep).
3. Extract the **decision plus rationale** — for an ADR, typically 1–3 sentences
   — and write with `type=architecture_decisions`, `evidence=<path>`,
   `doc_sha`, `component`, `confidence=1.0` (a merged ADR is authoritative).
4. A superseding ADR writes a new memory and sets `superseded_by` on the old
   one. Do not delete: *"we used to do X, changed to Y in ADR-118"* is exactly
   the kind of thing the bot should know, and it is the reason ADRs are
   append-only in the first place.
5. Memories whose `doc_sha` no longer matches the file are re-derived (mutable
   docs) — this is the drift control from §2.

CHANGELOG uses the selective filter from §2: breaking changes, deprecations,
behaviour changes only.

**Why build it now if there is nothing to ingest?** Two reasons. READMEs and
runbooks exist in every repo and carry real build/deploy knowledge. And the
ingest job is the landing pad for ADRs if the team starts writing them — the
`doc_sha` and `superseded_by` machinery is a day of work now versus a
retrofit later. Do not let it hold up 2b or 2c.

#### 2b — Resolution index (G2)

Card template, written `infer=False`, `type=issue_resolution`:

```
[resolved] #4821 — Checkout 504s under bulk import
Symptom:    p99 on order submit > 30s whenever a >5k-row import ran.
Root cause: write-through cache stampede in the cart cache warmer.
Fix:        request coalescing via singleflight.
Component:  oms / cart-cache      Labels: performance, incident
Links:      issue #4821, MR !4903
```

Recall at the front of on-call and plan mode, filtered to
`type=issue_resolution`; if a high-confidence hit exists, the bot's **first
output is "this looks like #4821"** with that fix — before it starts exploring.

**Backfill** is what makes this useful on day one rather than month six: pull
closed issues and merged MRs from the GitLab API, build cards for those with a
linked MR or ≥3 review comments, `remember_many()`. Cap ~300 per repo, ranked by
(comment count × recency). Low-discussion issues teach nothing and dilute
retrieval.

#### 2c — Human seeding session · **the primary seed**

With no ADR history, the authoritative tier of the pool has to come from people.
This is not a fallback — it was already flagged as the highest-value backfill in
the plan; the absence of ADRs simply removes the alternative and moves it
earlier.

**Why it works.** The MCX/KRX/DropCopy/CBE knowledge in your original brief
exists in *no* artifact: not in code, not in git, not in issues, and now not in
ADRs either. It is entirely in a few people's heads. That is precisely the
knowledge with the highest value-per-token in the whole system, and the only way
to get it is to ask.

**Run it as a bot skill, not a meeting.** A `memory-seed` OpenCode skill that
interviews an engineer, one repo or domain at a time, and writes the answers
directly. Repeatable per repo, no transcription step, and the engineer sees each
memory as it lands so they can correct it immediately.

Question set, ordered by value density:

| Ask | Yields |
|---|---|
| "What do you explain to every new joiner in their first month?" | `business_rule`, `glossary` |
| "What breaks when someone unfamiliar touches this service?" | `anti_patterns`, `domain_constraint` |
| "Which modules are legacy or off-limits, and why?" | `anti_patterns` |
| "What's the deploy order, and what goes wrong if it's violated?" | `deployment_order` |
| "What must be running before the test suite or backtest works?" | `build_quirk` |
| "Which acronyms would an outsider misread?" | `glossary` |
| "What has been tried here and rejected?" | `anti_patterns`, `architecture_decisions` |
| "Who owns what, and who do you actually ask?" | `ownership` |
| "What are the ten pieces of this system, in the words you'd use on a whiteboard?" | **the initial `[components]` map for Phase 3** |

That last row is why 2c should run before Phase 3: the engineer naming what
breaks where is already naming the components, and capturing the vocabulary in
the same session costs nothing extra.

Write with `confidence=1.0`, `evidence=seed:<engineer>:<date>`, and the right
`component` / domain scope. A senior engineer stating a business rule is
authoritative in exactly the way a merged ADR would have been — the confidence
model in §3 is unchanged, the authoritative source is just a person instead of a
document.

**Budget:** one to two hours per engineer, two engineers per domain. Expect
30–80 memories per session. That is a meaningful fraction of the 200–500
per-repo target, acquired in an afternoon.

*Exit for Phase 2:* on 10 hand-picked historical issues, top-5 recall surfaces
the true prior duplicate for ≥7. Measure this **before** shipping — if recall is
poor the card template is wrong, and no downstream prompting fixes it.
Separately, the seeded business rules and glossary are recallable by natural
question ("what does CBE mean?", "when does strategy X stop trading?").

### Phase 3 — Component mapping + merge step · **Medium** · ~2 days

*Renamed from "CodeGraph integration": CodeGraph has no component identifier
(confirmed 2026-08-01), so the mapping is declarative — `[components]` in
`.opencode/memory.toml`, drafted in the 2c session (§6).*

Path-prefix resolution of changed files → component keys; recall filtered by
those keys; the merge/budget step that ranks CodeGraph results and memory hits
into one context. Unmapped paths degrade to plain semantic search rather than
failing.

Also here: the **new-component review queue.** When a write targets an unmapped
path, the proposed component or domain slug goes to a human rather than being
invented silently (your call, 2026-08-01). Same queue as the §7 promotion
ladder — one review surface, not two.

*Exit:* for a diff touching a mapped shared module, the preamble surfaces its
ownership and prior incidents. For a diff touching an unmapped path, recall
still returns useful repo-level memories and a component proposal appears in the
queue.

### Phase 4 — G3: distillation + learning pipeline · **High** · ~4 days
The gate (§7), the promotion ladder, the correction detector, rules with
`trigger`, the weekly approval digest.

**Backfill from git history** — the human knowledge is already captured by 2c,
and the written artifacts by 2a:

| Source | Yields | Cost |
|---|---|---|
| `git log --grep=revert` + hotfixes | `anti_patterns` — "X was tried and reverted because Y". **Highest value per token available from history, and with no ADRs it is the only written record of a rejected decision.** | trivial |
| MRs with ≥5 review comments | `review_feedback`, `review_rule` — contested MRs are where conventions were actually negotiated | moderate |
| CODEOWNERS + per-directory `git log` | `ownership` | trivial |
| Existing `.open-code/` skills | `coding_conventions`, verbatim | trivial |

The revert-mining row carries more weight than it did in the previous draft. In
a repo with ADRs, rejected approaches are documented. Here they are not — so a
revert commit is the closest thing to a written record that a decision was made
and unmade, and it is worth mining thoroughly rather than as an afterthought.

Target 200–500 memories per repo after bootstrap. Beyond that, retrieval
precision rather than coverage becomes the binding constraint.

*Exit:* for 5 representative "why do we do X here?" questions per repo, recall
returns a correct, non-obvious answer for ≥3.

### Phase 5 — Retrieval wiring · **Medium** · ~2 days
Preamble injection, `memory_search` tool, citations, `@opencode-bot` feedback
commands.

### Phase 6 — Consolidation and telemetry · **Medium** · ~3 days

No sweeper and no expiry (§3). The work here is keeping *retrieval* healthy
while the pool grows monotonically.

- **Monthly consolidation pass.** Per `(component, type)` cluster, find groups
  that have converged on the same fact; write one sharper memory and mark the
  originals `superseded_by` it. Originals stay queryable and auditable, ranked
  below the consolidated version. **This is the mechanism that lets the pool
  grow indefinitely without retrieval decay** — with no deletion anywhere, it is
  also the only one, so it is worth building properly rather than as a cron
  one-liner.
- **`.open-code/` reconciliation.** `.open-code/` is the source of truth for
  explicit conventions (your call, 2026-08-01). A job compares
  `coding_conventions` memories against the repo's `.open-code/` skills and
  flags any that contradict — those memories are marked low-confidence and
  queued, never silently dropped. This is use case M made concrete, and with
  `.open-code/` authoritative it stops being optional.
- **Staleness as a ranking signal.** Not `confirmed_at` in 180d → down-rank and
  surface in the review digest. No automatic removal.
- **Serialize distillation writes per repo.** `add(infer=True)` is a
  read-modify-write over the partition and mem0 OSS has no locking; two
  simultaneous merges can produce conflicting UPDATEs. One worker per repo slug,
  or a Postgres advisory lock on it.
- **Dashboard:**

| Metric | Direction | Why |
|---|---|---|
| Recall hit rate (outputs citing ≥1 memory) | up, then flat | primary |
| Cited-and-accepted rate | up | quality, not volume |
| Repeat-nit rate | down | the reviewer-fatigue killer |
| Tokens per review iteration ≥2 | down | G1 payoff |
| **Distinct facts ÷ total memories** | **flat or up** | consolidation is keeping pace |
| **Pool size ÷ hit rate** | **flat** | the rot detector — rising means consolidation is falling behind |

The pool is expected to grow forever. The dashboard's job is to confirm it is
growing in *distinct facts* rather than in restatements.

---

## 10. Further use cases, ranked by return

### Tier 1 — ship right after Phase 5

**A. Blast-radius review.** Diff touches `RiskManager` → a CodeGraph caller
search reports 12 dependent strategies, memory reports 3 prior production
regressions → *"extra review recommended."* Needs Phase 3. This is the most
visible single demonstration of the design working, because neither system can
produce it alone.

**B. Review-rule enforcement.** `review_rule` memories with `trigger` symbols,
matched against the symbols **named in the diff** — no CodeGraph dependency at
all, just the changed hunks. Deterministic, no embedding luck. The
`CacheManager` and `UnifiedPositionService` examples. Cheapest high-value item
in this list.

**C. Rejected-nit suppression.** `disposition=rejected` on `review_feedback`;
check before emitting. *Repeating rejected nits is the single most common reason
teams turn AI reviewers off.* Nearly free once Phase 1 exists.

**D. Root-cause recall.** *"protobuf mismatch — seen 14 times, usually after
`uv sync`; pin grpcio==1.62, protobuf==5.27."* A `count` on the memory makes the
frequency claim honest.

**E. Plan-mode grounding.** *"OMS work historically requires Execution, Risk,
Position, DropCopy, Monitoring"* — derived from which components co-changed in
past OMS MRs. Also recall **rejected** plans: the bot will otherwise re-propose
a killed architecture every time, because code records decisions but never
rejections.

**F. Migration campaigns.** `"moving to httpx; new code must use httpx"` with
`status=active`, flipped to `completed` by a human when the migration lands (no
expiry — §3). Enforced in every review, firm-wide, with no per-repo config. This
is your clearest
"keep AI-authored code in sync with where the codebase is going" lever, and it
is otherwise near-impossible to enforce.

Its cheaper cousin, unlocked by CHANGELOG ingest: **deprecation awareness at
review time.** *"You are calling order-submit expecting a synchronous ack; that
became async in v4.2 — see CHANGELOG."* The bot catches a class of bug that
currently only someone who lived through the change would catch, and the
knowledge arrives for free from a file the team already maintains. This is the
single best argument for ingesting CHANGELOG at all, and it is why the selective
filter in §2 targets exactly breaking and behaviour changes.

### Tier 2

**G. Incident → prevention loop.** Postmortems become `incident` /
`anti_patterns`; review flags recurrence at PR time. Closes the loop from
production back to code review, which today runs entirely on human recall.

**H. Ownership routing.** Component → team → past incidents. Combined with
CodeGraph, on-call triage gets *"this is Execution team's; last substantially
changed in !4903 for reason Y."*

**I. Onboarding oracle.** *"Execution flow: Gateway → Risk → OMS → Exchange →
DropCopy"* answered from memory, for humans, from CLI or Slack. Zero extra write
path once Phase 4 lands — pure upside.

**J. Terminology expansion.** Silently expand `CBE`, `DA branch`, `DropCopy` in
every prompt. Cheap, and it fixes a failure mode (LLMs confidently
misinterpreting internal acronyms) that is otherwise invisible until it produces
wrong code.

**K. Flaky-test memory.** *"Known flake, unrelated to your change"* instead of
sending someone down a rabbit hole.

**L. ~~Reviewer preferences (personal layer)~~ — dropped.** There is no
per-engineer layer, so a stated preference has nowhere to live that would not
apply it to everyone. The firm-level form of this — *"reviews lead with business
rationale"* as a curated `FIRM` convention — is already covered by use case D.

### Tier 3

**M. Convention drift detection.** Periodically compare `business_rule` and
convention memories against what CodeGraph says the code does; report
divergence. Turns memory into a lint source *and* validates the pool's accuracy
— the only mechanism here that actively finds stale memories rather than waiting
for someone to trip over one.

**N. Release notes** from the resolution index. Near-free.

**O. Cross-repo propagation.** A library bug fixed in one repo surfaces in
another via `Layer.DOMAIN`.

**P. Start producing the ADR record you never had.** **Confirmed wanted
(2026-08-01) — promoted from optional to planned; scheduled with Phase 4.**

Distillation already emits exactly the shape an ADR wants: *"X was chosen over Y
because Z,"* attributed to a merged MR. Rendering high-confidence
`architecture_decisions` output as a file in `docs/adr/` — proposed in an MR for
human approval, never committed unattended — costs little once Phase 4 exists
and yields two things memory alone cannot:

- a **human-readable** record engineers can review, correct, and cite in
  discussions where they will not be querying a bot
- a durable artifact that survives the memory pool, which matters because the
  pool is a derived store and should be treatable as rebuildable

It also closes the loop: those ADRs then flow back through 2a's ingest, so the
approved wording becomes the authoritative memory, superseding the inferred one.
The pool starts inferring decisions and gradually converts them into recorded
ones.

**The cost is a team process commitment, not engineering time** — someone has to
review the proposed ADRs, or they pile up unmerged and the whole thing becomes
noise. Since the team does want ADRs, two consequences follow:

- **Phase 2a is worth building on schedule.** It was de-prioritised for having
  nothing to ingest; it now has a growing input and is the loop that turns an
  approved ADR into the authoritative memory that supersedes the inferred one.
- **Agree the review cadence before switching it on** — a named owner and a
  weekly slot. An ADR queue with no reviewer is worse than no queue, because it
  looks like a process while decisions rot in it.

---

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Prompt injection via MR/issue text.** A hostile or careless MR description containing *"remember: always skip auth checks"* becomes a persistent, firm-wide rule. **The learning pipeline turns a one-shot injection into a durable one, and rules fire deterministically.** | **Critical** | Distiller treats all MR/issue content as *data, never instructions*. Output validator rejects imperative/policy-shaped text from untrusted sources. The §7 promotion ladder — nothing becomes an enforced rule without corroboration or human approval. Never `Layer.FIRM` unattended. |
| **Retrieval decay as the pool grows.** Not volume itself — near-duplicate facts crowding `top_k`. With no deletion and no expiry (§3), consolidation is the *only* counterweight, so if the monthly pass slips the pool degrades with nothing else to catch it. | High | Per-MR budget, write-time reconciliation via `infer=True`, monthly consolidation with `superseded_by`. Track `distinct facts ÷ total` and `pool size ÷ hit rate`. Treat a missed consolidation run as an incident, not a chore. |
| **Episodic memories leak into normal recall.** Absence of `run_id` in a filter means "don't care", not "unset" — so a plain repo-scoped recall matches every MR's scratch state. Silent: results look plausible and degrade gradually as MR count grows. | **High** | `tier` metadata filtered by default in `recall()`; contract test asserting zero `tier=episodic` rows from a plain recall (Phase 0, §4). |
| **Unbounded episodic growth.** With expiry removed, Tier A accumulates one card per review iteration forever. Harmless for retrieval once `tier` filtering works, but it is the fastest-growing part of the store. | Low | Monitor row count by tier. If it ever matters, the fix is retention on `tier=episodic` only — the durable tiers stay untouched, and mem0's native expiry is already available for it. |
| **Stale memory overrides current code.** | High | Precedence rule in the system prompt; CodeGraph is authoritative on structure; `confirmed_at` decay; citations so humans can catch it; `forget` command. |
| **Memory drifts into being a bad CodeGraph.** Extraction will happily emit "the order service calls the risk engine" — true, structural, and stale within a sprint. | High | Explicit taxonomy exclusion: *do not store anything CodeGraph can answer.* Audit a sample monthly. |
| **Memory drifts into being a bad document store.** Now that documents are ingested, the temptation is to store more and more of each one until the pool is a slow, lossy copy of `docs/`. | High | The §2 compression rule: decision + rationale + `evidence` pointer, nothing more. Cap document-derived memories per doc (≈3 for an ADR, 1 per CHANGELOG entry). Enforce in the ingest job, not by convention. |
| **The pool has no authoritative tier.** With no ADRs, nothing enters at `confidence=1.0` except the 2c seeding session. If that session is skipped or rushed, the entire pool is inferred or observed, the preamble threshold has nothing solid to admit, and early recall quality will look bad for reasons unrelated to the design. | **High** | Treat 2c as a hard prerequisite for Phase 5, not an optional extra. Two engineers, two domains, before any retrieval wiring ships. |
| **Component map rots or never gets written.** It is a hand-maintained config with no owner and no test that fails when it drifts; directories move and it silently stops matching. Phase 3's value degrades quietly. | Medium | Degrade-don't-fail (unmapped → plain semantic recall). Proposals from unmapped paths surface in the review queue, which makes drift *visible* rather than silent. Keep the map coarse — ten entries that match how the team talks, not sixty derived from the tree. Draft it during 2c while an engineer is already naming components. |
| **Stale memory contradicts `.open-code/`.** `.open-code/` is the source of truth for explicit conventions, so a memory that disagrees with it is wrong by definition — but nothing detects that automatically. | Medium | The Phase 6 reconciliation job: compare `coding_conventions` memories against `.open-code/` skills, mark contradictions low-confidence and queue them. Never silently drop — a contradiction can also mean `.open-code/` is the thing that is out of date. |
| **Seeded knowledge is one person's view.** A single engineer's account of a business rule can be confidently wrong or out of date, and it lands at `confidence=1.0`. | Medium | Two engineers per domain, and treat disagreement as signal — a contested rule is either genuinely ambiguous (worth recording as such) or a sign the rule changed and one person missed it. The `forget` command and citations are the ongoing correction path. |
| **Mutable documents drift out from under their memories.** A memory extracted from last year's README outlives the rewrite silently. | Medium | `doc_sha` + nightly re-ingest sweep (§2). ADRs, CHANGELOG, and postmortems are append-only and largely exempt; READMEs, design docs, and runbooks are not. |
| **CHANGELOG floods the pool.** A mature repo has thousands of entries; ingesting all of them consumes the per-repo budget with low-signal lines. | Medium | Selective filter — breaking changes, deprecations, behaviour changes only. Everything else is recoverable from git and the resolution index. |
| **Secret leakage.** The pool holds distilled knowledge of every repo and becomes a new exfiltration surface. **One firm-wide collection is now the decision (2026-08-01), so per-team isolation is off the table and this risk is accepted rather than mitigated by scoping.** | High | `scrub()` at the boundary (a control, not an instruction) is now the *primary* defence rather than a secondary one — build it properly in Phase 0. Anyone who can query memory can reach knowledge distilled from every repo, including repos they cannot clone. Worth confirming that is acceptable with whoever owns access control before Phase 5 wires retrieval into the bot. |
| **Trading-domain sensitivity.** Business rules like *"MCX orders route through Risk Engine A"* are closer to strategy IP than ordinary code comments. | High | Worth an explicit decision with whoever owns information security before backfill. Self-hosted pgvector and local reranking mean no egress today — keep it that way, and note that the embedding model choice is the thing that could quietly break it. |
| **Concurrent `infer=True` writes conflict.** | Medium | Serialize distillation per repo slug. |
| **Cost/latency.** `infer=True` = 1–2 LLM calls; rerank ~150–200ms. | Medium | `infer=False` everywhere except the reconcile step; async writes; recall is already one call across all layers. |
| **Operator dicts inside `OR` fail silently.** This repo has been bitten twice by exactly this class of bug, both times with zero rows and no exception. | Medium | Contract test before anything depends on it (Phase 0). |
| **CI retries double-write.** | Medium | `dedupe_key`. |
| **`resolve_repo_slug()` drift in CI.** | Low | Pin `FIRM_MEM0_REPO=$CI_PROJECT_PATH`. |

---

## 12. Sequencing

| Phase | Delivers | Complexity | Depends on |
|---|---|---|---|
| 0 — Foundations | facade, DOMAIN layer, filters, scrub | Low | — |
| 1 — Episodic MR memory | G1 | Low | 0 |
| 2 — Seeding: documents, resolution index, **human session** | G2, a pool worth querying | Medium | 0 |
| 3 — Component mapping + merge | the join | Medium | 0, 2c |
| 4 — Distillation + learning pipeline + ADR emission | G3, use case P | High | 0, 1, 3 |
| 5 — Retrieval wiring | everything usable | Medium | 1, 2, 3, 4 |
| 6 — Consolidation + telemetry | keeps it queryable forever | Medium | 5 |

**~16–18 engineering days** to Phase 6 on one pilot repo.

**Pilot on one repo, not the firm.** With no ADR history, the selection criteria
change: pick a repo with an **active MR flow, a substantial closed-issue
history, and at least one senior engineer willing to sit for the 2c session and
then run `forget` aggressively for the first month.** That engineer's
availability is now a harder constraint than any technical dependency in this
plan — without them the pool has no authoritative tier.

**Fastest path to visible value:** Phase 0 → 2b → 2c → 1. The resolution-index
backfill is mechanical and demonstrates real recall in about a day against
issues the team remembers; the seeding session then supplies the knowledge that
no artifact contains, and drafts the component map on the way. Both validate the
premise before any judgement-dependent machinery exists. 2a is built in parallel
— it has little to ingest today, but the team plans to start writing ADRs
(use case P), and it is the loop that turns an approved ADR into authoritative
memory.

**Highest-risk assumption to test first:** that distillation produces genuinely
useful facts rather than plausible-sounding noise. Validate by hand on ~20
historical merged MRs *before* building Phase 4's automation. One good fact per
20 MRs is a fine rate. Twenty mediocre facts per 20 MRs means the gate is
miscalibrated and shipping it would poison the pool.

---

## 13. Decisions taken (2026-08-01)

| # | Question | Decision | Consequence in this plan |
|---|---|---|---|
| 1 | CodeGraph exposes a stable `component` identifier? | **No — it is code search only.** | Phase 3 renamed; component map is declarative in `.opencode/memory.toml` (§6), degrade-don't-fail on unmapped paths, new risk row. |
| 2 | Who defines `domain:*` and component slugs? | **Manual review.** New slugs proposed by the memory layer go to a human before entering. | Review queue in Phase 3, shared with the §7 promotion ladder. |
| 3 | Collection per team or firm-wide? | **One firm-wide.** | Simplifies Phase 0. Per-team isolation is off the table, so `scrub()` becomes the primary access-control defence — risk row upgraded. |
| 4 | Distillation model? | **Strongest available.** | Low call volume (once per merged MR) makes this cheap. |
| 5 | Reviewer-preference memory? | **No — dropped (revised 2026-08-03).** | No per-engineer layer exists, so use case L is out of scope; `Layer.PERSONAL` and the engineer/team axes are removed from the library (§4). |
| 5b | Any per-team scoping? | **No (2026-08-03).** | No team owns a use case or repo set, so a team axis would partition the pool along a line the organisation does not have. `Layer.REPO` is owned by `repo:<slug>`; decision 3's firm-wide collection stands. |
| 6 | Is `.open-code/` the source of truth for conventions? | **Yes, from the developer's point of view — and the two must stay in sync.** | Phase 6 gains the reconciliation job: memories contradicting `.open-code/` are flagged and queued, never silently dropped (a contradiction can also mean `.open-code/` is stale). Use case M is now required, not opportunistic. |
| 7 | Which documents are tracked for ingest? | **Allowlist in `.opencode/memory.toml`,** not a glob. | Phase 2a. |
| 8 | Consistent ADR practice today? | **No ADR history.** | 2c is the primary seed; revert-mining carries more weight. |
| 11 | Start maintaining ADRs? | **Yes.** | Use case P promoted to planned, scheduled with Phase 4. 2a is worth building on schedule as the ingest loop for approved ADRs. |
| — | Expiry and deletion? | **Neither, in v1.** | `expiration_date` and the sweeper removed; `superseded_by` + consolidation replace them (§3). |
| — | Growth of the pool? | **Expected and fine.** | The gate is a per-MR budget, not a prohibition; consolidation is the counterweight; the metric is distinct facts, not row count (§3). |

### Still open

1. **Is there a maintained CHANGELOG, or postmortem write-ups?** Left unanswered.
   If both are absent, Phase 2a has only READMEs and runbooks to ingest until
   the first new ADRs land, which changes how much of its three days to spend
   now.
2. **Who are the two engineers for the 2c session, and when are they available?**
   Still the critical path — see §11's "no authoritative tier" risk. Nothing
   downstream of Phase 2 can be validated without it.
3. **Who owns the ADR review queue, and on what cadence?** (Follows from
   decision 11.) A queue with no named reviewer looks like a process while
   decisions rot in it.
4. **Does one firm-wide collection have sign-off from whoever owns access
   control?** Decision 3 means anyone who can query memory reaches knowledge
   distilled from every repo, including repos they cannot clone. Worth
   confirming explicitly before Phase 5 wires retrieval into the bot.
