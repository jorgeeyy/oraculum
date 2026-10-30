import { Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/AuthProvider";
import App from "./App";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import Register from "./pages/Register";
import AdminLayout from "./pages/admin/AdminLayout";
import AdminAuditPage from "./pages/admin/Audit";
import AdminFeedbackPage from "./pages/admin/Feedback";
import AdminOverviewPage from "./pages/admin/Overview";
import AdminUsagePage from "./pages/admin/Usage";
import AdminUsersPage from "./pages/admin/Users";

/**
 * Public: `/` landing, `/login`, `/register`. Everything else that touches
 * user data sits behind RequireAuth, which waits for the session bootstrap
 * and then applies the guard matrix (see auth/guards.ts). Admin pages add
 * the adminOnly flag: a real admin session is required even in open-local
 * mode.
 */
export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/chat"
        element={
          <RequireAuth>
            <App />
          </RequireAuth>
        }
      />
      <Route
        path="/documents"
        element={
          <RequireAuth>
            <App />
          </RequireAuth>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireAuth adminOnly>
            <AdminLayout />
          </RequireAuth>
        }
      >
        <Route index element={<AdminOverviewPage />} />
        <Route path="users" element={<AdminUsersPage />} />
        <Route path="usage" element={<AdminUsagePage />} />
        <Route path="feedback" element={<AdminFeedbackPage />} />
        <Route path="audit" element={<AdminAuditPage />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
