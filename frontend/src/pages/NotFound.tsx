import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-canvas px-4 text-ink">
      <p className="text-5xl font-bold tracking-tight text-faint">404</p>
      <p className="text-sm text-muted">That page doesn't exist.</p>
      <Link
        to="/"
        className="mt-1 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:bg-accent-hover"
      >
        Back home
      </Link>
    </div>
  );
}
