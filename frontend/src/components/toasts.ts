// Global toast store (WS-8): a tiny pub/sub any module can push into.

export type ToastKind = "info" | "success" | "warn" | "error";

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  /** No auto-dismiss (governance states the user must acknowledge). */
  sticky: boolean;
}

type Listener = (toasts: Toast[]) => void;

const listeners = new Set<Listener>();
let toasts: Toast[] = [];
let nextId = 1;

/** Same message within this window is ignored (parallel 401s, retries). */
const DEDUPE_MS = 2500;
const lastPushed = new Map<string, number>();

/** Per-kind lifetimes; warn/error linger a little longer than info/success. */
const TTL_MS: Record<ToastKind, number> = {
  info: 5000,
  success: 4000,
  warn: 8000,
  error: 8000,
};

function emit(): void {
  for (const listener of listeners) listener([...toasts]);
}

export interface PushOptions {
  kind: ToastKind;
  message: string;
  sticky?: boolean;
}

/**
 * Show a toast. Returns its id (0 when deduped) so callers could dismiss
 * it early. Auto-dismiss lives here — not in the component — so a toast
 * expires even if the host re-renders or unmounts.
 */
export function pushToast({ kind, message, sticky = false }: PushOptions): number {
  const now = Date.now();
  const previous = lastPushed.get(message);
  if (previous !== undefined && now - previous < DEDUPE_MS) return 0;
  lastPushed.set(message, now);

  const id = nextId++;
  toasts = [...toasts, { id, kind, message, sticky }];
  emit();

  if (!sticky) {
    setTimeout(() => dismissToast(id), TTL_MS[kind]);
  }
  return id;
}

export function dismissToast(id: number): void {
  if (!toasts.some((toast) => toast.id === id)) return;
  toasts = toasts.filter((toast) => toast.id !== id);
  emit();
}

/** Subscribe to toast-list changes; returns an unsubscribe function. */
export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  listener([...toasts]);
  return () => void listeners.delete(listener);
}

/** Test-only: wipe toasts and dedupe history between test cases. */
export function resetToasts(): void {
  toasts = [];
  lastPushed.clear();
  emit();
}
