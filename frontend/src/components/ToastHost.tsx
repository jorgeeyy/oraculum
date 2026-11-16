import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  X,
  XCircle,
} from "lucide-react";
import { onUnauthorized, onUsageLimitExceeded } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import {
  dismissToast,
  pushToast,
  subscribeToasts,
  type Toast,
  type ToastKind,
} from "./toasts";

const KIND_ICON: Record<ToastKind, typeof Info> = {
  info: Info,
  success: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
};

const KIND_STYLE: Record<ToastKind, string> = {
  info: "border-line bg-panel text-ink",
  success: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  warn: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  error: "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400",
};

/**
 * Fixed toast stack mounted once above the router (main.tsx), so every
 * screen — chat, documents, admin, login — shows the same messages.
 *
 * Two global subscriptions live here because they are app-wide states,
 * not page-local errors:
 * - mid-session 401 → "Session expired" (the guard's redirect follows);
 *   the bootstrap 401 of a signed-out visit is NOT an expiry, so the
 *   toast only fires while a session was actually present.
 * - budget refusal (403 `usage_limit_exceeded`) → sticky warn toast;
 *   the chat surface additionally keeps its inline banner.
 */
export default function ToastHost() {
  const { user } = useAuth();
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Ref (not state): must read "was signed in" at the instant the 401
  // fires, before React re-renders from AuthProvider clearing the user.
  const hadSession = useRef(false);

  useEffect(() => {
    hadSession.current = user !== null;
  }, [user]);

  useEffect(() => subscribeToasts(setToasts), []);

  useEffect(
    () =>
      onUnauthorized(() => {
        if (hadSession.current) {
          pushToast({
            kind: "warn",
            message: "Session expired — please sign in again.",
          });
        }
      }),
    [],
  );

  useEffect(
    () => onUsageLimitExceeded((message) => pushToast({ kind: "warn", message, sticky: true })),
    [],
  );

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed right-4 top-4 z-[60] flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-2"
      role="status"
      aria-live="polite"
    >
      {toasts.map((toast) => {
        const Icon = KIND_ICON[toast.kind];
        return (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-sm shadow-lg backdrop-blur ${KIND_STYLE[toast.kind]}`}
          >
            <Icon size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span className="flex-1 leading-5">{toast.message}</span>
            <button
              onClick={() => dismissToast(toast.id)}
              aria-label="Dismiss notification"
              className="shrink-0 rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
