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

One install:

```bash
pip install -e '.[demo]'
```

That is `mem0ai`, `psycopg`, `litellm` and `fastembed` — everything this script
touches. Composing the narrower extras yourself is easy to get wrong; the script
checks up front and tells you exactly what is missing and which interpreter it
is running as.

Then two credentials. Put them in a `.env` at the repo root — it is gitignored,
and `.env.example` is there to copy:

```bash
cp .env.example .env
$EDITOR .env
```

```ini
OPENROUTER_API_KEY=sk-or-...
FIRM_MEM0_PG_DSN=postgresql://mem0:pw@localhost:5432/mem0
```

Exported shell variables override the file. **Do not hardcode a key into the
script** — this repository is public.

The script defaults everything else, so it works as-is:

| | Default | Why |
| --- | --- | --- |
| LLM | `openrouter/anthropic/claude-3.5-sonnet` via litellm | Your OpenRouter key |
| Embedder | `BAAI/bge-small-en-v1.5` via fastembed (ONNX, local) | **OpenRouter has no embeddings endpoint.** Local also keeps memory content off the network, and fastembed avoids pulling in torch |
| Dimensions | `384` | Must match the embedding model — pgvector fixes the column width at creation |
| Reranker | off | It needs `sentence-transformers` (and torch). Turn it on for real retrieval work: `pip install -e '.[rerank]'` and `export FIRM_MEM0_RERANK=on` |
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
