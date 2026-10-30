import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft, Loader2, MessageSquare } from "lucide-react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

/** Turn an auth failure into a sentence a human can act on. */
export function loginErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Could not sign in. Try again.";
  switch (error.status) {
    case 0:
      return "Cannot reach the server. Is it running?";
    case 401:
      return "Invalid email or password.";
    case 403:
      return "This account is disabled. Ask an administrator for help.";
    case 429:
      return error.retryAfterSeconds
        ? `Too many attempts. Try again in ${error.retryAfterSeconds}s.`
        : "Too many attempts. Try again in a minute.";
    default:
      return error.message;
  }
}

export default function Login() {
  const { login, user, loading, registrationEnabled } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from =
    (location.state as { from?: string } | null)?.from ?? "/chat";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Already signed in: skip the form entirely.
  useEffect(() => {
    if (!loading && user) navigate(from, { replace: true });
  }, [loading, user, from, navigate]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(loginErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-canvas text-ink">
      <header className="mx-auto flex h-16 w-full max-w-md items-center px-4">
        <Link
          to="/"
          className="flex items-center gap-1.5 text-sm text-muted hover:text-ink"
        >
          <ArrowLeft size={15} />
          Back
        </Link>
      </header>

      <main className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-md">
          <div className="mb-6 flex flex-col items-center text-center">
            <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-on-accent">
              <MessageSquare size={20} strokeWidth={2.25} />
            </span>
            <h1 className="text-xl font-semibold tracking-tight">
              Sign in to Oraculum
            </h1>
            <p className="mt-1 text-sm text-muted">
              Use your email and password.
            </p>
          </div>

          <form
            onSubmit={submit}
            className="rounded-2xl border border-line bg-panel p-6"
          >
            <label className="mb-1 block text-sm font-medium" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="mb-4 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent focus:ring-2 focus:ring-accent/25"
            />

            <label
              className="mb-1 block text-sm font-medium"
              htmlFor="password"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="mb-4 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent focus:ring-2 focus:ring-accent/25"
            />

            {error && (
              <p
                role="alert"
                className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400"
              >
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-on-accent hover:bg-accent-hover disabled:opacity-60"
            >
              {busy && <Loader2 size={15} className="animate-spin" />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <p className="mt-4 text-center text-sm text-muted">
            {registrationEnabled ? (
              <>
                No account yet?{" "}
                <Link
                  to="/register"
                  className="font-medium text-accent hover:underline"
                >
                  Create one
                </Link>
              </>
            ) : (
              "Registration is disabled — ask an administrator for an account."
            )}
          </p>
        </div>
      </main>
    </div>
  );
}
