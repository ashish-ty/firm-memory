# Trying it out

## `try_extraction.py`

Runs the ingestion pipeline end to end and prints what it does at each step:

```text
raw material ─► extract ─► candidates ─► human approval ─► searchable
```

Four sample sources in `sample_sources.py`, chosen so the output is meaningful:
a design discussion with real rationale, an incident write-up, a routine
"nit / LGTM" thread with nothing durable in it, and a noisy thread full of
exactly what the taxonomy excludes — a stack trace, transient state, and one
personal preference.

A working extractor should return several facts from the first two, **nothing**
from the third, and be caught by the taxonomy on the fourth.

### Three modes

| Mode | Needs | What runs |
| --- | --- | --- |
| `offline` *(default)* | nothing | A canned extractor. Shows the flow and the approval gate without spending a token. |
| `llm` | an LLM key | The platform's own extractor against a real model. Memories held in process unless `FIRM_MEM0_PG_DSN` is set. |
| `provider` | an LLM key + pgvector | mem0's own extractor (its read-only phases) against your real pool, so its deduplication has something to work against. |
| `compare` | both | Runs `llm` and `provider` over the same sources and prints what each returned. Skips whichever is not configured rather than failing. |

### Start here — no credentials

```bash
python examples/try_extraction.py --mode offline --approve
```

You should see five candidates queued, all three searches return **zero** hits
before approval, and the same searches return cited memories after it. That zero
is the guarantee: extraction does not write.

### Against a real model

```bash
export OPENROUTER_API_KEY='sk-or-...'
python examples/try_extraction.py --mode llm \
    --model openrouter/anthropic/claude-3.5-sonnet --approve
```

An OpenRouter key covers the LLM. It does **not** cover embeddings — OpenRouter
has no embeddings endpoint — so this mode keeps memories in process. For a real
pool, add pgvector and a local embedder:

```bash
export FIRM_MEM0_PG_DSN='postgresql://mem0:pw@localhost:5432/mem0'
export FIRM_MEM0_LLM_PROVIDER=litellm
export FIRM_MEM0_LLM_MODEL='openrouter/anthropic/claude-3.5-sonnet'
export FIRM_MEM0_EMBEDDER_PROVIDER=huggingface
export FIRM_MEM0_EMBEDDER_MODEL='BAAI/bge-small-en-v1.5'
export FIRM_MEM0_EMBEDDING_DIMS=384      # must match the model

python examples/try_extraction.py --mode compare \
    --model openrouter/anthropic/claude-3.5-sonnet
```

`FIRM_MEM0_EMBEDDING_DIMS` must match the embedding model — pgvector fixes the
column width, so changing it later means a new collection, not a migration.

### What to look for in `--mode compare`

The open question is which extractor suits your material. mem0's is
deduplication-aware, which the platform's is not; the platform's prompt is
written for engineering memory, while mem0's is written for a consumer
assistant and its few-shot examples pull toward personal-profile facts.

Judge them on: does the routine thread correctly yield nothing? Does the noisy
thread's personal preference get dropped? Are the facts self-contained enough to
be useful months later, to someone who never read the source?

### Useful flags

```
--approve            approve every candidate and search again
--in-memory          never touch pgvector, even if a DSN is set
--min-score 0.3      apply a relevance floor (default 0 shows everything)
--verbose            debug logging, including what gets dropped and why
```
