import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ReadyResponse } from "../api/types";

const POLL_MS = 30_000;

export default function HealthBadge() {
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      try {
        const result = await api.ready();
        if (!cancelled) {
          setReady(result);
          setOffline(false);
        }
      } catch {
        if (!cancelled) {
          setReady(null);
          setOffline(true);
        }
      }
    }

    check();
    const timer = setInterval(check, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const down = offline || ready?.status === "unavailable";
  const dotClass = ready === null && !offline ? "bg-faint" : down ? "bg-red-500" : "bg-emerald-500";
  const label = offline
    ? "Server unreachable"
    : ready === null
      ? "Checking…"
      : ready.status === "ok"
        ? "System ready"
        : "Degraded";

  const title = offline
    ? "Cannot reach /health/ready"
    : ready
      ? Object.entries(ready.dependencies)
          .map(([name, state]) => `${name}: ${state}`)
          .join("\n")
      : "";

  return (
    <div
      className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted"
      title={title}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`} />
      <span className="truncate">{label}</span>
    </div>
  );
}
