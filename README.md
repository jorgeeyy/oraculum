# Oraculum

A production-style Retrieval-Augmented Generation (RAG) knowledge assistant. It ingests documents (PDF, TXT, Markdown), indexes them in a vector database, and answers questions grounded in the retrieved evidence with source citations — or states clearly when the documents do not contain enough information.

## Stack

- **Language:** Python 3.13
- **API:** FastAPI + Uvicorn
- **Package/dependency manager:** [uv](https://docs.astral.sh/uv/)
- **LLM and embeddings:** OpenAI API (`text-embedding-3-small`)
- **Vector database:** ChromaDB (local, persistent)
- **Relational database:** PostgreSQL via SQLAlchemy 2.0 + Alembic
- **Validation:** Pydantic / pydantic-settings
- **Testing:** pytest + HTTPX

## Setup

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL (local instance; needed from Phase 5 onward)

### Install

```powershell
git clone <repo-url>
cd oraculum
uv sync
```

### Configure environment

Copy `.env.example` to `.env` and fill in your own values:

```powershell
Copy-Item .env.example .env
```

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key for embeddings and answer generation |
| `DATABASE_URL` | PostgreSQL connection string (`postgresql://user:pass@host:port/db`) |
| `CHUNK_SIZE_TOKENS` | Target chunk size in tokens (default `600`) |
| `CHUNK_OVERLAP_TOKENS` | Overlap between adjacent chunks (default `80`) |
| `MAX_FILE_SIZE_BYTES` | Upload size limit (default `10485760`) |
| `UPLOADS_DIR` | Where uploaded files are stored (default `data/uploads`) |
| `EMBEDDING_MODEL` | OpenAI embedding model (default `text-embedding-3-small`) |
| `EMBEDDING_BATCH_SIZE` | Texts per embedding API call (default `100`) |
| `EMBEDDING_TIMEOUT_SECONDS` | Embedding API timeout (default `30`) |
| `CHROMA_DIR` | ChromaDB persistence directory (default `data/chroma`) |
| `RETRIEVAL_TOP_K` | Server default for `/retrieve` + `/chat` (default `8`; per-request `top_k` overrides) |
| `RETRIEVAL_HYBRID` | Fuse BM25 term search with vector search (default `true`; `false` = legacy vector-only) |
| `RETRIEVAL_RRF_K` | Reciprocal Rank Fusion constant (default `60`) |
| `BM25_DB_PATH` | SQLite FTS5 term-index file (default `data/bm25.db`) |
| `CHAT_MODEL` | OpenAI chat model for answers (default `gpt-4o-mini`) |
| `CHAT_MAX_TOKENS` | Max tokens per generated answer (default `500`) |
| `CHAT_TEMPERATURE` | Sampling temperature (default `0.0`) |
| `RATE_LIMIT_PER_MINUTE` | Per-IP cap on `/chat` + `/retrieve` (default `60`; `0` disables) |
| `API_KEY` | When set, `/documents`, `/retrieve`, `/chat` require this key (unset = open) |
| `LOG_FILE` | Rotating application log file (default `data/logs/oraculum.log`; set empty to disable) |

`.env` is git-ignored. Never commit real keys.

Note: `DATABASE_URL` accepts the plain PostgreSQL format (`postgresql://...`) or the SQLAlchemy dialect format (`postgresql+psycopg://...`). Both are normalized automatically.

### Create the database

```powershell
# in psql or your preferred client, once:
CREATE DATABASE rag_assistant;
```

Then apply the schema migrations:

```powershell
uv run alembic upgrade head
```

### Database migrations (Alembic)

The schema is managed by Alembic. When you change a model in `app/db/models.py`:

```powershell
# 1. generate a migration from the model changes
uv run alembic revision --autogenerate -m "describe your change"

# 2. review the generated file in alembic/versions/

# 3. apply it
uv run alembic upgrade head

# check current revision / history
uv run alembic current
uv run alembic history
```

## Run

```powershell
# backend
uv run uvicorn app.main:app --reload

# frontend (separate terminal, dev server with API proxy on :5173)
cd frontend
npm run dev
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health
- UI (dev): http://localhost:5173 — Vite proxies `/chat`, `/documents`,
  `/retrieve`, and `/health` to the backend
- UI (production): `cd frontend && npm run build`, then restart the backend —
  FastAPI serves `frontend/dist` at `/`, so the UI and API are same-origin and
  no CORS configuration is needed

### Logs

Application logs (requests with their `X-Request-ID`, ingestion progress,
errors with tracebacks) are written to `data/logs/oraculum.log` in addition
to stderr. The file rotates at 5 MB and keeps 3 backups. Tail it live:

```powershell
Get-Content data\logs\oraculum.log -Wait -Tail 50
```

Set `LOG_FILE=` (empty) in `.env` to disable file logging.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/documents` | Upload a PDF/TXT/Markdown file; validates synchronously, then extracts/chunks/embeds/indexes it in the background (`202` + `status: pending`; poll `GET /documents` for progress) |
| `GET` | `/documents` | List ingested documents and their statuses |
| `GET` | `/documents/{id}` | Document record, ingestion status, and indexing config used |
| `GET` | `/documents/{id}/chunks` | Re-chunk a stored document with the current chunking configuration |
| `DELETE` | `/documents/{id}` | Remove a document's vectors, stored file, and record |
| `POST` | `/retrieve` | Semantic search: question in, top-k chunks out with scores and citations |
| `POST` | `/chat` | Grounded Q&A: short answer with resolved source citations, or an explicit insufficient-evidence refusal; pass `conversation_id` to persist the exchange |
| `GET` | `/conversations` | List saved chats (id, title, message count, activity) |
| `GET` | `/conversations/{id}` | Full thread: title plus every stored message in order |
| `PATCH` | `/conversations/{id}` | Rename a chat |
| `DELETE` | `/conversations/{id}` | Delete a chat and all of its messages |
| `GET` | `/health` | Process liveness only |
| `GET` | `/health/ready` | Dependency readiness: Postgres + vector store must respond (503 if down) |

Try it with the committed sample documents:

```powershell
# via Swagger UI: open http://127.0.0.1:8000/docs and upload samples/employee_handbook.md
# or via curl (returns 202 immediately; ingestion continues in the background):
curl -F "file=@samples/employee_handbook.md" http://127.0.0.1:8000/documents
curl http://127.0.0.1:8000/documents   # poll until status is "completed" (or "failed")

# semantic retrieval (no LLM involved):
curl -X POST http://127.0.0.1:8000/retrieve -H "Content-Type: application/json" `
  -d "{\"question\": \"How many days of annual leave?\"}"

# grounded answer with citations:
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" `
  -d "{\"question\": \"How many days of annual leave?\"}"

# persistent thread: conversation_id: null creates a chat and returns its id;
# sending that id back appends to it. Omitting the key entirely stays stateless.
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" `
  -d "{\"question\": \"How many days of annual leave?\", \"conversation_id\": null}"
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" `
  -d "{\"question\": \"What about carry-over?\", \"conversation_id\": \"<id from previous reply>\"}"
curl http://127.0.0.1:8000/conversations

# or the standalone smoke script:
uv run python scripts/verify_index.py "How many days of annual leave?"
```

### Hybrid retrieval (BM25 + vector)

Retrieval runs two rankings and fuses them with Reciprocal Rank Fusion:

- **Vector search** — cosine similarity over OpenAI embeddings (ChromaDB).
- **BM25 term search** — SQLite FTS5 with porter stemming, in `data/bm25.db`.
  No new dependencies; the row ids mirror Chroma's `{document_id}#{chunk_index}`
  so both rankings fuse on the same keys.

Each side ranks its top-k; every chunk votes `1 / (rrf_k + rank)` and chunks
found by both sides win. This fixes a flat-cosine failure mode where a question
literally containing a distinctive term ("principles") missed the chunk holding
it because the scores around the cutoff differed by ~0.001. **Scores are fused
rank support, not similarity** — they are for ordering, never confidence.

Consistency policy: a term-index write failure during ingestion **fails the
document and rolls back its vectors** (a `failed` document is never retrievable
through either path); a term-index *query* failure only degrades the request to
vector-only search (logged as a warning). Deleting a document removes its FTS
rows first, then its vectors — every step is idempotent, so retries are safe.

Upgrading an existing index (one-time; your documents are untouched):

```powershell
uv run python scripts/backfill_bm25.py   # rebuilds data/bm25.db from stored texts
```

Set `RETRIEVAL_HYBRID=false` to restore the exact legacy vector-only path.

### How grounding works

1. The question is retrieved through the hybrid pipeline above (same as `/retrieve`).
2. No evidence at all -> a deterministic refusal; the LLM is never called.
3. Evidence is passed to the chat model as clearly-labelled untrusted data; the trusted
   system prompt forbids following instructions found inside documents and requires
   structured JSON output (answer + cited evidence labels).
4. Citations are resolved in application code against the real retrieved chunks:
   invented labels are dropped, and an "answered" result with no resolvable citation
   is downgraded to `insufficient_evidence` so uncited claims are never presented as
   grounded facts.

### Reliability and security (Phase 5)

- **Shared OpenAI client** — one cached client per (key, timeout) reuses HTTP
  connections across requests; building a client per request pays a fresh TLS
  handshake every call (seconds of overhead on Windows).
- **Request tracing** — every request gets an `X-Request-ID` (incoming value is
  echoed, otherwise generated); log lines during the request carry the same ID.
- **Background ingestion** — uploads only validate and persist the file plus
  a `pending` row (`202 Accepted`); extraction, chunking, embedding, and
  vector indexing run as a FastAPI background task, so a large PDF never
  blocks the API or the chat. A process-wide semaphore ingests one document
  at a time. Any failure is stamped on the row (`failed` + `error_message`)
  instead of failing the upload. On startup, rows stuck in
  `pending`/`processing` (e.g. after a crash) are re-queued automatically.
- **Idempotent uploads** — SHA-256 of the file bytes is stored per document;
  re-uploading identical content returns the existing document with HTTP 200
  when it is already indexed, or HTTP 202 when it is still being indexed,
  never a duplicate record. Identical bytes that previously failed are
  retried in place (same row, error cleared).
- **Rate limiting** — per-IP fixed window on `/chat` and `/retrieve`
  (default 60/min, HTTP 429 + `Retry-After`); `/documents` and health are exempt.
- **Optional API-key auth** — set `API_KEY` and every non-health endpoint
  requires `Authorization: Bearer <key>` or `X-API-Key: <key>` (401 otherwise).
  Unset = local development behaves exactly as before.
- **Dependency-aware readiness** — `/health` is process liveness only;
  `/health/ready` checks Postgres and the vector store and returns 503 when
  a critical dependency is down.
- **Chat history** — exchanges are saved server-side in Postgres when the
  `/chat` request includes a `conversation_id` (`null` creates a new chat,
  a UUID appends, omission keeps the endpoint stateless for scripts).
  Each chat is scoped to a SHA-256 fingerprint of the API key (or a single
  local scope when no key is configured), so changing `API_KEY` hides the
  previous key's history. Nothing is auto-purged; chats are deletable
  individually via `DELETE /conversations/{id}`.

## Frontend

A React + Vite + TypeScript single-page UI (Tailwind CSS v4, sidebar
navigation, no router) with two views:

- **Chat** — ask questions, get grounded answers with expandable source
  excerpts, status chip (grounded vs insufficient evidence), and timing;
  401 opens an API-key modal, 429 shows a retry countdown. The sidebar
  lists saved chats with a **New chat** button, resume on click, and
  inline delete; threads reload from `GET /conversations/{id}` and the
  first message of a new thread creates the conversation server-side
- **Documents** — drag-and-drop upload (returns instantly; indexing runs in
  the background and the list auto-refreshes every 2 s while anything is
  pending/processing), duplicate uploads are detected and reported, list
  with status/chunks/size, view chunked text, delete
- **Health badge** — polls `/health/ready` every 30 s with dependency details
  on hover

The `/retrieve` endpoint stays API-only (no UI screen): it remains available
via `/docs` and curl as a retrieval debugging tool.

```powershell
cd frontend
npm install          # once
npm run dev          # dev server + proxy → :5173
npm run typecheck    # tsc --noEmit
npm test             # vitest (API client error-mapping tests)
npm run build        # typecheck + production build to frontend/dist
```

The API key (only needed when the server has `API_KEY` set) is stored in
`localStorage` and sent as `X-API-Key`. The theme (dark by default, light
toggle in the sidebar) is also persisted in `localStorage`.

## Evaluation

A labelled dataset (`evals/dataset.jsonl`, 41 questions across four types —
factual, multi-document, distractor, and no-answer) drives two suites. One
command runs both over a shared corpus ingest and writes a single results
file with provenance (app version, git commit, dataset hash, run config):

```powershell
uv run python scripts/evaluate.py                    # retrieval + chat suites
uv run python scripts/evaluate.py --retrieval-only   # recall / MRR only
uv run python scripts/evaluate.py --chat-only        # answer quality only
uv run python scripts/evaluate.py --skip-ingest      # reuse the eval index
```

The suite ingests the sample documents into a dedicated `documents_eval`
ChromaDB collection and a dedicated `data/bm25_eval.db` term index (the live
indexes and PostgreSQL are never touched). Results are written to
`evals/results/latest.json` (`--save` to write elsewhere). A full run costs a
few cents; retrieval-only runs cost a fraction of a cent.

### Metric glossary

| Metric | Meaning |
|---|---|
| `retrieval.answerable.recall@3` / `recall@5` | Fraction of answerable questions with a relevant document in the top-3 / top-5 results. |
| `retrieval.answerable.mrr` | Mean reciprocal rank of the first relevant hit. |
| `retrieval.no_answer.max_top1_score` | Highest retrieval score given to an unanswerable question — a hallucination-risk guard. Scores are rank support, not confidence. |
| `chat.answerable.fact_score_mean` / `fact_all_rate` | Fraction of `expected_facts` present in the answer / fraction of questions with all facts present (case-insensitive substring; `\|`-separated phrasing alternates). |
| `chat.no_answer.abstention_accuracy` | No-answer questions correctly refused instead of hallucinated (the safety number). |
| `chat.answerable.citation_coverage` | Fraction of declared relevant documents actually cited by the answer. |
| `chat.answerable.grounded_sentence_pct` | Structural groundedness proxy: answer sentences sharing content words with the retrieved evidence. |
| `chat.errors.rate` | Questions that failed with an error (lower is better). |
| `chat.latency_ms.*` / `retrieval.latency_ms.*` | Mean stage latencies in milliseconds. |
| `chat.tokens.*` / `chat.cost_usd.*` | Usage totals; cost is estimated from an approximate static price table in the eval script — production code holds no prices. |
| `chat.answerable.abstained` | Answerable questions the model declined to answer (over-caution counterweight). |

Every results file carries `generated_at`, `app_version`, `git_commit`, and
`dataset_sha256`, so any number can be traced to the code and dataset that
produced it.

### Comparing runs (regression gate)

```powershell
uv run python scripts/compare_eval.py evals/results/baseline.json evals/results/latest.json
```

Prints a per-metric `old → new` table with deltas, applies per-metric
tolerances (ε on scores, ±25% relative on latency), and exits `1` if a gated
metric regressed or is missing — `0` otherwise, so it drops into CI. Legacy
flat result files (`baseline.json`, `hybrid.json`) stay comparable: dotted
metric paths fall back to top-level keys automatically. The gate logic is
covered by `tests/test_eval_regression.py` (no API key needed).

## Test

```powershell
uv run pytest
```

## Project structure

```text
oraculum/
├── app/
│   ├── main.py                  # FastAPI application factory
│   ├── config.py                # Settings loaded from environment / .env
│   ├── observability.py         # X-Request-ID middleware + request-scoped log ID
│   ├── api/
│   │   ├── health.py            # GET /health + GET /health/ready
│   │   ├── auth.py              # Optional API-key dependency
│   │   ├── documents.py         # Document upload/list/chunks/delete endpoints
│   │   ├── retrieve.py          # POST /retrieve semantic search endpoint
│   │   ├── chat.py              # POST /chat grounded answer endpoint (optional persistence)
│   │   ├── conversations.py      # Chat history list/get/rename/delete endpoints
│   │   └── deps.py               # Shared DB session dependencies (503 on DB down)
│   ├── db/
│   │   ├── base.py               # SQLAlchemy DeclarativeBase
│   │   ├── session.py            # Engine + session factory + URL normalization
│   │   ├── models.py             # SQLAlchemy Document, Conversation, ChatMessage models
│   │   └── repositories.py       # Document record CRUD + conversation/message persistence
│   ├── schemas/
│   │   ├── documents.py          # Pydantic models for pages, chunks, records
│   │   ├── retrieval.py          # Pydantic models for retrieval request/results
│   │   ├── chat.py               # Pydantic models for chat request/answer/sources
│   │   └── conversations.py       # Pydantic models for saved chats and messages
│   └── services/
│       ├── text_extraction.py    # PDF/TXT/Markdown → pages (magic-byte validation)
│       ├── chunking.py           # Pages → token-bounded overlapping chunks (tiktoken)
│       ├── embeddings.py         # Batched OpenAI embeddings with bounded retries
│       ├── ingestion.py          # Background pipeline: extract→chunk→embed→index + startup recovery
│       ├── openai_client.py      # Shared cached OpenAI HTTP client
│       ├── vector_store.py       # Persistent ChromaDB collection (upsert/query/delete)
│       ├── bm25_index.py          # SQLite FTS5 term index (BM25) + failure policy
│       ├── retrieval.py           # Hybrid retrieve_documents + RRF + recall/MRR metrics
│       ├── generation.py         # Grounded answer generation + citation resolution
│       ├── rate_limit.py         # In-memory per-IP rate limiting
│       ├── storage.py            # Safe UUID-based file storage under data/uploads
│       └── errors.py             # Ingestion, storage, and indexing error types
├── alembic/
│   ├── env.py                    # Alembic env wired to app settings
│   └── versions/                 # Generated migration files
├── alembic.ini                   # Alembic config (no secrets)
├── samples/                      # Fictional sample documents for manual testing
├── evals/
│   ├── dataset.jsonl             # Labelled eval questions (41, 4 types, expected_facts)
│   └── results/                  # Evaluation output (baseline.json, latest.json, ...)
├── scripts/
│   ├── verify_index.py           # Hybrid retrieval smoke test against the index
│   ├── backfill_bm25.py          # One-time: rebuild the FTS term index from stored texts
│   ├── evaluate.py               # Single entry point: retrieval + chat suites, one results file
│   ├── evaluate_retrieval.py     # Recall@k / MRR / latency suite (used by evaluate.py)
│   ├── evaluate_chat.py          # Answer quality / abstention / cost suite (used by evaluate.py)
│   └── compare_eval.py           # Regression gate: compare two result files, exit 1 on drop
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_text_extraction.py
│   ├── test_chunking.py
│   ├── test_embeddings.py
│   ├── test_vector_store.py
│   ├── test_bm25_index.py
│   ├── test_retrieval.py
│   ├── test_hybrid.py
│   ├── test_generation.py
│   ├── test_chat.py
│   ├── test_reliability.py
│   ├── test_documents_api.py
│   ├── test_ingestion.py
│   ├── test_conversations.py
│   ├── test_eval_dataset.py
│   └── test_eval_regression.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts            # Vite config + dev proxy + vitest config
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx               # Tab shell + chat/document sidebar + API-key modal
│       ├── index.css               # Tailwind v4 entry: @theme tokens, dark palette
│       ├── api/                  # Typed fetch client + TS types + tests
│       ├── components/           # HealthBadge, SourceCard, upload dropzone
│       ├── hooks/                # useDocuments: shared list + status polling
│       └── tabs/                 # Chat, Documents screens
├── .env.example
├── pyproject.toml
└── README.md
```