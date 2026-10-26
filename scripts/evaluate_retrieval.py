"""Reproducible retrieval evaluation over the sample corpus.

Ingests the sample documents into a dedicated `documents_eval` ChromaDB
collection (the live `documents` index and PostgreSQL are never touched),
runs every question in evals/dataset.jsonl, and reports Recall@k, MRR, and
retrieval latency. Results are written to evals/results/baseline.json.

The term index lives in its own SQLite file (data/bm25_eval.db) so the
evaluation never reads from or writes to the live BM25 index.

Usage:
    uv run python scripts/evaluate_retrieval.py                 # hybrid (default)
    uv run python scripts/evaluate_retrieval.py --no-hybrid     # vector-only
    uv run python scripts/evaluate_retrieval.py --skip-ingest   # reuse eval collection
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import PROJECT_ROOT as APP_PROJECT_ROOT, get_settings  # noqa: E402
from app.services.bm25_index import Bm25Index  # noqa: E402
from app.services.chunking import chunk_pages  # noqa: E402
from app.services.embeddings import Embedder  # noqa: E402
from app.services.retrieval import recall_at_k, retrieve_documents, reciprocal_rank  # noqa: E402
from app.services.text_extraction import extract_pages  # noqa: E402
from app.services.vector_store import VectorStore  # noqa: E402

EVAL_COLLECTION = "documents_eval"
EVAL_BM25_PATH = PROJECT_ROOT / "data" / "bm25_eval.db"
CORPUS = [
    "samples/employee_handbook.md",
    "samples/it_support_guide.md",
    "samples/security_policy.md",
]
DATASET_PATH = PROJECT_ROOT / "evals" / "dataset.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
RELEVANCE_KEY_SAFETY = 0.6  # no-answer top-1 score above this is flagged


def load_dataset(path: Path) -> list[dict]:
    questions = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                questions.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    if not questions:
        raise SystemExit(f"{path} contains no questions")
    return questions


def reset_eval_collection(settings) -> None:
    client = chromadb.PersistentClient(
        path=str(settings.chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    try:
        client.delete_collection(EVAL_COLLECTION)
        print(f"reset collection '{EVAL_COLLECTION}'")
    except Exception:
        pass  # collection did not exist yet
    if EVAL_BM25_PATH.exists():
        EVAL_BM25_PATH.unlink()


def ingest_corpus(store: VectorStore, embedder: Embedder, bm25: Bm25Index) -> int:
    total_chunks = 0
    for relative in CORPUS:
        path = APP_PROJECT_ROOT / relative
        data = path.read_bytes()
        pages = extract_pages(path.name, data)
        chunks = chunk_pages(
            pages, document_id=str(uuid.uuid4()), filename=path.name
        )
        vectors = embedder.embed([chunk.text for chunk in chunks])
        store.upsert_chunks(chunks, vectors)
        bm25.index_chunks(chunks)
        total_chunks += len(chunks)
        print(f"ingested {path.name}: {len(chunks)} chunks")
    return total_chunks


def hit_key(hit) -> tuple:
    return (hit.filename, hit.page)


def evaluate(
    questions: list[dict],
    *,
    embedder: Embedder,
    store: VectorStore,
    bm25: Bm25Index,
    top_k: int,
    hybrid: bool,
    rrf_k: int,
) -> dict:
    per_question = []
    answerable_rows = []
    no_answer_rows = []

    for item in questions:
        outcome = retrieve_documents(
            item["question"],
            embedder=embedder,
            store=store,
            top_k=top_k,
            bm25=bm25,
            hybrid=hybrid,
            rrf_k=rrf_k,
        )
        keys = [hit_key(hit) for hit in outcome.results]
        relevant = [(r["filename"], r["page"]) for r in item["relevant"]]
        top1 = outcome.results[0] if outcome.results else None

        row = {
            "question": item["question"],
            "type": item["type"],
            "latency_ms": round(outcome.elapsed_ms, 1),
            "top1_score": round(top1.score, 3) if top1 else None,
            "retrieved": [
                {"filename": key[0], "page": key[1], "score": round(hit.score, 3)}
                for key, hit in zip(keys, outcome.results)
            ],
        }
        if item["type"] == "no_answer":
            no_answer_rows.append(row)
        else:
            row["recall@3"] = round(recall_at_k(relevant, keys, 3), 3)
            row["recall@5"] = round(recall_at_k(relevant, keys, 5), 3)
            row["reciprocal_rank"] = round(reciprocal_rank(relevant, keys), 3)
            answerable_rows.append(row)
        per_question.append(row)

    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    all_latencies = [row["latency_ms"] for row in per_question]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": CORPUS,
        "top_k": top_k,
        "hybrid": hybrid,
        "rrf_k": rrf_k if hybrid else None,
        "score_semantics": "rrf" if hybrid else "cosine",
        "num_questions": len(questions),
        "answerable": {
            "count": len(answerable_rows),
            "recall@3": _mean([row["recall@3"] for row in answerable_rows]),
            "recall@5": _mean([row["recall@5"] for row in answerable_rows]),
            "mrr": _mean([row["reciprocal_rank"] for row in answerable_rows]),
        },
        "no_answer": {
            "count": len(no_answer_rows),
            "top1_scores": [row["top1_score"] for row in no_answer_rows],
            "max_top1_score": max(
                (row["top1_score"] or 0.0) for row in no_answer_rows
            )
            if no_answer_rows
            else None,
        },
        "latency_ms": {
            "mean": _mean(all_latencies),
            "max": round(max(all_latencies), 1) if all_latencies else 0.0,
        },
        "per_question": per_question,
    }
    return summary


def print_report(summary: dict) -> None:
    hybrid = bool(summary.get("hybrid"))
    print()
    for row in summary["per_question"]:
        if row["type"] == "no_answer":
            if hybrid:
                flag = ""  # RRF scores are rank-based, not comparable to 0.6
            else:
                flag = " (!)" if (row["top1_score"] or 0) >= RELEVANCE_KEY_SAFETY else ""
            print(
                f"[no_answer]   top1={row['top1_score']}  {row['latency_ms']}ms{flag}"
            )
            print(f"    {row['question']}")
        else:
            ok = "ok " if row["recall@5"] >= 1.0 else "MISS"
            print(
                f"[{ok}] r@3={row['recall@3']:.2f} r@5={row['recall@5']:.2f} "
                f"rr={row['reciprocal_rank']:.2f} top1={row['top1_score']}  "
                f"{row['latency_ms']}ms"
            )
            print(f"    {row['question']}")

    answerable = summary["answerable"]
    no_answer = summary["no_answer"]
    latency = summary["latency_ms"]
    print()
    print(
        f"answerable ({answerable['count']}): "
        f"Recall@3={answerable['recall@3']}  Recall@5={answerable['recall@5']}  "
        f"MRR={answerable['mrr']}"
    )
    print(
        f"no-answer ({no_answer['count']}): max top-1 score={no_answer['max_top1_score']}"
        + ("" if hybrid else f" (flagged if >= {RELEVANCE_KEY_SAFETY})")
    )
    print(f"latency: mean={latency['mean']}ms  max={latency['max']}ms")
    if hybrid:
        print(
            "note: hybrid mode -> scores are RRF fused (rank-based); "
            f"the {RELEVANCE_KEY_SAFETY} no-answer safety flag does not apply"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument(
        "--no-hybrid",
        action="store_true",
        help="Disable BM25 fusion (pure vector path, for before/after comparison)",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse the existing documents_eval collection",
    )
    parser.add_argument(
        "--save",
        default=str(RESULTS_DIR / "baseline.json"),
        help="Where to write results JSON ('' to skip saving)",
    )
    args = parser.parse_args()

    settings = get_settings()
    top_k = args.top_k or settings.retrieval_top_k
    hybrid = settings.retrieval_hybrid and not args.no_hybrid
    rrf_k = settings.retrieval_rrf_k
    embedder = Embedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        batch_size=settings.embedding_batch_size,
        max_retries=settings.embedding_max_retries,
    )

    if not args.skip_ingest:
        reset_eval_collection(settings)
    store = VectorStore(settings.chroma_path, EVAL_COLLECTION)
    bm25 = Bm25Index(EVAL_BM25_PATH)

    if not args.skip_ingest:
        chunk_total = ingest_corpus(store, embedder, bm25)
        print(f"ingested {chunk_total} chunks from {len(CORPUS)} documents\n")
    else:
        print("skipping ingest; reusing existing eval collection\n")

    if hybrid and bm25.count() == 0:
        print(
            "warning: eval term index is empty; running vector-only. "
            "Re-run without --skip-ingest.\n"
        )
        hybrid = False

    questions = load_dataset(DATASET_PATH)
    summary = evaluate(
        questions,
        embedder=embedder,
        store=store,
        bm25=bm25,
        top_k=top_k,
        hybrid=hybrid,
        rrf_k=rrf_k,
    )
    print_report(summary)

    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nsaved results to {save_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
