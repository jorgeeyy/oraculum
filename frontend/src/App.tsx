import { useState } from "react";
import {
  FileText,
  KeyRound,
  Menu,
  MessageSquare,
  Moon,
  Search,
  Sun,
  X,
} from "lucide-react";
import { getApiKey, setApiKey } from "./api/client";
import HealthBadge from "./components/HealthBadge";
import Chat from "./tabs/Chat";
import Documents from "./tabs/Documents";
import Retrieve from "./tabs/Retrieve";

type Tab = "chat" | "documents" | "retrieve";

const NAV_ITEMS: readonly { id: Tab; label: string; icon: typeof MessageSquare }[] =
  [
    { id: "chat", label: "Chat", icon: MessageSquare },
    { id: "documents", label: "Documents", icon: FileText },
    { id: "retrieve", label: "Retrieve", icon: Search },
  ];

const TITLES: Record<Tab, string> = {
  chat: "Chat",
  documents: "Documents",
  retrieve: "Retrieve",
};

function readIsDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [prefill, setPrefill] = useState("");
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(getApiKey() ?? "");
  const [apiKey, setApiKeyState] = useState(getApiKey());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isDark, setIsDark] = useState(readIsDark);

  const sendToChat = (question: string) => {
    setPrefill(question);
    setTab("chat");
    setSidebarOpen(false);
  };

  const saveKey = () => {
    const trimmed = keyDraft.trim();
    setApiKey(trimmed || null);
    setApiKeyState(trimmed || null);
    setKeyModalOpen(false);
  };

  const toggleTheme = () => {
    const next = !isDark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("rag.theme", next ? "dark" : "light");
    } catch {
      /* storage unavailable; theme still applies for this session */
    }
    setIsDark(next);
  };

  const navigate = (next: Tab) => {
    setTab(next);
    setSidebarOpen(false);
  };

  return (
    <div className="flex h-full bg-canvas text-ink">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-line bg-panel transition-transform duration-200 lg:static lg:z-auto lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center justify-between px-4">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-on-accent">
              <MessageSquare size={16} strokeWidth={2.25} />
            </span>
            <span className="text-sm font-semibold tracking-tight">
              Knowledge Assistant
            </span>
          </div>
          <button
            className="rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="flex flex-col gap-0.5 px-3 py-2">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => navigate(id)}
              className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                tab === id
                  ? "bg-surface font-medium text-ink shadow-sm ring-1 ring-line"
                  : "text-muted hover:bg-surface/70 hover:text-ink"
              }`}
            >
              <Icon size={16} strokeWidth={2} />
              {label}
            </button>
          ))}
        </nav>

        <div className="mt-auto flex flex-col gap-1 p-3">
          <HealthBadge />
          <button
            onClick={() => setKeyModalOpen(true)}
            className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface/70 hover:text-ink"
          >
            <KeyRound size={16} strokeWidth={2} />
            API key
            <span
              className={`ml-auto h-1.5 w-1.5 rounded-full ${
                apiKey ? "bg-emerald-500" : "bg-faint"
              }`}
              title={apiKey ? "Key configured" : "No key set"}
            />
          </button>
          <button
            onClick={toggleTheme}
            className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface/70 hover:text-ink"
          >
            {isDark ? <Sun size={16} strokeWidth={2} /> : <Moon size={16} strokeWidth={2} />}
            {isDark ? "Light mode" : "Dark mode"}
          </button>
        </div>
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line px-4">
          <button
            className="rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu size={18} />
          </button>
          <h1 className="text-sm font-medium">{TITLES[tab]}</h1>
        </header>

        <main className="min-h-0 flex-1 overflow-hidden">
          {tab === "chat" && (
            <Chat
              apiKeyVersion={apiKey ?? ""}
              prefill={prefill}
              onAskKey={() => setKeyModalOpen(true)}
            />
          )}
          {tab === "documents" && (
            <Documents onAskKey={() => setKeyModalOpen(true)} />
          )}
          {tab === "retrieve" && <Retrieve onSendToChat={sendToChat} />}
        </main>
      </div>

      {/* API key dialog */}
      {keyModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget) setKeyModalOpen(false);
          }}
        >
          <div className="w-full max-w-md rounded-2xl border border-line bg-elevated p-6 shadow-2xl">
            <div className="mb-1 flex items-center justify-between">
              <h2 className="text-base font-semibold">API key</h2>
              <button
                className="rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink"
                onClick={() => setKeyModalOpen(false)}
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>
            <p className="mb-4 text-sm text-muted">
              Sent as <code className="rounded bg-surface px-1 py-0.5 text-[13px]">X-API-Key</code>{" "}
              with every request. Only required when the server has{" "}
              <code className="rounded bg-surface px-1 py-0.5 text-[13px]">API_KEY</code>{" "}
              configured.
            </p>
            <input
              type="text"
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              placeholder="paste API key"
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-accent focus:ring-2 focus:ring-accent/25"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") saveKey();
              }}
            />
            <div className="mt-4 flex justify-end gap-2">
              {apiKey && (
                <button
                  className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-surface hover:text-ink"
                  onClick={() => {
                    setKeyDraft("");
                    setApiKey(null);
                    setApiKeyState(null);
                    setKeyModalOpen(false);
                  }}
                >
                  Clear
                </button>
              )}
              <button
                className="rounded-lg px-3 py-1.5 text-sm font-medium text-on-accent bg-accent hover:bg-accent-hover"
                onClick={saveKey}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
