import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api, onUnauthorized } from "../api/client";
import type { AuthUser } from "../api/types";
import { evaluateGuard } from "./guards";

interface AuthContextValue {
  /** Signed-in account, or null (anonymous — allowed in open-local mode). */
  user: AuthUser | null;
  /** Session bootstrap still running: guards must wait, not redirect. */
  loading: boolean;
  /** The API requires auth (API_KEY configured): anonymous → /login. */
  authRequired: boolean;
  /** Whether the register page should be offered. */
  registrationEnabled: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const [registrationEnabled, setRegistrationEnabled] = useState(true);

  // Bootstrap: public config + session probe. A 401 on /auth/me is the
  // normal signed-out answer, not an error.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [config, me] = await Promise.allSettled([
        api.authConfig(),
        api.me(),
      ]);
      if (cancelled) return;
      if (config.status === "fulfilled") {
        setAuthRequired(config.value.auth_required);
        setRegistrationEnabled(config.value.registration_enabled);
      }
      setUser(me.status === "fulfilled" ? me.value : null);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Any mid-session 401 (expired cookie, revoked session) drops the user;
  // the guard then redirects away from protected routes.
  useEffect(() => onUnauthorized(() => setUser(null)), []);

  const login = useCallback(async (email: string, password: string) => {
    setUser(await api.login(email, password));
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    setUser(await api.register(email, password));
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // Cookie is gone either way; local state must agree.
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        authRequired,
        registrationEnabled,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

function Splash() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas text-ink">
      <Loader2 size={22} className="animate-spin text-accent" />
    </div>
  );
}

/**
 * Route wrapper: waits for the session bootstrap, then applies the guard
 * matrix from `guards.ts`. Unauthenticated attempts keep their target path
 * in router state so /login can send the user back after signing in.
 */
export function RequireAuth({
  children,
  adminOnly = false,
}: {
  children: ReactNode;
  adminOnly?: boolean;
}) {
  const { user, loading, authRequired } = useAuth();
  const location = useLocation();
  const decision = evaluateGuard({ loading, user, authRequired, adminOnly });

  if (decision === "wait") return <Splash />;
  if (decision === "login") {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    );
  }
  if (decision === "chat") return <Navigate to="/chat" replace />;
  return <>{children}</>;
}
