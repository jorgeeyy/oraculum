import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "../../api/client";
import type { AdminOverview } from "../../api/types";

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

export default function AdminOverviewPage() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .adminOverview()
      .then((value) => !cancelled && setData(value))
      .catch(
        (err: Error) => !cancelled && setError(err.message || "Failed to load."),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-600 dark:text-red-400">
        {error}
      </p>
    );
  }
  if (!data) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
        <Loader2 size={17} className="animate-spin text-accent" />
        Loading overview…
      </div>
    );
  }

  const cards = [
    { label: "Documents", value: fmt(data.documents), sub: `${fmt(data.chunks)} chunks` },
    { label: "Conversations", value: fmt(data.conversations), sub: `${fmt(data.messages)} messages` },
    { label: "Accounts", value: fmt(data.users), sub: `${fmt(data.active_admins)} active admins` },
    { label: "Version", value: data.version, sub: data.git_commit ? `commit ${data.git_commit}` : "no git" },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {cards.map(({ label, value, sub }) => (
          <div key={label} className="rounded-2xl border border-line bg-panel p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-faint">
              {label}
            </p>
            <p className="mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
            <p className="mt-0.5 text-xs text-muted">{sub}</p>
          </div>
        ))}
      </div>
      <div className="rounded-2xl border border-line bg-panel px-4 py-3 text-sm text-muted">
        Answer cache (semantic):{" "}
        <span className="font-medium text-ink">
          {data.chat_cache_enabled ? "enabled" : "disabled"}
        </span>
      </div>
    </div>
  );
}
