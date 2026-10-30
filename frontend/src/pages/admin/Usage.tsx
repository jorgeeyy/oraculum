import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "../../api/client";
import type { AdminUsage } from "../../api/types";

const RANGES = [7, 14, 30] as const;

const CHART_W = 640;
const CHART_H = 160;
const PAD = 8;

/** Zero-state when the window has no activity at all. */
function EmptyChart() {
  return (
    <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-line text-sm text-faint">
      No chat activity in this window yet.
    </div>
  );
}

/** Minimal hand-rolled SVG bar chart (no chart library in the bundle). */
function BarChart({ daily }: { daily: AdminUsage["daily"] }) {
  const max = Math.max(...daily.map((d) => d.answers), 1);
  const n = daily.length;
  const slot = (CHART_W - PAD * 2) / n;
  const barW = Math.max(slot * 0.6, 3);
  const labelEvery = Math.max(Math.ceil(n / 7), 1); // avoid crowding x labels

  return (
    <div>
      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="w-full"
        role="img"
        aria-label="Answers per day"
      >
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line
            key={f}
            x1={PAD}
            x2={CHART_W - PAD}
            y1={CHART_H - 24 - (CHART_H - 40) * f}
            y2={CHART_H - 24 - (CHART_H - 40) * f}
            className="stroke-line"
            strokeDasharray="3 4"
          />
        ))}
        {daily.map((d, i) => {
          const h = Math.round(((CHART_H - 40) * (d.answers || 0)) / max);
          const x = PAD + slot * i + (slot - barW) / 2;
          return (
            <g key={d.day ?? i}>
              <rect
                x={x}
                y={CHART_H - 24 - h}
                width={barW}
                height={Math.max(h, d.answers > 0 ? 2 : 0)}
                rx={2}
                className="fill-accent"
              >
                <title>{`${d.day ?? "?"}: ${d.answers} answers`}</title>
              </rect>
              {i % labelEvery === 0 && d.day && (
                <text
                  x={x + barW / 2}
                  y={CHART_H - 8}
                  textAnchor="middle"
                  className="fill-faint"
                  fontSize="10"
                >
                  {d.day.slice(5)} {/* MM-DD */}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-right text-xs text-faint">
        peak: {max} answers/day
      </p>
    </div>
  );
}

export default function AdminUsagePage() {
  const [days, setDays] = useState<number>(14);
  const [data, setData] = useState<AdminUsage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    api
      .adminUsage(days)
      .then((value) => !cancelled && setData(value))
      .catch(
        (err: Error) => !cancelled && setError(err.message || "Failed to load."),
      );
    return () => {
      cancelled = true;
    };
  }, [days]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Chat usage</h2>
        <div className="flex items-center gap-1 rounded-lg border border-line bg-panel p-0.5">
          {RANGES.map((range) => (
            <button
              key={range}
              onClick={() => setDays(range)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                days === range
                  ? "bg-surface text-ink ring-1 ring-line"
                  : "text-muted hover:text-ink"
              }`}
            >
              {range}d
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Answers (lifetime)", value: data?.totals.answers },
          { label: "👍 Up", value: data?.totals.feedback_up },
          { label: "👎 Down", value: data?.totals.feedback_down },
        ].map(({ label, value }) => (
          <div key={label} className="rounded-2xl border border-line bg-panel p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-faint">
              {label}
            </p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">
              {value === undefined ? "—" : value.toLocaleString()}
            </p>
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-line bg-panel p-4">
        {error ? (
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        ) : !data ? (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
            <Loader2 size={17} className="animate-spin text-accent" />
            Loading usage…
          </div>
        ) : data.daily.length === 0 ? (
          <EmptyChart />
        ) : (
          <BarChart daily={data.daily} />
        )}
      </div>

      <p className="text-xs text-faint">
        Usage is counted from stored answers. Token-spend tracking arrives
        with the cost-governance workstream.
      </p>
    </div>
  );
}
