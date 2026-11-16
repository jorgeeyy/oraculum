import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import ToastHost from "./components/ToastHost";
import AppRoutes from "./routes";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
        {/* Global toasts: mounted above the router so every route (chat,
            documents, admin, login) shares one notification surface. */}
        <ToastHost />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
