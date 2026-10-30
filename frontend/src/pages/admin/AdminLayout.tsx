import { NavLink, Outlet } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  Gauge,
  MessageSquare,
  ScrollText,
  Users,
} from "lucide-react";

const SECTIONS = [
  { to: "/admin", label: "Overview", icon: Gauge, end: true },
  { to: "/admin/users", label: "Users", icon: Users },
  { to: "/admin/usage", label: "Usage", icon: Activity },
  { to: "/admin/feedback", label: "Feedback", icon: MessageSquare },
  { to: "/admin/audit", label: "Audit log", icon: ScrollText },
];

/** Shell for /admin/*: section nav + page outlet. */
export default function AdminLayout() {
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold tracking-tight">Admin</h1>
            <nav className="flex items-center gap-1">
              {SECTIONS.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                      isActive
                        ? "bg-surface font-medium text-ink ring-1 ring-line"
                        : "text-muted hover:bg-surface/60 hover:text-ink"
                    }`
                  }
                >
                  <Icon size={14} strokeWidth={2} />
                  <span className="hidden sm:inline">{label}</span>
                </NavLink>
              ))}
            </nav>
          </div>
          <NavLink
            to="/chat"
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface hover:text-ink"
          >
            <ArrowLeft size={14} />
            Back to app
          </NavLink>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
