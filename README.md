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
| `RETRIEVAL_TOP_K` | Server default for `/retrieve` + `/chat` (default `5`; per-request `top_k` overrides) |
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

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/documents` | Upload a PDF/TXT/Markdown file; extracts, chunks, embeds, and indexes it |
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
# or via curl:
curl -F "file=@samples/employee_handbook.md" http://127.0.0.1:8000/documents

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
- **Documents** — drag-and-drop upload (duplicate uploads are detected and
  reported), list with status/chunks/size, view chunked text, delete
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
│   ├── test_documents_api.py
│   └── test_conversations.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts            # Vite config + dev proxy + vitest config
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx               # Tab shell + chat-history sidebar + API-key modal
│       ├── index.css               # Tailwind v4 entry: @theme tokens, dark palette
│       ├── api/                  # Typed fetch client + TS types + tests
│       ├── components/           # HealthBadge, SourceCard, dropzone, modal
│       └── tabs/                 # Chat, Documents screens
├── .env.example
├── pyproject.toml
└── README.md
```