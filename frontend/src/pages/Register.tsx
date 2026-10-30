import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Loader2, MessageSquare } from "lucide-react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

const MIN_PASSWORD = 8;

function registerErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Could not create the account.";
  switch (error.status) {
    case 0:
      return "Cannot reach the server. Is it running?";
    case 403:
      return "Registration is disabled on this instance.";
    case 409:
      return "That email is already registered — sign in instead.";
    case 422:
      return error.message;
    default:
      return error.message;
  }
}

export default function Register() {
  const { register, user, loading, registrationEnabled } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && user) navigate("/chat", { replace: true });
  }, [loading, user, navigate]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(email.trim(), password);
      navigate("/chat", { replace: true });
    } catch (err) {
      setError(registerErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!registrationEnabled) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-canvas px-4 text-ink">
        <p className="text-sm text-muted">
          Registration is disabled on this instance.
        </p>
        <Link to="/login" className="text-sm font-medium text-accent hover:underline">
          Go to sign in
        </Link>
      </div>
    );
  }

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
              Create your account
            </h1>
            <p className="mt-1 text-sm text-muted">
              Your chats and uploads stay private to your account.
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
              minLength={MIN_PASSWORD}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={`At least ${MIN_PASSWORD} characters`}
              className="mb-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent focus:ring-2 focus:ring-accent/25"
            />
            <p className="mb-4 text-xs text-faint">
              Stored as an Argon2id hash — never in plain text.
            </p>

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
              {busy ? "Creating account…" : "Create account"}
            </button>
          </form>

          <p className="mt-4 text-center text-sm text-muted">
            Already have an account?{" "}
            <Link
              to="/login"
              className="font-medium text-accent hover:underline"
            >
              Sign in
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
