import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, ShieldCheck, ShieldUser } from "lucide-react";
import { ApiError, api } from "../../api/client";
import type { AdminUser } from "../../api/types";
import { useAuth } from "../../auth/AuthProvider";

function fmtDate(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleDateString();
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
    </div>
  );
}
