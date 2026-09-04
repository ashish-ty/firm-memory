# `review_comment.py`

Takes one PR review comment, extracts the durable facts from it, and asks you
which to keep.

```text
review comment ─► mem0 extracts facts ─► you approve ─► searchable
```

Nothing is stored until you approve it. mem0's extractor runs read-only — its
retrieve-and-extract phases, not its write — so an unapproved run leaves the
pool exactly as it was.

## Setup

Two variables:

```bash
export OPENROUTER_API_KEY='sk-or-...'
export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'
```

```bash
pip install -e '.[mem0,pgvector,extract]'
```

The script sets everything else as defaults, so it works as-is:

| | Default | Why |
| --- | --- | --- |
| LLM | `openrouter/anthropic/claude-3.5-sonnet` via litellm | Your OpenRouter key |
| Embedder | `BAAI/bge-small-en-v1.5`, local | **OpenRouter has no embeddings endpoint.** Running it locally also keeps memory content off the network |
| Dimensions | `384` | Must match the embedding model — pgvector fixes the column width at creation |
| Store | pgvector | Your DSN |

Any of these you set yourself wins; the script only fills gaps.

## Run

```bash
python examples/review_comment.py                       # built-in sample comment
python examples/review_comment.py --comment "..."        # your own
pbpaste | python examples/review_comment.py              # from the clipboard
```

Options: `--repo` and `--domain` set the scope the facts inherit (default
`oms` / `execution`), `--reference` records which MR they came from, and
`--yes` approves everything without asking.

## What it looks like

```text
3 candidate fact(s) — none stored yet:

  1. [architecture_decisions]  confidence 0.90
     The cut-off check belongs in OrderRouter, not the strategy, because
     DropCopy reconciles against what OrderRouter emitted.

  2. [anti_patterns]  confidence 0.88
     Queueing after-cut-off orders for the next session was tried in March and
     reverted: it caused duplicate fills.

  3. [business_rules]  confidence 0.95
     MCX rejects cash-strategy orders sent after 15:20.

Approve which? [all / none / e.g. 1,3] (1-3):
```

Approve, and those facts are retrievable with a citation back to the MR.

## If it extracts nothing

That is a normal outcome, not a failure — most review comments contain no
durable knowledge. Worth watching for the opposite problem: mem0's extraction
prompt is written for a consumer assistant, so it can return personal facts
("the reviewer prefers tabs"). Those fit no category in the firm taxonomy and
are dropped before they reach you. If you see them getting through, switch to
the platform's own extractor with `FIRM_MEMORY_EXTRACTOR=llm`.
