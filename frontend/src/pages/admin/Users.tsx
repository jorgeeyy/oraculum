import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, ShieldCheck, ShieldUser } from "lucide-react";
import { ApiError, api } from "../../api/client";
import type { AdminUser } from "../../api/types";
import { useAuth } from "../../auth/AuthProvider";

function fmtDate(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleDateString();
}

function toDraft(value: number | null): string {
  return value === null ? "" : String(value);
}

/**
 * Per-user budget overrides: two small inputs + Save. Empty follows the
 * global default; 0 disables that window. Local state so an unrelated
 * table refresh doesn't clobber half-typed values.
 */
function BudgetCell({
  user,
  disabled,
  onSave,
}: {
  user: AdminUser;
  disabled: boolean;
  onSave: (daily: number | null, monthly: number | null) => Promise<void>;
}) {
  const [daily, setDaily] = useState(toDraft(user.daily_budget_usd));
  const [monthly, setMonthly] = useState(toDraft(user.monthly_budget_usd));
  const [localError, setLocalError] = useState<string | null>(null);

  const dirty =
    daily !== toDraft(user.daily_budget_usd) ||
    monthly !== toDraft(user.monthly_budget_usd);

  const save = async () => {
    const parse = (raw: string): number | null | "invalid" => {
      const trimmed = raw.trim();
      if (trimmed === "") return null;
      const value = Number(trimmed);
      return Number.isFinite(value) && value >= 0 ? value : "invalid";
    };
    const parsedDaily = parse(daily);
    const parsedMonthly = parse(monthly);
    if (parsedDaily === "invalid" || parsedMonthly === "invalid") {
      setLocalError("Use USD ≥ 0 (empty = global).");
      return;
    }
    setLocalError(null);
    await onSave(parsedDaily, parsedMonthly);
  };

  const inputClass =
    "w-20 rounded-lg border border-line bg-surface px-2 py-1 text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none disabled:opacity-50";

  return (
    <div className="flex items-center gap-1.5">
      <input
        type="text"
        inputMode="decimal"
        autoComplete="off"
        aria-label={`Daily budget (USD) for ${user.email}`}
        placeholder="daily"
        value={daily}
        disabled={disabled}
        onChange={(event) => setDaily(event.target.value)}
        className={inputClass}
      />
      <input
        type="text"
        inputMode="decimal"
        autoComplete="off"
        aria-label={`Monthly budget (USD) for ${user.email}`}
        placeholder="monthly"
        value={monthly}
        disabled={disabled}
        onChange={(event) => setMonthly(event.target.value)}
        className={inputClass}
      />
      <button
        disabled={disabled || !dirty}
        onClick={() => void save()}
        className="rounded-lg border border-line bg-surface px-2 py-1 text-xs text-muted hover:bg-elevated hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
      >
        Save
      </button>
      {localError && (
        <span className="whitespace-nowrap text-xs text-red-600 dark:text-red-400">
          {localError}
        </span>
      )}
    </div>
  );
}

export default function AdminUsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api.adminUsers());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load users.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const patch = async (
    target: AdminUser,
    changes: { role?: "admin" | "user"; is_active?: boolean },
    confirmText: string,
  ) => {
    if (!window.confirm(confirmText)) return;
    setBusyId(target.id);
    setNotice(null);
    setError(null);
    try {
      await api.adminPatchUser(target.id, changes);
      await load();
      setNotice(`Updated ${target.email}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed.");
    } finally {
      setBusyId(null);
    }
  };

  const resetSessions = async (target: AdminUser) => {
    if (!window.confirm(`Sign ${target.email} out of every session?`)) return;
    setBusyId(target.id);
    setNotice(null);
    setError(null);
    try {
      const result = await api.adminResetPassword(target.id);
      setNotice(
        `${target.email}: revoked ${result.sessions_revoked} session(s).`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reset failed.");
    } finally {
      setBusyId(null);
    }
  };

  // Budget overrides are a typed edit, not a destructive action: no confirm
  // dialog, but the audit trail records every change server-side.
  const saveBudgets = async (
    target: AdminUser,
    daily: number | null,
    monthly: number | null,
  ) => {
    setBusyId(target.id);
    setNotice(null);
    setError(null);
    try {
      await api.adminPatchUser(target.id, {
        daily_budget_usd: daily,
        monthly_budget_usd: monthly,
      });
      await load();
      setNotice(`Budgets updated for ${target.email}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Budget update failed.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Accounts</h2>
        <button
          onClick={() => void load()}
          className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-elevated hover:text-ink"
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {notice && (
        <p className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-sm text-emerald-700 dark:text-emerald-400">
          {notice}
        </p>
      )}
      {error && (
        <p className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {!users ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
          <Loader2 size={17} className="animate-spin text-accent" />
          Loading accounts…
        </div>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-line bg-panel">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-faint">
                <th className="px-4 py-2.5 font-medium">Email</th>
                <th className="px-4 py-2.5 font-medium">Role</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Budgets (d / m, USD)</th>
                <th className="px-4 py-2.5 font-medium">Last login</th>
                <th className="px-4 py-2.5 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isSelf = u.id === me?.id;
                const busy = busyId === u.id;
                return (
                  <tr key={u.id} className="border-b border-line/60 last:border-0">
                    <td className="max-w-[220px] truncate px-4 py-2.5 font-medium">
                      {u.email}
                      {isSelf && (
                        <span className="ml-1.5 text-xs text-faint">(you)</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                          u.role === "admin"
                            ? "bg-accent/10 text-accent"
                            : "bg-surface text-muted ring-1 ring-line"
                        }`}
                      >
                        {u.role === "admin" ? (
                          <ShieldCheck size={11} />
                        ) : (
                          <ShieldUser size={11} />
                        )}
                        {u.role}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs ${
                          u.is_active ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"
                        }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            u.is_active ? "bg-emerald-500" : "bg-red-500"
                          }`}
                        />
                        {u.is_active ? "active" : "disabled"}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <BudgetCell
                        user={u}
                        disabled={busy}
                        onSave={(daily, monthly) =>
                          saveBudgets(u, daily, monthly)
                        }
                      />
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted">
                      {fmtDate(u.last_login_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-1.5">
                        {busy ? (
                          <Loader2 size={14} className="animate-spin text-accent" />
                        ) : (
                          <>
                            <button
                              disabled={isSelf}
                              onClick={() =>
                                void patch(
                                  u,
                                  { role: u.role === "admin" ? "user" : "admin" },
                                  u.role === "admin"
                                    ? `Demote ${u.email} to user?`
                                    : `Promote ${u.email} to admin?`,
                                )
                              }
                              className="rounded-lg border border-line bg-surface px-2 py-1 text-xs text-muted hover:bg-elevated hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {u.role === "admin" ? "Demote" : "Promote"}
                            </button>
                            <button
                              disabled={isSelf}
                              onClick={() =>
                                void patch(
                                  u,
                                  { is_active: !u.is_active },
                                  u.is_active
                                    ? `Disable ${u.email}? They can no longer sign in.`
                                    : `Re-enable ${u.email}?`,
                                )
                              }
                              className="rounded-lg border border-line bg-surface px-2 py-1 text-xs text-muted hover:bg-elevated hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {u.is_active ? "Disable" : "Enable"}
                            </button>
                            <button
                              onClick={() => void resetSessions(u)}
                              className="rounded-lg border border-line bg-surface px-2 py-1 text-xs text-muted hover:bg-elevated hover:text-ink"
                            >
                              Sign out sessions
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-faint">
        Budgets are per-user USD caps enforced on chat before any model spend.
        Empty follows the global default; 0 disables that window.
      </p>
    </div>
  );
}
