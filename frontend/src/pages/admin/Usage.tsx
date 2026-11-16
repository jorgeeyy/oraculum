import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "../../api/client";
import type { AdminUsage, UsageRollup } from "../../api/types";

const RANGES = [7, 14, 30] as const;

const CHART_W = 640;
const CHART_H = 160;
const PAD = 8;

/** USD with at least cents; small amounts keep up to 4 decimals. */
function fmtUsd(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  });
}

/** Zero-state when the window has no activity at all. */
function EmptyChart({ message }: { message: string }) {
  return (
    <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-line text-sm text-faint">
      {message}
    </div>
  );
}

interface ChartPoint {
  day: string | null;
  value: number;
  label: string; // tooltip text, e.g. "2 answers" / "$0.0042"
}

/** Minimal hand-rolled SVG bar chart (no chart library in the bundle). */
function BarChart({
  series,
  ariaLabel,
  peak,
}: {
  series: ChartPoint[];
  ariaLabel: string;
  peak: string;
}) {
  const max = Math.max(...series.map((p) => p.value), 1);
  const n = series.length;
  const slot = (CHART_W - PAD * 2) / n;
  const barW = Math.max(slot * 0.6, 3);
  const labelEvery = Math.max(Math.ceil(n / 7), 1); // avoid crowding x labels

  return (
    <div>
      <svg
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        className="w-full"
        role="img"
        aria-label={ariaLabel}
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
        {series.map((p, i) => {
          const h = Math.round(((CHART_H - 40) * p.value) / max);
          const x = PAD + slot * i + (slot - barW) / 2;
          return (
            <g key={p.day ?? i}>
              <rect
                x={x}
                y={CHART_H - 24 - h}
                width={barW}
                height={Math.max(h, p.value > 0 ? 2 : 0)}
                rx={2}
                className="fill-accent"
              >
                <title>{`${p.day ?? "?"}: ${p.label}`}</title>
              </rect>
              {i % labelEvery === 0 && p.day && (
                <text
                  x={x + barW / 2}
                  y={CHART_H - 8}
                  textAnchor="middle"
                  className="fill-faint"
                  fontSize="10"
                >
                  {p.day.slice(5)} {/* MM-DD */}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-right text-xs text-faint">peak: {peak}</p>
    </div>
  );
}

/** Spend ranking table (by account or by model) for the window. */
function RollupTable({
  title,
  keyHeader,
  rows,
  empty,
}: {
  title: string;
  keyHeader: string;
  rows: UsageRollup[];
  empty: string;
}) {
  return (
    <div className="rounded-2xl border border-line bg-panel p-4">
      <h3 className="text-xs font-medium uppercase tracking-wide text-faint">
        {title}
      </h3>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-faint">{empty}</p>
      ) : (
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-faint">
              <th className="py-1.5 pr-2 font-medium">{keyHeader}</th>
              <th className="py-1.5 text-right font-medium">Requests</th>
              <th className="py-1.5 text-right font-medium">Spend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                className="border-b border-line/60 last:border-0"
              >
                <td className="max-w-[240px] truncate py-1.5 pr-2 font-medium">
                  {row.key}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {row.requests.toLocaleString()}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {fmtUsd(row.spend_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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

  // Window rollups (the `totals` block is lifetime; the chart is the window).
  const windowRequests = data?.daily.reduce((sum, d) => sum + d.requests, 0);
  const windowTokensIn = data?.daily.reduce((sum, d) => sum + d.tokens_in, 0);
  const windowTokensOut = data?.daily.reduce((sum, d) => sum + d.tokens_out, 0);

  const cards: { label: string; value: string | number }[] = [
    { label: "Answers (lifetime)", value: data?.totals.answers ?? "—" },
    { label: "👍 Up", value: data?.totals.feedback_up ?? "—" },
    { label: "👎 Down", value: data?.totals.feedback_down ?? "—" },
    { label: `Spend (${days}d)`, value: data ? fmtUsd(data.spend_usd) : "—" },
    {
      label: `Requests (${days}d)`,
      value: windowRequests ?? "—",
    },
    {
      label: `Tokens (${days}d)`,
      value: data
        ? `${(windowTokensIn ?? 0).toLocaleString()} in / ${(windowTokensOut ?? 0).toLocaleString()} out`
        : "—",
    },
  ];

  const answerSeries: ChartPoint[] = (data?.daily ?? []).map((d) => ({
    day: d.day,
    value: d.answers,
    label: `${d.answers} answers`,
  }));
  const spendSeries: ChartPoint[] = (data?.daily ?? []).map((d) => ({
    day: d.day,
    value: d.spend_usd,
    label: fmtUsd(d.spend_usd),
  }));
  const answerMax = Math.max(...answerSeries.map((p) => p.value), 1);
  const spendMax = Math.max(...spendSeries.map((p) => p.value), 0);

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
        {cards.map(({ label, value }) => (
          <div key={label} className="rounded-2xl border border-line bg-panel p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-faint">
              {label}
            </p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">
              {typeof value === "number" ? value.toLocaleString() : value}
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
        ) : (
          <div className="space-y-6">
            <section>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-faint">
                Answers per day
              </h3>
              {data.daily.length === 0 ? (
                <EmptyChart message="No chat activity in this window yet." />
              ) : (
                <BarChart
                  series={answerSeries}
                  ariaLabel="Answers per day"
                  peak={`${answerMax} answers/day`}
                />
              )}
            </section>
            <section>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-faint">
                Estimated spend per day
              </h3>
              {data.spend_usd > 0 ? (
                <BarChart
                  series={spendSeries}
                  ariaLabel="Estimated spend per day"
                  peak={`${fmtUsd(spendMax)}/day`}
                />
              ) : (
                <EmptyChart message="No spend recorded in this window." />
              )}
            </section>
          </div>
        )}
      </div>

      {data && !error && (
        <div className="grid gap-4 md:grid-cols-2">
          <RollupTable
            title="Spend by account"
            keyHeader="Account"
            rows={data.by_user}
            empty="No attributed spend in this window."
          />
          <RollupTable
            title="Spend by model"
            keyHeader="Model"
            rows={data.by_model}
            empty="No spend recorded in this window."
          />
        </div>
      )}

      <p className="text-xs text-faint">
        Spend is estimated from token counts × list prices (single source in
        app/services/pricing.py); requests with unknown models count as $0.00.
      </p>
    </div>
  );
}
