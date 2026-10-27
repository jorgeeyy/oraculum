"""Single documented entry point for the full evaluation.

Runs both suites over one shared eval corpus ingest and writes ONE results
file with provenance (app version, git commit, dataset hash, config):

    retrieval  Recall@3 / Recall@5 / MRR / latency   (scripts/evaluate_retrieval.py)
    chat       correctness / abstention / citations / groundedness /
               tokens / cost / error rate            (scripts/evaluate_chat.py)

Usage:
    uv run python scripts/evaluate.py                    # both suites
    uv run python scripts/evaluate.py --retrieval-only
    uv run python scripts/evaluate.py --chat-only --skip-ingest
    uv run python scripts/evaluate.py --save evals/results/my_run.json

Compare two runs (regression gate, exit 1 on quality drop):
    uv run python scripts/compare_eval.py evals/results/baseline.json evals/results/latest.json
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.main import APP_VERSION  # noqa: E402
from app.services.bm25_index import Bm25Index  # noqa: E402
from app.services.embeddings import Embedder  # noqa: E402
from app.services.vector_store import VectorStore  # noqa: E402
from scripts import evaluate_chat  # noqa: E402
from scripts.evaluate_retrieval import (  # noqa: E402
    DATASET_PATH,
    EVAL_BM25_PATH,
    EVAL_COLLECTION,
    evaluate as run_retrieval_suite,
    ingest_corpus,
    load_dataset,
    print_report as print_retrieval_report,
    reset_eval_collection,
)


def provenance() -> dict:
    """Which code + dataset produced this run (investigability)."""
    dataset_sha = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()[:16]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception:
        commit = None
    return {
        "app_version": APP_VERSION,
        "git_commit": commit,
        "dataset_sha256": dataset_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--no-hybrid", action="store_true")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Reuse the existing eval collection + term index",
    )
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--chat-only", action="store_true")
    parser.add_argument(
        "--save",
        default=str(PROJECT_ROOT / "evals" / "results" / "latest.json"),
        help="Where to write the combined results JSON ('' to skip)",
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
    generator = evaluate_chat.build_generator(settings)

    if not args.skip_ingest:
        reset_eval_collection(settings)
    store = VectorStore(settings.chroma_path, EVAL_COLLECTION)
    bm25 = Bm25Index(EVAL_BM25_PATH)
    if not args.skip_ingest:
        chunk_total = ingest_corpus(store, embedder, bm25)
        print(f"ingested {chunk_total} chunks\n")
    if hybrid and bm25.count() == 0:
        print("warning: eval term index empty; running vector-only")
        hybrid = False

    questions = load_dataset(DATASET_PATH)
    config = {
        "top_k": top_k,
        "hybrid": hybrid,
        "rrf_k": rrf_k if hybrid else None,
        "chat_model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "num_questions": len(questions),
    }
    results: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **provenance(),
        "config": config,
    }

    run_retrieval = not args.chat_only
    run_chat = not args.retrieval_only

    if run_retrieval:
        retrieval_summary = run_retrieval_suite(
            questions,
            embedder=embedder,
            store=store,
            bm25=bm25,
            top_k=top_k,
            hybrid=hybrid,
            rrf_k=rrf_k,
        )
        results["retrieval"] = retrieval_summary
        print_retrieval_report(retrieval_summary)

    if run_chat:
        chat_summary = evaluate_chat.run_chat_suite(
            questions,
            embedder=embedder,
            store=store,
            bm25=bm25,
            generator=generator,
            top_k=top_k,
            hybrid=hybrid,
            rrf_k=rrf_k,
        )
        results["chat"] = chat_summary
        evaluate_chat.print_chat_report(chat_summary)

    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nsaved results to {save_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
