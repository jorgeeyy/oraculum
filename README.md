# RAG Knowledge Assistant

A production-style Retrieval-Augmented Generation (RAG) knowledge assistant. It ingests documents (PDF, TXT, Markdown), indexes them in a vector database, and answers questions grounded in the retrieved evidence with source citations — or states clearly when the documents do not contain enough information.

This project is being built incrementally following `production_rag_knowledge_assistant_guide.md`. It is currently at **Phase 5: API, persistence, and reliability** — the service now has request tracing, rate limiting, optional API-key auth, idempotent uploads, dependency-aware health checks, and shared connection pooling to the OpenAI API.

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
cd rag-application
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
| `CHAT_MODEL` | OpenAI chat model for answers (default `gpt-4o-mini`) |
| `CHAT_MAX_TOKENS` | Max tokens per generated answer (default `500`) |
| `CHAT_TEMPERATURE` | Sampling temperature (default `0.0`) |
| `RATE_LIMIT_PER_MINUTE` | Per-IP cap on `/chat` + `/retrieve` (default `60`; `0` disables) |
| `API_KEY` | When set, `/documents`, `/retrieve`, `/chat` require this key (unset = open) |

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
uv run uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/documents` | Upload a PDF/TXT/Markdown file; extracts, chunks, embeds, and indexes it |
| `GET` | `/documents` | List ingested documents and their statuses |
| `GET` | `/documents/{id}` | Document record, ingestion status, and indexing config used |
| `GET` | `/documents/{id}/chunks` | Re-chunk a stored document with the current chunking configuration |
| `DELETE` | `/documents/{id}` | Remove a document's vectors, stored file, and record |
| `POST` | `/retrieve` | Semantic search: question in, top-k chunks out with scores and citations |
| `POST` | `/chat` | Grounded Q&A: short answer with resolved source citations, or an explicit insufficient-evidence refusal |
| `GET` | `/health` | Process liveness only |
| `GET` | `/health/ready` | Dependency readiness: Postgres + vector store must respond (503 if down) |

Try it with the committed sample documents:

```powershell
# via Swagger UI: open http://127.0.0.1:8000/docs and upload samples/employee_handbook.md
# or via curl:
curl -F "file=@samples/employee_handbook.md" http://127.0.0.1:8000/documents

# semantic retrieval (no LLM involved):
curl -X POST http://127.0.0.1:8000/retrieve -H "Content-Type: application/json" `
  -d "{\"question\": \"How many days of annual leave?\"}"

# grounded answer with citations:
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" `
  -d "{\"question\": \"How many days of annual leave?\"}"

# or the standalone smoke script:
uv run python scripts/verify_index.py "How many days of annual leave?"
```

### How grounding works

1. The question is embedded and the top-k chunks are retrieved (same pipeline as `/retrieve`).
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
- **Idempotent uploads** — SHA-256 of the file bytes is stored per document;
  re-uploading identical content returns the existing document with HTTP 200
  instead of creating a duplicate record and vectors.
- **Rate limiting** — per-IP fixed window on `/chat` and `/retrieve`
  (default 60/min, HTTP 429 + `Retry-After`); `/documents` and health are exempt.
- **Optional API-key auth** — set `API_KEY` and every non-health endpoint
  requires `Authorization: Bearer <key>` or `X-API-Key: <key>` (401 otherwise).
  Unset = local development behaves exactly as before.
- **Dependency-aware readiness** — `/health` is process liveness only;
  `/health/ready` checks Postgres and the vector store and returns 503 when
  a critical dependency is down.

## Retrieval evaluation

A labelled dataset (`evals/dataset.jsonl`, 12 questions across factual, multi-document,
distractor, and no-answer categories) is scored with Recall@3, Recall@5, MRR, and
retrieval latency:

```powershell
uv run python scripts/evaluate_retrieval.py
```

The script ingests the sample documents into a dedicated `documents_eval` ChromaDB
collection (the live index and PostgreSQL are never touched), runs every question,
prints a per-question report, and writes `evals/results/baseline.json` as the
measurement baseline. Query embeddings cost a fraction of a cent per run.

## Test

```powershell
uv run pytest
```

## Project structure

```text
rag-application/
├── app/
│   ├── main.py                  # FastAPI application factory
│   ├── config.py                # Settings loaded from environment / .env
│   ├── observability.py         # X-Request-ID middleware + request-scoped log ID
│   ├── api/
│   │   ├── health.py            # GET /health + GET /health/ready
│   │   ├── auth.py              # Optional API-key dependency
│   │   ├── documents.py         # Document upload/list/chunks/delete endpoints
│   │   ├── retrieve.py          # POST /retrieve semantic search endpoint
│   │   └── chat.py              # POST /chat grounded answer endpoint
│   ├── db/
│   │   ├── base.py               # SQLAlchemy DeclarativeBase
│   │   ├── session.py            # Engine + session factory + URL normalization
│   │   ├── models.py             # SQLAlchemy Document model
│   │   └── repositories.py       # Document record CRUD
│   ├── schemas/
│   │   ├── documents.py          # Pydantic models for pages, chunks, records
│   │   ├── retrieval.py          # Pydantic models for retrieval request/results
│   │   └── chat.py               # Pydantic models for chat request/answer/sources
│   └── services/
│       ├── text_extraction.py    # PDF/TXT/Markdown → pages (magic-byte validation)
│       ├── chunking.py           # Pages → token-bounded overlapping chunks (tiktoken)
│       ├── embeddings.py         # Batched OpenAI embeddings with bounded retries
│       ├── openai_client.py      # Shared cached OpenAI HTTP client
│       ├── vector_store.py       # Persistent ChromaDB collection (upsert/query/delete)
│       ├── retrieval.py          # retrieve_documents + recall/MRR metric functions
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
│   ├── dataset.jsonl             # Labelled retrieval questions (5 categories)
│   └── results/                  # Evaluation output (baseline.json)
├── scripts/
│   ├── verify_index.py           # Semantic retrieval smoke test against the index
│   └── evaluate_retrieval.py     # Recall@k / MRR / latency evaluation harness
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_text_extraction.py
│   ├── test_chunking.py
│   ├── test_embeddings.py
│   ├── test_vector_store.py
│   ├── test_retrieval.py
│   ├── test_generation.py
│   ├── test_chat.py
│   ├── test_reliability.py
│   └── test_documents_api.py
├── .env.example
├── pyproject.toml
└── README.md
```

## Status and roadmap

- [x] Phase 0 — Repository and environment setup
- [x] Phase 1 — Document ingestion and chunking
- [x] Phase 2 — Embeddings and vector indexing
- [x] Phase 3 — Retrieval
- [x] Phase 4 — Grounded answer generation and citations
- [x] Phase 5 — API, persistence, and reliability
- [ ] Phase 6 — Evaluation and monitoring
- [ ] Phase 7 — Testing, Docker, and deployment

Note: document record persistence (PostgreSQL) and the `/documents` endpoints were
pulled forward from Phase 5 into Phase 1 as a deliberate deviation from the guide's
phase order.

Deviation: the guide specifies Pinecone as the vector store; this project uses
ChromaDB instead (free, local, no external account) while filling the same role —
vector persistence, metadata filtering for scoped deletion, and citation metadata
for retrieval. Chat endpoints remain scheduled for later phases.

## Security notes

- Secrets live only in `.env` (git-ignored) or environment variables.
- `.env.example` contains placeholders only.
- Document contents and retrieval results are treated as untrusted input in later phases.
