# Plan: Tracing and Memory Evaluation for the OpenCode Bot

Companion to `PLAN_OPENCODE_MEMORY.md`. Two asks:

1. OpenTelemetry tracing so the flow through the three memory tiers is visible.
2. An evaluation framework for *how beneficial the retrieved memory actually was*,
   so the retrieval window can be tuned against evidence.

They are not two projects. **The instrumentation that produces a readable trace is
the same instrumentation that produces the eval dataset.** Build it once.

---

## 1. The framing that decides the design

The obvious move is to reach for a RAG eval library and score retrieval relevance.
That answers the wrong question.

> Relevance asks: *was this memory related to the query?*
> Benefit asks: *did injecting it change the output, for the better?*

A memory can be perfectly relevant and change nothing — the model already knew it,
or it never reached the output. Conversely a memory can score poorly on relevance
and still be the thing that stops a repeat-nit. So relevance metrics are a cheap
**leading indicator**, not the answer. The answer is a counterfactual: run the same
MR with memory on and off, and compare.

That splits the eval into three levels with very different cost profiles:

| Level | Question | Cost | Cadence |
|---|---|---|---|
| **L1 — Retrieval** | Did recall return the right memory ids? | cheap, mostly no LLM | every commit (CI gate) |
| **L2 — Utilization** | Of what was retrieved, what got injected, cited, accepted? | free (derived from traces) | continuous, production |
| **L3 — Outcome** | Did memory-on beat memory-off on real MRs? | expensive (N × 2 LLM runs + judge) | weekly / per config change |

L2 is the one people skip and it is the highest value per unit of effort, because it
costs nothing beyond getting the span attributes right and it is measured on real
traffic rather than a frozen corpus.

---

## 2. Framework recommendation

Both halves are covered by one tool. **Arize Phoenix, self-hosted**, with Ragas for
the L1 metric implementations.

Why Phoenix over Langfuse for this specific case:

- **OpenTelemetry-native, and it owns the OpenInference semantic conventions.**
  OpenInference has a first-class `RETRIEVER` span kind that renders retrieved
  documents as a ranked list with scores and metadata. Memory recall *is* a
  retrieval step; you get the most useful debugging view for free rather than
  hand-rolling it out of generic span attributes.
- **Self-host footprint matches the pilot.** One container plus Postgres — which
  is already running for pgvector. Langfuse wants Postgres + ClickHouse + Redis +
  S3-compatible object storage; that is a reasonable platform at scale and
  disproportionate for one pilot repo.
- **`phoenix.experiments` is the L3 harness.** Datasets + task function +
  evaluator functions + a side-by-side comparison UI. That is exactly the replay
  A/B described in §5, without building a bespoke runner.
- **Embedding projection view.** UMAP over the memory pool is a real pool-rot
  diagnostic — dense clusters of near-duplicates are visible at a glance, which
  is the failure mode `PLAN_OPENCODE_MEMORY.md` §7 is most worried about.

**The one thing to check before committing:** Phoenix ships under the Elastic
License 2.0 — source-available, *not* OSI-approved open source. Langfuse's core is
MIT. At a quant firm that is a procurement/legal question, not a technical one, and
it should be answered before day 1 rather than after the pilot. If the answer is
"must be OSI-licensed", switch to Langfuse and **nothing else in this plan
changes**, because of the next point.

### The portability rule (non-negotiable)

`firm_mem0` imports `opentelemetry-api` and nothing else. No Phoenix SDK, no
Langfuse SDK, ever, in library code. Spans are emitted to OTLP using OpenInference
attribute names; the backend is a URL in an env var. This keeps the vendor decision
reversible and keeps the library dependency-light, which matters because it is
imported into every bot invocation in CI.

Also note: as far as I can verify there is **no first-party OpenInference
auto-instrumentor for mem0**. That is fine and arguably better — the interesting
spans are at the *facade* level (layer, tier, namespace, policy), which only
`firm_mem0` knows about. mem0's internals are not the thing you want to look at.
Do turn on the auto-instrumentor for whichever LLM client the distiller uses
(OpenAI / Anthropic / litellm) so token and cost accounting come for free.

---

## 3. Tracing design

### 3.1 Where the code goes

Two layers, cleanly separated:

| Layer | Owns | New module |
|---|---|---|
| `firm_mem0` (library) | Spans around `remember` / `recall` / `forget` / `timeline` / `sweep`. API-only, no-op when no SDK is configured. | `src/firm_mem0/tracing.py` |
| OpenCode bot (application) | SDK setup, OTLP exporter, resource attributes, flush-on-exit. | bot repo |

The library never configures a TracerProvider. If the bot doesn't set one up, every
span is a no-op with effectively zero cost — this is the standard library-hygiene
rule and it means `firm_mem0` stays usable in tests and scripts with no telemetry
stack present.

### 3.2 The span tree

This is the "how is it flowing" picture. One MR review iteration:

```
mr-review                       CHAIN     session.id=mr-4821, mr.iteration=3
├── memory.timeline             RETRIEVER tier=A  → n_cards, tokens, last_sha
├── memory.recall               RETRIEVER tier=B|C, layers, policy_hash
│   ├── mem0.search                       embed + pgvector
│   └── rerank                            cross-encoder, latency
├── prompt.assemble             CHAIN     tokens by section ← the G1 metric
├── llm.review                  LLM       usage, cost (auto-instrumented)
├── review.emit                 CHAIN     findings[], citations[memory_id]
└── memory.write                CHAIN     tier=A, dedupe_key, infer=False
```

Post-merge distillation is a separate trace on the same session:

```
memory.distill                  CHAIN     session.id=mr-4821
├── timeline.load               RETRIEVER
├── llm.distill                 LLM       → candidates[], confidences[]
├── gate.filter                 CHAIN     n_in, n_dropped, drop_reasons[]
├── memory.recall               RETRIEVER dedupe probe, max_similarity
└── memory.write                CHAIN     tier=B, infer=True|False
```

### 3.3 Span attributes that matter

Getting these right is the whole job — every eval metric in §4 and §5 is an
aggregation over them. Missing one means re-instrumenting later and losing the
historical series.

**On `memory.recall`:**

| Attribute | Why |
|---|---|
| `retrieval.documents.N.document.{id,score,content}` | OpenInference convention → ranked-list UI |
| `...document.metadata.{type,layer,age_days,confirmed_at}` | lets you ask "are we only ever citing memories <30d old?" |
| `memory.policy_hash` | the retrieval window that produced this result (§5.3) |
| `memory.n_retrieved`, `memory.n_above_threshold` | threshold calibration |

**On `prompt.assemble`:** `tokens.diff`, `tokens.timeline`, `tokens.memory`,
`tokens.skills`, `tokens.total`. This is how the G1 exit criterion ("iteration 4
sends <40% of the tokens") becomes a measurement instead of an assertion.

**On `review.emit`:** `memory.citations` (list of ids), `findings.fingerprints`
(list of stable hashes), `findings.count`.

`memory.citations` is the linchpit. The citation contract in
`PLAN_OPENCODE_MEMORY.md` §4 was specified for auditability — it is *also* the only
signal that distinguishes "retrieved" from "actually used". Emit it as a span
attribute, not just in the MR comment text.

### 3.4 Two CI-specific gotchas

1. **Flush before exit.** Every bot run is a short-lived process that GitLab kills
   the instant it returns. `BatchSpanProcessor` will drop everything it hasn't
   shipped. Wrap the run in a `finally: provider.force_flush(timeout_millis=...)`
   with a bounded timeout so a slow collector can't hang the job. This is the
   single most common reason "we added OTel and see nothing".
2. **Iterations are separate traces.** Each CI job is its own process and its own
   trace. Group them with `session.id = mr-<iid>` — Phoenix and Langfuse both
   treat session as first-class, giving you the "how did this MR evolve across 4
   iterations" view, which is precisely Tier A's story. Resource attributes:
   `service.name=opencode-bot`, `firm.repo`, `ci.pipeline.id`, `ci.job.id`.

### 3.5 Telemetry is a second exfiltration surface

`PLAN_OPENCODE_MEMORY.md` §7 treats the memory pool as an exfiltration surface. The
trace backend is a **second copy of the same content** in a different datastore
with, most likely, a different access model. Two controls:

- Run `scrub()` (Phase 0) over span content attributes, not just over writes to
  mem0. Same function, applied at the export boundary.
- `FIRM_MEM0_TRACE_CONTENT=0` kill switch: emit ids, scores, counts, token
  numbers — never memory text or diff text. All L2 metrics still work without
  content; only the human debugging view degrades. Default this to `0` for any
  repo that isn't the pilot.

---

## 4. Evaluation: L1 and L2

### 4.1 L1 — retrieval quality (offline, CI-gated)

**Golden set.** The Phase 2 backfill hands this to you nearly free: GitLab already
records which issue duplicates which, and which MR closed which issue. Build
`evals/golden/retrieval.jsonl` as `{query, relevant_memory_ids[], notes}` from
those links. Target 50–100 cases for the pilot repo; below ~30 the metrics are too
noisy to gate on.

Metrics — deterministic, no LLM, sub-second, runs in CI:

- `recall@k` for k ∈ {3, 5, 10} — primary; this is Phase 2's stated exit criterion
  (`≥7 of 10`) generalized and made repeatable.
- `MRR` — how far down the list the right answer sits.
- `nDCG@k` — with graded relevance where the golden set supports it.
- `precision@k` — the dilution guard as the pool grows.

Add reference-free judge metrics from **Ragas** (`context_precision`,
`context_relevance`) for queries with no labelled ground truth. These need an LLM
judge — see §6 on pinning the judge.

**Gate:** a PR that drops `recall@5` on the golden set by more than a small margin
fails CI. This is what stops a "harmless" retrieval-config tweak from silently
degrading the system.

### 4.2 L2 — utilization (online, derived from production traces)

No labels, no corpus, no extra LLM calls. Pure aggregation over §3.3 attributes.
This is the funnel:

```
retrieved → above threshold → injected → cited → accepted
```

Each arrow is a drop-off you can measure and act on:

| Metric | Definition | What a bad value tells you |
|---|---|---|
| **Injection rate** | % runs with ≥1 memory above threshold | low → threshold too high, or pool doesn't cover this repo |
| **Citation rate** | % runs citing ≥1 injected memory | low → memory is reaching the prompt and being ignored; a prompt problem, not a retrieval problem |
| **Memory yield** | cited ÷ retrieved | **the window-tuning signal.** Low yield = `top_k` too high or threshold too low; you're paying tokens for memories the model never uses |
| **Acceptance rate** | cited and the developer didn't push back | quality, not volume |
| **Token cost per cited memory** | `tokens.memory` ÷ citations | the efficiency number that justifies the whole system |
| **Staleness of cited set** | median `age_days` of cited memories | rising with no new citations → pool is calcifying |

Memory yield deserves emphasis because it is directly what was asked for. If you
retrieve 8 and cite 1, you are spending 8× the tokens for 1× the value, and the fix
is `top_k=4` — but you cannot know that without the citation attribute.

### 4.3 Mapping onto the Phase 5 dashboard

`PLAN_OPENCODE_MEMORY.md` §5 already specifies the operating metric set. Every row
falls out of the instrumentation above — do **not** build a bespoke dashboard:

| Phase 5 metric | Source |
|---|---|
| Recall hit rate | traces with `memory.citations` non-empty ÷ all traces |
| Cited-and-accepted rate | citations + feedback-command ingestion as span events |
| Repeat-nit rate | `findings.fingerprints` on iteration *n* ∩ resolved fingerprints in Tier A timeline |
| Tokens per iteration ≥2 | `tokens.total` filtered on `mr.iteration >= 2` |
| Pool size ÷ hit rate | needs a **metric**, not a trace — see below |

Pool size is a gauge, not a span. Emit it from the nightly sweeper via the OTel
*metrics* SDK to Prometheus, or just write it to a table. Don't try to force it
into the trace backend.

---

## 5. Evaluation: L3 — did memory actually help?

The counterfactual. This is the part that answers the question as asked.

### 5.1 Replay corpus

30–50 merged MRs from the pilot repo, frozen, with their real human review comments
as the reference. Selection matters: stratify deliberately rather than sampling
uniformly — include MRs with heavy review discussion (where memory should help),
routine MRs (where it should do nothing and must not *hurt*), and MRs touching
subsystems with known prior incidents (the G2 case). A uniform sample is mostly
routine MRs and will show no effect either way.

Freeze the memory pool state to a snapshot per eval run, otherwise you cannot tell
a retrieval change from a pool change.

### 5.2 Arms and judging

Run each MR under several arms:

- **A** — memory off (baseline)
- **B** — memory on, current policy
- **C…** — memory on, policy variants (the window sweep, §5.3)

Judge with a **pairwise** LLM judge against the human review, not absolute scoring
— pairwise preference is markedly more stable. Randomize presentation order to
cancel position bias, and run each pair in both orders if budget allows.

| Metric | Type | Why it's here |
|---|---|---|
| **Win rate B vs A** | pairwise judge | the headline "was memory beneficial" number |
| **Repeat-nit rate** | deterministic (fingerprints) | the reviewer-fatigue killer; needs no judge |
| **Novel true findings** | judge vs human reference | what only the memory arm caught |
| **False-positive rate** | judge + code check | **memory-induced hallucination — the downside risk.** A win-rate improvement bought with more false positives is not a win |
| **Tokens per run** | deterministic | the G1 payoff, and the cost side of the ledger |

Report paired-bootstrap confidence intervals, not a t-test on unpaired means. Be
honest that N≈40 detects a large effect (~15pp win-rate delta) and nothing subtle.
That is still enough to answer "is this worth keeping".

### 5.3 Retrieval policy as data — the window sweep

Right now the retrieval window is scattered across call sites as `top_k=` and
`threshold=` arguments. Lift it into a frozen dataclass:

```python
@dataclass(frozen=True)
class RetrievalPolicy:
    top_k: int
    threshold: float
    layers: tuple[Layer, ...]
    type_filters: tuple[str, ...]
    rerank: bool
    recency_halflife_days: float | None
    max_memory_tokens: int
```

Stamp `policy_hash` on every `memory.recall` span and tag every experiment run with
it. Then "improve the window" becomes a parameter sweep over the replay corpus with
a comparison table, rather than a vibe. This is a small refactor with a large
payoff, and it fits the existing immutability convention (`for_task()` returns a new
facade; `with_policy()` should too).

Sweep `top_k` and `threshold` first — they dominate. `recency_halflife` matters
only once the pool is old enough for staleness to bite; defer it.

### 5.4 Attribution back to individual memories, and the curation loop

Because citations are recorded, you can score memories individually: times
retrieved, times cited, win rate of runs that cited them. That produces a
memory-level leaderboard — and the bottom of that leaderboard is a **prune list**.

This is the strongest structural idea in the plan: *the eval framework becomes the
curation policy*. A memory retrieved 40 times and never cited in 90 days is pool rot
by definition, and the Phase 5 sweeper can act on that automatically instead of
relying on staleness heuristics. It closes the loop from `PLAN_OPENCODE_MEMORY.md`
§5 that `confirmed_at` decay only approximates.

### 5.5 It doubles as the distillation validation rig

`PLAN_OPENCODE_MEMORY.md` §8 names the highest-risk assumption: *that distillation
produces useful facts rather than plausible noise*, to be validated manually on ~20
historical MRs before building Phase 3. The L3 harness is that rig. Build it before
Phase 3 and the validation is a scripted experiment with a number attached, not a
manual read-through — and it stays runnable afterwards as a regression gate on the
distiller prompt.

---

## 6. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Judge model drift silently reinvalidates history.** Upgrade the judge, every historical score shifts, and trend lines become meaningless. | **High** | Pin judge model + prompt version; store both with every score. Re-run a fixed calibration subset when either changes and publish the delta. |
| **Optimizing the proxy.** Citation rate is trivially gamed by prompting the model to cite more. | **High** | Citation rate is never a target on its own — always paired with acceptance rate and false-positive rate. L3 win rate is the only headline metric. |
| **Trace content leaks firm knowledge into a second store.** | **High** | §3.5 — `scrub()` at export, `FIRM_MEM0_TRACE_CONTENT=0` default off-pilot. |
| **Chicken-and-egg: need a pool to eval, need eval to build a pool.** | Medium | Bootstrap on the Phase 2 backfill, which is deterministic and involves no distillation judgement. Evaluate retrieval on that first; let L3 gate whether distillation is allowed to write at all. |
| **Instrumentation latency in the review hot path.** | Medium | `BatchSpanProcessor`, never `SimpleSpanProcessor`. Bounded `force_flush` timeout. Sampling stays at 100% — volume is per-MR, not per-request. |
| **Small-N eval overclaims.** | Medium | Paired bootstrap CIs; report N; refuse to act on differences inside the interval. |
| **Golden set rots** as the codebase moves. | Low | Timestamp cases; re-derive from GitLab links quarterly. |
| **Phoenix licence (ELv2) fails firm review.** | Low, but blocking if it lands | Answer it in week 1. OTLP + OpenInference means the swap to Langfuse is a config change. |

---

## 7. Phasing

| Phase | Delivers | Effort | Depends on |
|---|---|---|---|
| **O0** — OTel scaffolding | `tracing.py` (api-only, no-op safe), Phoenix container, bot-side SDK + flush | ~1d | — |
| **O1** — Span the flow | full span tree, session grouping, citation + token attributes | ~1–2d | O0, memory Phase 1 |
| **E1** — L1 retrieval eval | golden set, recall@k / MRR / nDCG, CI gate, Ragas metrics | ~2d | memory Phase 2 (backfill) |
| **E2** — L2 utilization | funnel metrics + Phase 5 dashboard from traces | ~1d | O1 |
| **E3** — L3 counterfactual | replay corpus, arms, pairwise judge, experiment runner | ~3d | O1, E1 |
| **E4** — Window sweep + curation loop | `RetrievalPolicy`, parameter sweep, memory-level attribution → sweeper | ~2d | E3 |

Roughly **9–11 days**, overlapping the existing 10–12.

**Sequencing against `PLAN_OPENCODE_MEMORY.md`:** O0 and O1 should land *before*
memory Phases 1–3, not after. Every exit criterion in that plan ("<40% of the
tokens", "≥7 of 10 recall", "correct answer for ≥3 of 5") is currently unmeasurable
— the instrumentation is what turns them from intentions into gates. E3 should land
before Phase 3, because it is the rig for the distillation-yield validation that
plan already calls the highest-risk assumption.

The cheap early win is **E2**: once O1 is in, the utilization funnel costs a day and
immediately tells you whether the retrieval window is wrong, on real traffic, with
no corpus to build.

---

## 8. Open questions

1. **Licence constraint.** Does ELv2 clear firm review? Decides Phoenix vs
   Langfuse. Nothing else depends on it. *Answer in week 1.*
2. **Judge model.** Strongest available, or a cheaper one? L3 volume is ~40 MRs ×
   arms × runs — low enough that the strongest model is affordable, and judge
   quality caps the credibility of every number the framework produces.
   Recommend the strongest, same as the distiller decision.
3. **Where does the trace backend live** relative to the pgvector store — same
   network segment, same access control? This is the §3.5 question in
   infrastructure form and it decides the default for `FIRM_MEM0_TRACE_CONTENT`.
4. **Replay corpus refresh cadence.** A frozen corpus goes stale as the codebase
   moves; refreshing it breaks comparability with earlier runs. Suggest a stable
   core plus a rotating tail, reported separately.
