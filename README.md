# RAG Knowledge Assistant

A production-style Retrieval-Augmented Generation (RAG) knowledge assistant. It ingests documents (PDF, TXT, Markdown), indexes them in a vector database, and answers questions grounded in the retrieved evidence with source citations — or states clearly when the documents do not contain enough information.

This project is being built incrementally following `production_rag_knowledge_assistant_guide.md`. It is currently at **Phase 1: document ingestion and chunking** — documents can be uploaded, stored, extracted, and chunked via the API. Embeddings, vector indexing, retrieval, and answer generation are not implemented yet.

## Stack

- **Language:** Python 3.13
- **API:** FastAPI + Uvicorn
- **Package/dependency manager:** [uv](https://docs.astral.sh/uv/)
- **LLM and embeddings:** OpenAI API
- **Vector database:** Pinecone
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
| `PINECONE_API_KEY` | Pinecone API key for vector storage |
| `PINECONE_INDEX_NAME` | Name of the Pinecone index to use |
| `DATABASE_URL` | PostgreSQL connection string (`postgresql://user:pass@host:port/db`) |
| `CHUNK_SIZE_TOKENS` | Target chunk size in tokens (default `600`) |
| `CHUNK_OVERLAP_TOKENS` | Overlap between adjacent chunks (default `80`) |
| `MAX_FILE_SIZE_BYTES` | Upload size limit (default `10485760`) |
| `UPLOADS_DIR` | Where uploaded files are stored (default `data/uploads`) |

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
| `POST` | `/documents` | Upload a PDF/TXT/Markdown file; returns the document record plus its chunks |
| `GET` | `/documents` | List ingested documents and their statuses |
| `GET` | `/documents/{id}` | Document record and ingestion status |
| `GET` | `/documents/{id}/chunks` | Re-chunk a stored document with the current chunking configuration |
| `DELETE` | `/documents/{id}` | Delete a document record and its stored file |
| `GET` | `/health` | Basic service health check |

Try it with the committed sample documents:

```powershell
# via Swagger UI: open http://127.0.0.1:8000/docs and upload samples/employee_handbook.md
# or via curl:
curl -F "file=@samples/employee_handbook.md" http://127.0.0.1:8000/documents
```

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
│   ├── api/
│   │   ├── health.py            # GET /health
│   │   └── documents.py         # Document upload/list/chunks/delete endpoints
│   ├── db/
│   │   ├── base.py               # SQLAlchemy DeclarativeBase
│   │   ├── session.py            # Engine + session factory + URL normalization
│   │   ├── models.py             # SQLAlchemy Document model
│   │   └── repositories.py       # Document record CRUD
│   ├── schemas/
│   │   └── documents.py          # Pydantic models for pages, chunks, records
│   └── services/
│       ├── text_extraction.py    # PDF/TXT/Markdown → pages (magic-byte validation)
│       ├── chunking.py           # Pages → token-bounded overlapping chunks (tiktoken)
│       ├── storage.py            # Safe UUID-based file storage under data/uploads
│       └── errors.py             # Ingestion and storage error types
├── alembic/
│   ├── env.py                    # Alembic env wired to app settings
│   └── versions/                 # Generated migration files
├── alembic.ini                   # Alembic config (no secrets)
├── samples/                      # Fictional sample documents for manual testing
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_text_extraction.py
│   ├── test_chunking.py
│   └── test_documents_api.py
├── .env.example
├── pyproject.toml
└── README.md
```

## Status and roadmap

- [x] Phase 0 — Repository and environment setup
- [x] Phase 1 — Document ingestion and chunking
- [ ] Phase 2 — Embeddings and Pinecone indexing
- [ ] Phase 3 — Retrieval
- [ ] Phase 4 — Grounded answer generation and citations
- [ ] Phase 5 — API, persistence, and reliability
- [ ] Phase 6 — Evaluation and monitoring
- [ ] Phase 7 — Testing, Docker, and deployment

Note: document record persistence (PostgreSQL) and the `/documents` endpoints were
pulled forward from Phase 5 into Phase 1 as a deliberate deviation from the guide's
phase order. Vector indexing and chat endpoints remain scheduled for later phases.

## Security notes

- Secrets live only in `.env` (git-ignored) or environment variables.
- `.env.example` contains placeholders only.
- Document contents and retrieval results are treated as untrusted input in later phases.
