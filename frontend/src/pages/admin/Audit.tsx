import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { ApiError, api } from "../../api/client";
import type { AuditEvent } from "../../api/types";

const ACTION_STYLES: Record<string, string> = {
  "user.update": "bg-accent/10 text-accent",
  "user.password_reset": "bg-amber-500/10 text-amber-600 dark:text-amber-400",
};

function fmtTime(value: string): string {
  return value.replace("T", " ").slice(0, 19);
}

export default function AdminAuditPage() {
  const [rows, setRows] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRows(null);
    try {
      setRows(await api.adminAudit());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Audit log</h2>
        <button
          onClick={() => void load()}
          className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-elevated hover:text-ink"
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {!rows ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
          <Loader2 size={17} className="animate-spin text-accent" />
          Loading audit events…
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line py-14 text-center text-sm text-faint">
          No audit events yet — admin actions appear here as they happen.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-line bg-panel">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-4 py-2.5 font-medium">Time</th>
                <th className="px-4 py-2.5 font-medium">Actor</th>
                <th className="px-4 py-2.5 font-medium">Action</th>
                <th className="px-4 py-2.5 font-medium">Target</th>
                <th className="px-4 py-2.5 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-line/60 last:border-0 align-top">
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs text-muted">
                    {fmtTime(row.created_at)}
                  </td>
                  <td className="px-4 py-2.5 text-xs">
                    <div>{row.actor_label}</div>
                    {row.ip && <div className="text-faint">{row.ip}</div>}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                        ACTION_STYLES[row.action] ?? "bg-surface text-muted ring-1 ring-line"
                      }`}
                    >
                      {row.action}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted">
                    {row.target_type ?? "—"}
                    {row.target_id && (
                      <div className="max-w-[120px] truncate font-mono text-[11px] text-faint">
                        {row.target_id}
                      </div>
                    )}
                  </td>
                  <td className="max-w-[240px] px-4 py-2.5">
                    <details className="text-xs text-muted">
                      {row.detail ? (
                        <summary className="cursor-pointer select-none">
                          view
                        </summary>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                      {row.detail && (
                        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-surface p-2 font-mono text-[11px]">
                          {JSON.stringify(row.detail, null, 2)}
                        </pre>
                      )}
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
