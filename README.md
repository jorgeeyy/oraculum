# RAG Knowledge Assistant

A production-style Retrieval-Augmented Generation (RAG) knowledge assistant. It ingests documents (PDF, TXT, Markdown), indexes them in a vector database, and answers questions grounded in the retrieved evidence with source citations — or states clearly when the documents do not contain enough information.

This project is being built incrementally following `production_rag_knowledge_assistant_guide.md`. It is currently at **Phase 0: repository and environment setup** (a minimal FastAPI application with a health endpoint). Ingestion, embeddings, retrieval, and answer generation are not implemented yet.

## Stack

- **Language:** Python 3.13
- **API:** FastAPI + Uvicorn
- **Package/dependency manager:** [uv](https://docs.astral.sh/uv/)
- **LLM and embeddings:** OpenAI API
- **Vector database:** Pinecone
- **Relational database:** PostgreSQL (local)
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
| `DATABASE_URL` | PostgreSQL connection string |

`.env` is git-ignored. Never commit real keys.

## Run

```powershell
uv run uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Test

```powershell
uv run pytest
```

## Project structure

```text
rag-application/
├── app/
│   ├── main.py          # FastAPI application factory
│   ├── config.py        # Settings loaded from environment / .env
│   └── api/
│       └── health.py    # GET /health
├── tests/
│   └── test_health.py
├── .env.example
├── pyproject.toml
└── README.md
```

## Status and roadmap

- [x] Phase 0 — Repository and environment setup
- [ ] Phase 1 — Document ingestion and chunking
- [ ] Phase 2 — Embeddings and Pinecone indexing
- [ ] Phase 3 — Retrieval
- [ ] Phase 4 — Grounded answer generation and citations
- [ ] Phase 5 — API, persistence, and reliability
- [ ] Phase 6 — Evaluation and monitoring
- [ ] Phase 7 — Testing, Docker, and deployment

## Security notes

- Secrets live only in `.env` (git-ignored) or environment variables.
- `.env.example` contains placeholders only.
- Document contents and retrieval results are treated as untrusted input in later phases.
