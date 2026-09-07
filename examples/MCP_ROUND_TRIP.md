# The round trip, on a new machine

An agent hands over a merge request discussion over MCP; a person approves what
was distilled from it; the approved facts become searchable. Two scripts, one
for each half:

```text
examples/ingest_via_mcp.py  ─►  candidate queue  ─►  examples/run_review_ui.py
        (the agent)              (Postgres)              (the human)
```

Every step below was run from a fresh copy, a fresh virtualenv and an empty
database, with nothing configured but the two credentials.

---

## 0. What the machine needs first

| | Why |
| --- | --- |
| **Python 3.11+** | `uv` will fetch one if the system Python is older. |
| **[uv](https://docs.astral.sh/uv/)** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Docker, *running*** | For the pgvector database. Installed is not enough — start Docker Desktop and check `docker info` succeeds. |
| **An OpenRouter key** | Extraction calls a model. |
| **Outbound network, once** | The embedder downloads a ~130 MB ONNX model on first use, then runs locally forever after. Plan for this if the target host has no egress: warm the cache somewhere that does and copy `~/.cache/huggingface`. |

---

## 1. Install

```bash
git clone <this repo> && cd firm-mem0
uv sync --extra mcp
```

**`--extra mcp` is not optional here.** A bare `uv sync` gives you the library
and the review UI but not the MCP server, and `firm-memory-mcp` will not exist.

## 2. Credentials

```bash
cp .env.example .env
$EDITOR .env
```

Two lines matter:

```ini
OPENROUTER_API_KEY=sk-or-...
FIRM_MEM0_PG_DSN=postgresql://mem0:pw@localhost:5432/mem0
```

`.env` is gitignored. Exported shell variables override it.

## 3. The database

```bash
docker compose -f examples/docker-compose.yml up -d
```

That matches the default DSN. mem0 creates the `vector` extension and its table
on first use, and the candidate queue creates its own table the same way, so
there is no migration step.

To use a Postgres you already run, it needs pgvector *available*:

```sql
SELECT 1 FROM pg_available_extensions WHERE name = 'vector';
```

## 4. Ingest, as an agent would

```bash
uv run python examples/ingest_via_mcp.py
```

This spawns a real `firm-memory-mcp` subprocess and speaks MCP to it over stdio
— not an in-process shortcut — so it exercises the same contract a GitLab CI bot
meets. It prints the tool surface, hands over one merge request discussion, and
then searches for what it just extracted to show that none of it is retrievable.

First run takes about a minute: the embedder model downloads, then extraction
makes its model call.

## 5. Review, as a person would

In a second terminal:

```bash
uv run python examples/run_review_ui.py
```

On the first run it will tell you to put a token in `.env` and stop, rather than
inventing a different one each time:

```ini
FIRM_MEMORY_REVIEW_TOKEN=<64 hex characters>
FIRM_MEMORY_REVIEW_APPROVER=your-name
```

For a team, one token each instead:

```ini
FIRM_MEMORY_REVIEW_TOKENS="ashish:<token>,priya:<token>"
```

Then open `http://127.0.0.1:8765` and paste the token. **There is no name to
fill in** — the token is who you are, and every fact you approve is recorded
against the name it was issued to. Approve, reject with a reason, or **Edit
first** to fix the wording, the type or the scope before approving.

Keyboard, on the top card: <kbd>A</kbd> approve, <kbd>E</kbd> edit,
<kbd>R</kbd> reject.

## 6. Confirm the loop closed

```bash
uv run python examples/ingest_via_mcp.py
```

Step 3 now lists what you approved as retrievable. Re-running is safe: candidate
ids are derived from the fact itself, so re-proposing updates the same queue
entry instead of adding a duplicate.

---

## What "the same output" means

**The wording and the count will differ every run.** Extraction is a model call,
and the same discussion yields six candidates one time and eight the next, split
at different granularities and typed differently at the margins — `anti_patterns`
or `architecture_decisions` for the rejected DropCopy approach, say. That is
normal and not a misconfiguration.

What should be identical every time:

- the five tools listed in step 1;
- candidates scoped `domain:execution repo:oms repo:gateway`;
- **zero** of the just-extracted candidates retrievable in step 3;
- every candidate typed within the firm taxonomy — nothing consumer-shaped
  ("the reviewer prefers tabs") survives to the queue.

If a candidate reads like a personal preference, switch extractors with
`FIRM_MEMORY_EXTRACTOR=llm` and compare. mem0's prompt is written for a consumer
assistant; the platform's own is written for engineering memory.

---

## Configuration these scripts set for you

`examples/_environment.py` fills these in as defaults. **A deployment that does
not go through the examples has to set them itself** — the bare defaults point
at OpenAI, which is not where your key is.

| Variable | Set to | Why |
| --- | --- | --- |
| `FIRM_MEM0_LLM_PROVIDER` | `litellm` | One gateway key for any model. Without this the default is `openai` + `gpt-4o-mini`, which fails with an OpenRouter key. |
| `FIRM_MEM0_LLM_MODEL` | `openrouter/anthropic/claude-haiku-4.5` | |
| `FIRM_MEM0_EMBEDDER_PROVIDER` | `fastembed` | Local ONNX. OpenRouter has no embeddings endpoint, and local keeps memory content off the network. |
| `FIRM_MEM0_EMBEDDING_DIMS` | `384` | Must match the embedder — pgvector fixes the column width at creation. |
| `MEM0_TELEMETRY` | `False` | **mem0 reports usage to PostHog by default.** A pool of trading knowledge should not phone home. |
| `FIRM_MEMORY_CANDIDATES_URL` | the pgvector DSN | The queue must outlive the CI job that fills it. Unset means in-process, and an agent's proposals die with the process. |

The review UI additionally needs a token, which is **not** defaulted — it is a
credential, and one invented per run cannot be bookmarked:

| Variable | |
| --- | --- |
| `FIRM_MEMORY_REVIEW_TOKEN` + `FIRM_MEMORY_REVIEW_APPROVER` | One reviewer. |
| `FIRM_MEMORY_REVIEW_TOKENS` | `"name:token,name:token"` — one token each. Sharing one between two people is refused. |

---

## When it does not work

| Symptom | Cause |
| --- | --- |
| `'firm-memory-mcp' is not on PATH` | `uv sync` ran without `--extra mcp`. |
| `Cannot connect to the Docker daemon` | Docker is installed but not started. |
| `FIRM_MEM0_PG_DSN is not set` | No `.env`, or it was not filled in. |
| `The candidate queue in Postgres is unreachable` (503 in the UI) | The database stopped. Nothing is lost — the queue is a table. |
| Review UI shows an empty queue after a successful ingest | The two processes disagree on `FIRM_MEMORY_CANDIDATES_URL`. Both must resolve the same one; run both through the example scripts and they will. |
| `mcp package … version problem` | An SDK major neither 1.x nor 2.x. `uv add 'mcp>=1.2'`. |
| `FIRM_MEMORY_REVIEW_APPROVER is required` | A token with no reviewer attached. Every approval is attributed, and the token is what says to whom. |
