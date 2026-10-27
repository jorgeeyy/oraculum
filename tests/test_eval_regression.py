"""Tests for scripts/compare_eval.py - the regression gate.

No API key needed: fixtures are plain result dicts/files.
"""

import json

from scripts.compare_eval import GATE_METRICS, compare, main, resolve


def nested(
    *,
    recall3=1.0,
    recall5=1.0,
    mrr=1.0,
    error_rate=0.0,
    abstention=1.0,
    fact=0.9,
    latency_mean=300.0,
) -> dict:
    return {
        "retrieval": {
            "answerable": {"recall@3": recall3, "recall@5": recall5, "mrr": mrr},
            "no_answer": {"max_top1_score": 0.03},
            "latency_ms": {"mean": latency_mean, "max": 400.0},
        },
        "chat": {
            "answerable": {
                "fact_score_mean": fact,
                "fact_all_rate": fact,
                "citation_coverage": fact,
                "grounded_sentence_pct": 95.0,
            },
            "no_answer": {"abstention_accuracy": abstention},
            "errors": {"rate": error_rate},
            "latency_ms": {"total_mean": 1000.0, "retrieval_mean": 300.0},
            "cost_usd": {"total": 0.05},
            "tokens": {"total": 1000},
        },
    }


def flat(**overrides) -> dict:
    """Legacy (pre-Phase-6) results shape: retrieval summary at top level."""
    data = {
        "answerable": {"recall@3": 1.0, "recall@5": 1.0, "mrr": 1.0},
        "no_answer": {"max_top1_score": 0.03},
        "latency_ms": {"mean": 300.0, "max": 400.0},
    }
    data.update(overrides)
    return data


def statuses(rows: dict[str, dict]) -> dict[str, str]:
    return {row["metric"]: row["status"] for row in rows}


class TestResolve:
    def test_nested_path(self) -> None:
        assert resolve(nested(), "retrieval.answerable.mrr") == 1.0

    def test_falls_back_to_flat_legacy_file(self) -> None:
        assert resolve(flat(), "retrieval.answerable.mrr") == 1.0
        assert resolve(flat(), "chat.errors.rate") is None

    def test_missing_everything_returns_none(self) -> None:
        assert resolve({}, "retrieval.answerable.mrr") is None


class TestGate:
    def test_drop_detected(self) -> None:
        rows = compare(nested(), nested(recall5=0.8, recall3=0.8, mrr=0.85), GATE_METRICS)
        regressed = {r["metric"] for r in rows if r["regressed"]}
        assert "retrieval.answerable.recall@5" in regressed
        assert "retrieval.answerable.mrr" in regressed

    def test_change_within_epsilon_passes(self) -> None:
        # 1.0 -> 0.99 is within eps=0.02
        rows = compare(nested(), nested(recall5=0.99, mrr=0.99), GATE_METRICS)
        assert not any(r["regressed"] for r in rows)
        assert statuses(rows)["retrieval.answerable.recall@5"] == "ok"

    def test_error_rate_rise_flagged(self) -> None:
        rows = compare(nested(), nested(error_rate=0.1), GATE_METRICS)
        row = next(r for r in rows if r["metric"] == "chat.errors.rate")
        assert row["regressed"] is True
        assert row["status"] == "REGRESSED"

    def test_abstention_regression_flagged(self) -> None:
        rows = compare(nested(), nested(abstention=0.5), GATE_METRICS)
        row = next(r for r in rows if r["metric"] == "chat.no_answer.abstention_accuracy")
        assert row["regressed"] is True

    def test_metric_missing_in_new_flagged(self) -> None:
        new = nested()
        del new["retrieval"]["answerable"]["mrr"]
        rows = compare(nested(), new, GATE_METRICS)
        row = next(r for r in rows if r["metric"] == "retrieval.answerable.mrr")
        assert row["status"] == "MISSING"
        assert row["regressed"] is True

    def test_metric_missing_in_old_is_new_not_regression(self) -> None:
        # legacy baseline has no chat section: chat metrics are simply NEW
        rows = compare(flat(), nested(), GATE_METRICS)
        assert not any(r["regressed"] for r in rows)
        assert statuses(rows)["chat.errors.rate"] == "NEW"

    def test_legacy_flat_baseline_compares_clean(self) -> None:
        rows = compare(flat(), nested(), GATE_METRICS)
        assert statuses(rows)["retrieval.answerable.recall@5"] == "ok"

    def test_latency_regression_is_relative(self) -> None:
        # 300 -> 400ms = +33% > 25% tolerance
        rows = compare(nested(), nested(latency_mean=400.0), GATE_METRICS)
        row = next(r for r in rows if r["metric"] == "retrieval.latency_ms.mean")
        assert row["regressed"] is True

    def test_improvement_not_flagged(self) -> None:
        rows = compare(nested(mrr=0.9), nested(mrr=1.0), GATE_METRICS)
        assert not any(r["regressed"] for r in rows)
        assert statuses(rows)["retrieval.answerable.mrr"] == "IMPROVED"

    def test_info_metrics_never_gate(self) -> None:
        rows = compare(
            nested(),
            {**nested(), "chat": {**nested()["chat"], "cost_usd": {"total": 99.0}}},
            GATE_METRICS,
        )
        cost = next(r for r in rows if r["metric"] == "chat.cost_usd.total")
        assert cost["regressed"] is False
        assert cost["gate"] is False


class TestCli:
    def _write(self, path, data) -> str:
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_exit_0_on_clean_compare(self, tmp_path) -> None:
        old = self._write(tmp_path / "old.json", nested())
        new = self._write(tmp_path / "new.json", nested())
        assert main([old, new]) == 0

    def test_exit_1_on_regression(self, tmp_path) -> None:
        old = self._write(tmp_path / "old.json", nested())
        new = self._write(tmp_path / "new.json", nested(recall5=0.5))
        assert main([old, new]) == 1

    def test_exit_1_on_missing_metric(self, tmp_path) -> None:
        old = self._write(tmp_path / "old.json", nested())
        broken = nested()
        del broken["retrieval"]["answerable"]["recall@5"]
        new = self._write(tmp_path / "new.json", broken)
        assert main([old, new]) == 1

    def test_exit_0_against_legacy_flat_file(self, tmp_path) -> None:
        old = self._write(tmp_path / "old.json", flat())
        new = self._write(tmp_path / "new.json", nested())
        assert main([old, new]) == 0

    def test_exit_2_on_unreadable_file(self, tmp_path) -> None:
        old = self._write(tmp_path / "old.json", nested())
        missing = str(tmp_path / "nope.json")
        assert main([old, missing]) == 2
