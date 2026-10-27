"""Regression gate: compare two evaluation result files metric-by-metric.

    uv run python scripts/compare_eval.py evals/results/baseline.json evals/results/latest.json

Resolves each gate metric by dotted path (e.g. "retrieval.answerable.mrr").
Files written before the nested Phase 6 format store the retrieval summary at
the top level, so unresolved paths fall back to stripping leading segments -
legacy flat results stay comparable.

Exit codes:
    0  no regressions (metrics may be equal, better, or absent in OLD)
    1  at least one metric regressed beyond tolerance, or is missing in NEW
    2  usage error / unreadable file
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Metric:
    path: str            # dotted path in the results JSON
    eps: float           # allowed adverse movement before flagging
    lower_is_better: bool = False
    relative: bool = False  # eps is a fraction of the OLD value (latency/cost)
    gate: bool = True    # False = reported for information only


GATE_METRICS: list[Metric] = [
    # retrieval suite
    Metric("retrieval.answerable.recall@3", 0.02),
    Metric("retrieval.answerable.recall@5", 0.02),
    Metric("retrieval.answerable.mrr", 0.02),
    Metric("retrieval.no_answer.max_top1_score", 0.02, lower_is_better=True),
    Metric("retrieval.latency_ms.mean", 0.25, lower_is_better=True, relative=True),
    # chat suite
    Metric("chat.answerable.fact_score_mean", 0.03),
    Metric("chat.answerable.fact_all_rate", 0.03),
    Metric("chat.answerable.citation_coverage", 0.03),
    Metric("chat.answerable.grounded_sentence_pct", 0.03),
    Metric("chat.no_answer.abstention_accuracy", 0.02),
    Metric("chat.errors.rate", 0.0, lower_is_better=True),
    Metric("chat.latency_ms.total_mean", 0.25, lower_is_better=True, relative=True),
    # information only (cost moves with usage, not quality)
    Metric("chat.cost_usd.total", 0.5, lower_is_better=True, relative=True, gate=False),
    Metric("chat.tokens.total", 0.0, lower_is_better=True, relative=True, gate=False),
]


def resolve(data: dict, dotted: str):
    """Read a dotted path; fall back to stripping leading segments (legacy flat files)."""
    parts = dotted.split(".")
    for start in range(len(parts)):
        node = data
        ok = True
        for key in parts[start:]:
            if not isinstance(node, dict) or key not in node:
                ok = False
                break
            node = node[key]
        if ok and not isinstance(node, (dict, list)):
            return node
    return None


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def delta_status(metric: Metric, old, new) -> tuple[str, bool]:
    """Return (status, is_regression)."""
    if old is None:
        return ("NEW", False)
    if new is None:
        return ("MISSING", True)
    delta = new - old
    if metric.relative:
        allowed = metric.eps * abs(old)
    else:
        allowed = metric.eps
    if metric.lower_is_better:
        regression = delta > allowed
        improved = delta < -allowed
    else:
        regression = delta < -allowed
        improved = delta > allowed
    if regression:
        return ("REGRESSED", True)
    if improved:
        return ("IMPROVED", False)
    return ("ok", False)


def compare(old_data: dict, new_data: dict, metrics: list[Metric]) -> list[dict]:
    rows = []
    for metric in metrics:
        old = resolve(old_data, metric.path)
        new = resolve(new_data, metric.path)
        status, regressed = delta_status(metric, old, new)
        rows.append(
            {
                "metric": metric.path,
                "gate": metric.gate,
                "old": old,
                "new": new,
                "status": status,
                "regressed": bool(regressed and metric.gate),
            }
        )
    return rows


def print_table(rows: list[dict]) -> None:
    width = max((len(r["metric"]) for r in rows), default=10)
    print(f"{'metric'.ljust(width)}  {'old':>10}  {'new':>10}  {'delta':>9}  status")
    print("-" * (width + 44))
    for row in rows:
        old, new = row["old"], row["new"]
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            delta = f"{new - old:+.4f}".rstrip("0").rstrip(".")
        else:
            delta = "-"
        flag = "" if row["gate"] else " (info)"
        print(
            f"{row['metric'].ljust(width)}  {_fmt(old):>10}  {_fmt(new):>10}  "
            f"{delta:>9}  {row['status']}{flag}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", help="Baseline results JSON (reference run)")
    parser.add_argument("new", help="Candidate results JSON (current run)")
    args = parser.parse_args(argv)

    try:
        old_data = json.loads(Path(args.old).read_text(encoding="utf-8"))
        new_data = json.loads(Path(args.new).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read results file: {exc}", file=sys.stderr)
        return 2

    rows = compare(old_data, new_data, GATE_METRICS)
    print(f"comparing:\n  old: {args.old}\n  new: {args.new}\n")
    print_table(rows)

    regressions = [r for r in rows if r["regressed"]]
    if regressions:
        print(f"\nFAIL: {len(regressions)} regression(s):")
        for row in regressions:
            print(f"  - {row['metric']}: {_fmt(row['old'])} -> {_fmt(row['new'])}")
        return 1
    print("\nOK: no regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
