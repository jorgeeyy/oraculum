"""Schema and corpus validation for evals/dataset.jsonl.

Guards the evaluation contract: every question is well-formed, answerable
questions point at documents/pages that really exist (verified through the
real extraction + chunking pipeline), no-answer questions carry no relevance
or facts, and declared expected facts actually appear in the corpus - a fact
that is not in the source can never be scored correctly.
"""

import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.services.chunking import chunk_pages
from app.services.text_extraction import extract_pages
from scripts.evaluate_retrieval import CORPUS, DATASET_PATH, load_dataset

ALLOWED_TYPES = {"factual", "multi_doc", "distractor", "no_answer"}


def corpus_keys() -> set[tuple[str, int]]:
    """(filename, page) pairs that exist after real extraction + chunking."""
    keys: set[tuple[str, int]] = set()
    for relative in CORPUS:
        path = PROJECT_ROOT / relative
        pages = extract_pages(path.name, path.read_bytes())
        chunks = chunk_pages(pages, document_id="x", filename=path.name)
        assert chunks, f"{relative} produced no chunks"
        for chunk in chunks:
            keys.add((chunk.filename, chunk.page))
    return keys


def corpus_text() -> str:
    parts = []
    for relative in CORPUS:
        path = PROJECT_ROOT / relative
        pages = extract_pages(path.name, path.read_bytes())
        parts.extend(page.text for page in pages)
    return "\n".join(parts).casefold()


def load_records() -> list[dict]:
    # Raw JSON parse (load_dataset already exits hard on malformed JSON).
    records = []
    with DATASET_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


class TestDatasetStructure:
    def test_load_dataset_parses(self) -> None:
        records = load_dataset(DATASET_PATH)
        assert records == load_records()

    def test_question_count_in_range(self) -> None:
        records = load_records()
        assert 30 <= len(records) <= 50, f"got {len(records)} questions"

    def test_questions_unique(self) -> None:
        questions = [r["question"] for r in load_records()]
        assert len(questions) == len(set(questions))

    def test_required_fields_and_types(self) -> None:
        for record in load_records():
            assert isinstance(record["question"], str) and record["question"].strip()
            assert record["type"] in ALLOWED_TYPES, record["question"]
            assert isinstance(record["relevant"], list)
            for ref in record["relevant"]:
                assert isinstance(ref.get("filename"), str)
                assert isinstance(ref.get("page"), int) and ref["page"] >= 1

    def test_expected_facts_format(self) -> None:
        for record in load_records():
            facts = record.get("expected_facts", [])
            assert isinstance(facts, list)
            for fact in facts:
                assert isinstance(fact, str) and fact.strip()
                # '|'-separated alternates; none may be empty
                assert all(alt.strip() for alt in fact.split("|")), fact


class TestRelevanceContract:
    def test_no_answer_has_no_relevance_or_facts(self) -> None:
        for record in load_records():
            if record["type"] == "no_answer":
                assert record["relevant"] == [], record["question"]
                assert not record.get("expected_facts"), record["question"]

    def test_answerable_has_relevant_docs(self) -> None:
        for record in load_records():
            if record["type"] != "no_answer":
                assert record["relevant"], record["question"]

    def test_relevant_pages_exist_in_corpus(self) -> None:
        valid = corpus_keys()
        for record in load_records():
            for ref in record["relevant"]:
                key = (ref["filename"], ref["page"])
                assert key in valid, f"{record['question']}: {key} not in corpus"

    def test_no_answer_count_matches_plan(self) -> None:
        records = load_records()
        no_answer = [r for r in records if r["type"] == "no_answer"]
        assert 8 <= len(no_answer) <= 12, f"got {len(no_answer)} no-answer"


class TestExpectedFacts:
    def test_every_fact_alternate_exists_in_corpus(self) -> None:
        text = corpus_text()
        missing = []
        for record in load_records():
            for fact in record.get("expected_facts", []):
                alternates = [a.strip().casefold() for a in fact.split("|")]
                if not any(alt in text for alt in alternates):
                    missing.append((record["question"], fact))
        assert not missing, f"facts not found in corpus: {missing}"

    def test_no_answer_questions_have_no_facts(self) -> None:
        # redundant with the contract test, but cheap and high-value
        for record in load_records():
            if record["type"] == "no_answer":
                assert not record.get("expected_facts")
