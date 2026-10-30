import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { ApiError, api } from "../../api/client";
import type { FeedbackItem } from "../../api/types";

type Filter = "all" | "up" | "down";

const FILTERS: readonly Filter[] = ["all", "up", "down"];

function truncate(text: string, max = 320): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export default function AdminFeedbackPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [rows, setRows] = useState<FeedbackItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRows(null);
    try {
      setRows(
        await api.adminFeedback(filter === "all" ? undefined : filter),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load.");
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Feedback inbox</h2>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 rounded-lg border border-line bg-panel p-0.5">
            {FILTERS.map((value) => (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                  filter === value
                    ? "bg-surface text-ink ring-1 ring-line"
                    : "text-muted hover:text-ink"
                }`}
              >
                {value === "all" ? "All" : value === "up" ? "👍 Up" : "👎 Down"}
              </button>
            ))}
          </div>
          <button
            onClick={() => void load()}
            className="rounded-lg border border-line bg-surface p-1.5 text-muted hover:bg-elevated hover:text-ink"
            aria-label="Refresh"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {!rows ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
          <Loader2 size={17} className="animate-spin text-accent" />
          Loading feedback…
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line py-14 text-center text-sm text-faint">
          No rated answers yet. Thumbs on chat replies land here.
        </div>
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => (
            <li
              key={row.message_id}
              className="rounded-2xl border border-line bg-panel p-4"
            >
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                <span
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${
                    row.feedback === "up"
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                      : "bg-red-500/10 text-red-600 dark:text-red-400"
                  }`}
                >
                  {row.feedback === "up" ? (
                    <ThumbsUp size={11} />
                  ) : (
                    <ThumbsDown size={11} />
                  )}
                  {row.feedback}
                </span>
                <span className="font-mono text-[11px]">
                  {row.created_at.replace("T", " ").slice(0, 16)}
                </span>
                {row.model && <span className="text-faint">{row.model}</span>}
              </div>
              <p className="text-sm leading-6 text-ink">
                {truncate(row.answer)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
