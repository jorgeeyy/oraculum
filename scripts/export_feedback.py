"""Export thumbs feedback (chat_messages.feedback) for analysis.

Read-only: prints one row per rated assistant answer, newest first.

    uv run python scripts/export_feedback.py                  # JSON lines
    uv run python scripts/export_feedback.py --rating down    # one bucket
    uv run python scripts/export_feedback.py --format csv > feedback.csv

Exit code 0 on success; 1 when the database is unreachable.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import repositories  # noqa: E402
from app.db.session import get_session  # noqa: E402

CSV_FIELDS = [
    "message_id",
    "conversation_id",
    "feedback",
    "status",
    "model",
    "created_at",
    "answer",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rating",
        choices=("up", "down"),
        help="only export one bucket (default: both)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="json = one object per line (default); csv = header + rows",
    )
    args = parser.parse_args()

    try:
        # get_session() is a FastAPI-style generator dependency: prime it,
        # then close the generator to run its cleanup.
        sessions = get_session()
        session = next(sessions)
        try:
            rows = repositories.list_feedback(session, rating=args.rating)
        finally:
            sessions.close()
    except Exception as exc:  # noqa: BLE001 - surface config/DB errors cleanly
        print(f"export failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    else:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))

    up = sum(1 for row in rows if row["feedback"] == "up")
    down = len(rows) - up
    print(f"# {len(rows)} rated answers ({up} up, {down} down)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
