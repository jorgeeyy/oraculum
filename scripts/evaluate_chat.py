"""Chat-level evaluation: answer quality, abstention, citations, cost.

Runs the real retrieval + generation pipeline directly (no server needed)
over the eval corpus (documents_eval Chroma collection + data/bm25_eval.db)
and scores each question in evals/dataset.jsonl deterministically:

- answer correctness  = fraction of expected_facts present in the answer
  (a fact may contain phrasing alternates separated by "|")
- abstention accuracy = no_answer questions must NOT be answered
- citation coverage   = fraction of relevant docs cited by the answer
- groundedness        = structural proxy: answer sentences must share
  content words with the retrieved evidence (LLM judge deferred)
- tokens / cost / latency / error rate

Usage (prefer scripts/evaluate.py, which runs retrieval + chat in one go):
    uv run python scripts/evaluate_chat.py
    uv run python scripts/evaluate_chat.py --skip-ingest
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.main import APP_VERSION  # noqa: E402
from app.services.bm25_index import Bm25Index  # noqa: E402
from app.services.embeddings import Embedder  # noqa: E402
from app.services.generation import Generator  # noqa: E402
from app.services.retrieval import retrieve_documents  # noqa: E402
from app.services.vector_store import VectorStore  # noqa: E402
from scripts.evaluate_retrieval import (  # noqa: E402
    EVAL_BM25_PATH,
    EVAL_COLLECTION,
    load_dataset,
    reset_eval_collection,
    ingest_corpus,
    DATASET_PATH,
)

# USD per 1M tokens (approximate OpenAI list prices; update when prices move).
_PRICES_PER_1M = {
    "text-embedding-3-small": (0.02, 0.0),
    "gpt-4o-mini": (0.15, 0.60),
}

_WORD_RE = re.compile(r"[a-z0-9£]+")
_STOPWORDS = frozenset(
    """the and that with from have been would could should may your their this
    which what when where who how not are for any must its into than then also
    if all one two there here out get use used using can will per""".split()
)


def content_tokens(text: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall(text.lower())
        if len(word) >= 4 and word not in _STOPWORDS
    }


def fact_matches(answer: str, fact: str) -> bool:
    """A fact passes if any of its '|'-separated alternates appears in the answer."""
    haystack = answer.casefold()
    return any(
        alternate.strip().casefold() in haystack
        for alternate in fact.split("|")
        if alternate.strip()
    )


def fact_score(answer: str, expected_facts: list[str]) -> tuple[int, int]:
    """(facts present, facts declared)."""
    passed = sum(1 for fact in expected_facts if fact_matches(answer, fact))
    return passed, len(expected_facts)


def sentence_grounded(sentence: str, evidence: str) -> bool:
    """Structural groundedness: most content words of the sentence appear in evidence."""
    tokens = content_tokens(sentence)
    if len(tokens) < 2:  # "Yes." / "No." - nothing to check
        return True
    evidence_tokens = content_tokens(evidence)
    overlap = len(tokens & evidence_tokens)
    return overlap >= 2 or overlap >= len(tokens) / 2


def grounded_sentence_pct(answer: str, evidence: str) -> float:
    sentences = [
        part.strip()
        for part in re.split(r"[.!?\n]+", answer)
        if part.strip()
    ]
    if not sentences:
        return 100.0
    grounded = sum(1 for s in sentences if sentence_grounded(s, evidence))
    return round(100.0 * grounded / len(sentences), 1)


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    prices = _PRICES_PER_1M.get(model)
    if prices is None:
        return None
    in_price, out_price = prices
    return round(
        tokens_in / 1e6 * in_price + tokens_out / 1e6 * out_price, 6
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def run_chat_suite(
    questions: list[dict],
    *,
    embedder: Embedder,
    store: VectorStore,
    bm25: Bm25Index,
    generator: Generator,
    top_k: int,
    hybrid: bool,
    rrf_k: int,
) -> dict:
    """Evaluate every dataset question end-to-end; returns the chat summary."""
    rows: list[dict] = []
    evidence_texts: dict[str, str] = {}

    for item in questions:
        question = item["question"]
        row: dict = {
            "question": question,
            "type": item["type"],
            "latency_ms": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
        }
        try:
            embed_before = getattr(embedder, "tokens_in", 0)
            outcome = retrieve_documents(
                question,
                embedder=embedder,
                store=store,
                top_k=top_k,
                bm25=bm25,
                hybrid=hybrid,
                rrf_k=rrf_k,
            )
            row["retrieval_ms"] = round(outcome.elapsed_ms, 1)
            row["embed_tokens"] = max(
                0, getattr(embedder, "tokens_in", 0) - embed_before
            )
            evidence = "\n".join(hit.text for hit in outcome.results)
            generation = generator.generate(question, outcome.results)
            row.update(
                status=generation.status,
                answer=generation.answer,
                retrieval_ms=row["retrieval_ms"],
                generation_ms=round(generation.elapsed_ms, 1),
                latency_ms=round(
                    outcome.elapsed_ms + generation.elapsed_ms, 1
                ),
                tokens_in=generation.tokens_in,
                tokens_out=generation.tokens_out,
                sources=[
                    {"filename": s.filename, "page": s.page}
                    for s in generation.sources
                ],
            )
            evidence_texts[question] = evidence
        except Exception as exc:  # noqa: BLE001 - eval records failures as rows
            row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue

        expected = item.get("expected_facts") or []
        if expected:
            passed, total = fact_score(generation.answer, expected)
            row["facts"] = f"{passed}/{total}"
            row["facts_ok"] = passed == total

        if item["type"] == "no_answer":
            row["abstained"] = generation.status != "answered"
        elif generation.status == "answered":
            relevant = {
                (r["filename"], r["page"]) for r in item["relevant"]
            }
            cited = {(s["filename"], s["page"]) for s in row["sources"]}
            if relevant:
                row["citation_coverage"] = round(
                    len(relevant & cited) / len(relevant), 3
                )
            row["grounded_pct"] = grounded_sentence_pct(
                generation.answer, evidence_texts[question]
            )
        else:
            row["abstained_on_answerable"] = (
                generation.status != "answered"
            )

        rows.append(row)

    answerable = [r for r in rows if r["type"] != "no_answer"]
    no_answer = [r for r in rows if r["type"] == "no_answer"]
    errors = [r for r in rows if "error" in r]
    answered = [r for r in answerable if r.get("status") == "answered"]
    fact_rows = [r for r in answered if "facts" in r]

    tokens_in = sum(r.get("embed_tokens", 0) + r.get("tokens_in", 0) for r in rows)
    tokens_out = sum(r.get("tokens_out", 0) for r in rows)
    embed_cost = cost_usd(embedder.model, sum(r.get("embed_tokens", 0) for r in rows), 0)
    gen_cost = cost_usd(generator.model, sum(r.get("tokens_in", 0) for r in rows), tokens_out)

    correct_abstentions = sum(1 for r in no_answer if r.get("abstained"))
    summary = {
        "model": generator.model,
        "embedding_model": embedder.model,
        "num_questions": len(rows),
        "errors": {
            "count": len(errors),
            "rate": round(len(errors) / len(rows), 3) if rows else 0.0,
            "rows": [
                {"question": r["question"], "error": r["error"]}
                for r in errors
            ],
        },
        "answerable": {
            "count": len(answerable),
            "answered": len(answered),
            "abstained": sum(
                1 for r in answerable if r.get("abstained_on_answerable")
            ),
            "fact_questions": len(fact_rows),
            "fact_score_mean": _mean(
                [
                    int(r["facts"].split("/")[0]) / int(r["facts"].split("/")[1])
                    for r in fact_rows
                ]
            ),
            "fact_all_rate": _mean([1.0 if r.get("facts_ok") else 0.0 for r in fact_rows]),
            "citation_coverage": _mean(
                [r.get("citation_coverage", 0.0) for r in answered]
            ),
            "grounded_sentence_pct": _mean(
                [r.get("grounded_pct", 0.0) for r in answered]
            ),
        },
        "no_answer": {
            "count": len(no_answer),
            "correct_abstentions": correct_abstentions,
            "abstention_accuracy": round(
                correct_abstentions / len(no_answer), 3
            )
            if no_answer
            else None,
            "hallucinated": [
                r["question"]
                for r in no_answer
                if "error" not in r and not r.get("abstained")
            ],
        },
        "tokens": {"in": tokens_in, "out": tokens_out, "total": tokens_in + tokens_out},
        "cost_usd": {
            "generation": gen_cost,
            "embedding": embed_cost,
            "total": round((gen_cost or 0.0) + (embed_cost or 0.0), 6)
            if (gen_cost is not None or embed_cost is not None)
            else None,
        },
        "latency_ms": {
            "retrieval_mean": _mean([r.get("retrieval_ms", 0.0) for r in rows]),
            "generation_mean": _mean([r.get("generation_ms", 0.0) for r in rows]),
            "total_mean": _mean([r.get("latency_ms", 0.0) for r in rows]),
        },
        "per_question": rows,
    }
    return summary


def print_chat_report(summary: dict) -> None:
    a = summary["answerable"]
    n = summary["no_answer"]
    e = summary["errors"]
    c = summary["cost_usd"]
    lat = summary["latency_ms"]
    print()
    print("chat evaluation")
    print(
        f"  answerable ({a['count']}): answered={a['answered']} "
        f"abstained={a['abstained']} facts={a['fact_score_mean']} "
        f"(all-correct {a['fact_all_rate']}) citations={a['citation_coverage']} "
        f"grounded={a['grounded_sentence_pct']}%"
    )
    abstention = n["abstention_accuracy"]
    print(
        f"  no-answer ({n['count']}): abstentions={n['correct_abstentions']}"
        + (f" accuracy={abstention}" if abstention is not None else "")
    )
    for question in n["hallucinated"]:
        print(f"    HALLUCINATED: {question}")
    print(f"  errors: {e['count']} ({e['rate']})")
    print(
        f"  tokens: in={summary['tokens']['in']} out={summary['tokens']['out']} "
        f"cost=${c['total']}"
    )
    print(
        f"  latency: retrieval_mean={lat['retrieval_mean']}ms "
        f"generation_mean={lat['generation_mean']}ms"
    )


def build_generator(settings) -> Generator:
    return Generator(
        api_key=settings.openai_api_key,
        model=settings.chat_model,
        max_tokens=settings.chat_max_tokens,
        temperature=settings.chat_temperature,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--no-hybrid", action="store_true")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument(
        "--save",
        default=str(PROJECT_ROOT / "evals" / "results" / "chat.json"),
        help="Where to write results JSON ('' to skip)",
    )
    args = parser.parse_args()

    settings = get_settings()
    top_k = args.top_k or settings.retrieval_top_k
    hybrid = settings.retrieval_hybrid and not args.no_hybrid
    embedder = Embedder(
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        batch_size=settings.embedding_batch_size,
        max_retries=settings.embedding_max_retries,
    )
    generator = build_generator(settings)

    if not args.skip_ingest:
        reset_eval_collection(settings)
    store = VectorStore(settings.chroma_path, EVAL_COLLECTION)
    bm25 = Bm25Index(EVAL_BM25_PATH)
    if not args.skip_ingest:
        ingest_corpus(store, embedder, bm25)
    if hybrid and bm25.count() == 0:
        print("warning: eval term index empty; running vector-only")
        hybrid = False

    questions = load_dataset(DATASET_PATH)
    summary = run_chat_suite(
        questions,
        embedder=embedder,
        store=store,
        bm25=bm25,
        generator=generator,
        top_k=top_k,
        hybrid=hybrid,
        rrf_k=settings.retrieval_rrf_k,
    )
    summary["app_version"] = APP_VERSION
    print_chat_report(summary)

    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nsaved results to {save_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
