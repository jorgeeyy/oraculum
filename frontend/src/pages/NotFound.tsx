import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-canvas px-4 text-ink">
      <span
        aria-hidden
        className="mb-2 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent text-2xl font-bold text-on-accent"
      >
        O
      </span>
      <p className="text-5xl font-bold tracking-tight text-faint">404</p>
      <p className="text-sm text-muted">That page doesn't exist.</p>
      <div className="mt-2 flex items-center gap-2">
        <Link
          to="/"
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover"
        >
          Back home
        </Link>
        <Link
          to="/chat"
          className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-muted hover:text-ink"
        >
          Go to chat
        </Link>
      </div>
    </div>
  );
}
