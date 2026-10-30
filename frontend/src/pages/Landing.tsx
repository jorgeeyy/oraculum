import { Link } from "react-router-dom";
import { FileText, MessageSquare, ShieldCheck } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";

const FEATURES = [
  {
    icon: MessageSquare,
    title: "Grounded answers",
    body: "Every answer cites the exact chunks it came from — verify before you trust.",
  },
  {
    icon: FileText,
    title: "Your knowledge base",
    body: "Upload handbooks, policies and runbooks; hybrid search finds the passage that answers the question.",
  },
  {
    icon: ShieldCheck,
    title: "Private by design",
    body: "Accounts, server-side sessions and per-user chat history. Nothing shared between users.",
  },
];

export default function Landing() {
  const { user, authRequired, registrationEnabled } = useAuth();

  // Anonymous + locked API → the first step is signing in; otherwise straight in.
  const primary =
    !user && authRequired
      ? { label: "Sign in", to: "/login" }
      : { label: "Open app", to: "/chat" };

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <header className="mx-auto flex h-16 max-w-5xl items-center justify-between px-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-on-accent">
            <MessageSquare size={16} strokeWidth={2.25} />
          </span>
          <span className="text-sm font-semibold tracking-tight">Oraculum</span>
        </div>
        <nav className="flex items-center gap-2">
          {user ? (
            <Link
              to="/chat"
              className="rounded-lg bg-accent px-3.5 py-1.5 text-sm font-medium text-on-accent hover:bg-accent-hover"
            >
              Open app
            </Link>
          ) : (
            <>
              <Link
                to="/login"
                className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-surface hover:text-ink"
              >
                Sign in
              </Link>
              <Link
                to={registrationEnabled ? "/register" : "/login"}
                className="rounded-lg bg-accent px-3.5 py-1.5 text-sm font-medium text-on-accent hover:bg-accent-hover"
              >
                Get started
              </Link>
            </>
          )}
        </nav>
      </header>

      <main className="mx-auto max-w-5xl px-4">
        <section className="py-16 text-center sm:py-24">
          <h1 className="mx-auto max-w-2xl text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
            Ask your documents.
            <br />
            Get answers you can verify.
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-base leading-6 text-muted">
            Oraculum turns your team's knowledge base into a conversational
            assistant that shows its work — every claim traced back to the
            source document.
          </p>
          <div className="mt-8 flex items-center justify-center gap-3">
            <Link
              to={primary.to}
              className="rounded-xl bg-accent px-6 py-2.5 text-sm font-medium text-on-accent shadow-sm hover:bg-accent-hover"
            >
              {primary.label}
            </Link>
            {!user && registrationEnabled && (
              <Link
                to="/register"
                className="rounded-xl border border-line bg-surface px-6 py-2.5 text-sm font-medium text-ink hover:bg-elevated"
              >
                Create an account
              </Link>
            )}
          </div>
          {!user && registrationEnabled && (
            <p className="mt-3 text-xs text-faint">
              Sign in or create an account to start asking questions.
            </p>
          )}
        </section>

        <section className="grid gap-4 pb-16 sm:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="rounded-2xl border border-line bg-panel p-5"
            >
              <span className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-surface text-accent ring-1 ring-line">
                <Icon size={17} strokeWidth={2} />
              </span>
              <h2 className="text-sm font-semibold">{title}</h2>
              <p className="mt-1.5 text-sm leading-5 text-muted">{body}</p>
            </div>
          ))}
        </section>
      </main>

      <footer className="border-t border-line py-6 text-center text-xs text-faint">
        Oraculum — retrieval-augmented knowledge assistant
      </footer>
    </div>
  );
}
