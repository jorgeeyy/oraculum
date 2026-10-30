// Pure route-guard decisions, kept free of React/DOM so the matrix is
// exhaustively testable in vitest's node environment.

import type { AuthUser } from "../api/types";

export interface GuardInput {
  /** Bootstrap (/auth/me + /auth/config) still running. */
  loading: boolean;
  user: AuthUser | null;
  /** True when the API requires auth (API_KEY configured). */
  authRequired: boolean;
  /** Route is under /admin — only role=admin may enter. */
  adminOnly?: boolean;
}

export type GuardDecision =
  /** Wait for the session bootstrap; render a splash, not a redirect. */
  | "wait"
  /** Send to /login (keep the attempted path in router state). */
  | "login"
  /** Signed in but not an admin: leave /admin for the app. */
  | "chat"
  /** Render the route. */
  | "allow";

export function evaluateGuard({
  loading,
  user,
  authRequired,
  adminOnly = false,
}: GuardInput): GuardDecision {
  if (loading) return "wait";
  if (!user) {
    // Anonymous: forced login only when the API is locked down. Admin is
    // never browsable anonymously — even in open-local mode it requires a
    // real (admin) account.
    return authRequired || adminOnly ? "login" : "allow";
  }
  if (adminOnly && user.role !== "admin") return "chat";
  return "allow";
}
