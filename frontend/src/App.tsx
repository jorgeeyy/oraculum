import { Fragment, useCallback, useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  FileText,
  KeyRound,
  Menu,
  MessageSquare,
  MessageSquareDashed,
  Moon,
  Plus,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import { api, getApiKey, setApiKey } from "./api/client";
import type { ConversationSummary } from "./api/types";
import HealthBadge from "./components/HealthBadge";
import Chat from "./tabs/Chat";
import Documents from "./tabs/Documents";

type Tab = "chat" | "documents";

const NAV_ITEMS: readonly { id: Tab; label: string; icon: typeof MessageSquare }[] =
  [
    { id: "chat", label: "Chat", icon: MessageSquare },
    { id: "documents", label: "Documents", icon: FileText },
  ];

const TITLES: Record<Tab, string> = {
  chat: "Chat",
  documents: "Documents",
};

function readIsDark(): boolean {
  return document.documentElement.classList.contains("dark");
}

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(getApiKey() ?? "");
  const [apiKey, setApiKeyState] = useState(getApiKey());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isDark, setIsDark] = useState(readIsDark);

  // Chat history. `activeId` is the conversation the thread belongs to;
  // `chatKey` only changes when the thread should remount (select/new chat),
  // so creating the first conversation doesn't blow away local messages.
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationsError, setConversationsError] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [chatKey, setChatKey] = useState("new");
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [chatsExpanded, setChatsExpanded] = useState(true);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations());
      setConversationsError(false);
    } catch {
      // Distinguish "no chats" from "couldn't fetch chats" so the sidebar
      // never claims the history is empty when the server is unreachable.
      setConversationsError(true);
    }
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations, apiKey]);

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

  const startNewChat = () => {
    setActiveId(null);
    setChatKey("new");
    setPendingDeleteId(null);
    setTab("chat");
    setSidebarOpen(false);
  };

  const selectConversation = (id: string) => {
    setActiveId(id);
    setChatKey(id);
    setPendingDeleteId(null);
    setTab("chat");
    setSidebarOpen(false);
  };

  const handleConversationCreated = (id: string) => {
    setActiveId(id);
    setChatsExpanded(true);
    refreshConversations();
  };

  const confirmDelete = async (id: string) => {
    setPendingDeleteId(null);
    try {
      await api.deleteConversation(id);
    } catch {
      return; // keep the item in the list
    }
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (activeId === id) {
      setActiveId(null);
      setChatKey("new");
    }
  };

  const activeTitle = conversations.find((c) => c.id === activeId)?.title;

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
              Oraculum
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

        {/* Nav + nested chat history (scrolls as one; footer stays pinned) */}
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          <nav className="flex flex-col gap-0.5">
            {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
              <Fragment key={id}>
                {id === "chat" ? (
                  <div
                    className={`group flex items-center rounded-lg transition-colors ${
                      tab === id
                        ? "bg-surface shadow-sm ring-1 ring-line"
                        : "hover:bg-surface/70"
                    }`}
                  >
                    <button
                      onClick={() => {
                        navigate(id);
                        setChatsExpanded(true);
                      }}
                      className={`flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors ${
                        tab === id
                          ? "font-medium text-ink"
                          : "text-muted group-hover:text-ink"
                      }`}
                    >
                      <Icon size={16} strokeWidth={2} />
                      {label}
                    </button>
                    <button
                      onClick={() => setChatsExpanded((v) => !v)}
                      aria-expanded={chatsExpanded}
                      aria-label={
                        chatsExpanded
                          ? "Collapse chat history"
                          : "Expand chat history"
                      }
                      className="mr-1.5 shrink-0 rounded-md p-1 text-faint transition-colors hover:bg-surface hover:text-ink"
                    >
                      <ChevronDown
                        size={14}
                        className={`transition-transform duration-150 ${
                          chatsExpanded ? "" : "-rotate-90"
                        }`}
                      />
                    </button>
                  </div>
                ) : (
                  <button
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
                )}

                {/* Chat history, indented under the Chat nav item */}
                {id === "chat" && chatsExpanded && (
                  <div className="mb-1 ml-5 mt-1 flex flex-col gap-0.5 border-l border-line pl-2">
                    <button
                      onClick={startNewChat}
                      className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] font-medium text-muted transition-colors hover:bg-surface/70 hover:text-ink"
                    >
                      <Plus size={14} strokeWidth={2} />
                      New chat
                    </button>
                    {conversations.length === 0 ? (
                      <div className="flex items-start gap-2 px-2.5 py-1.5 text-xs leading-4 text-faint">
                        <MessageSquareDashed
                          size={14}
                          className="mt-px shrink-0"
                        />
                        <span>
                          {conversationsError ? (
                            <>
                              Chats couldn't be loaded — is the server
                              reachable?
                            </>
                          ) : (
                            <>
                              No chats yet — your first message starts one
                            </>
                          )}
                        </span>
                      </div>
                    ) : (
                      conversations.map((c) => (
                        <div key={c.id} className="group relative">
                          <button
                            onClick={() => selectConversation(c.id)}
                            className={`flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[13px] transition-colors ${
                              activeId === c.id
                                ? "bg-surface font-medium text-ink shadow-sm ring-1 ring-line"
                                : "text-muted hover:bg-surface/70 hover:text-ink"
                            }`}
                          >
                            <span className="truncate pr-6">{c.title}</span>
                          </button>
                          {pendingDeleteId === c.id ? (
                            <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5 rounded-md bg-elevated px-1 py-0.5 shadow ring-1 ring-line">
                              <button
                                onClick={() => confirmDelete(c.id)}
                                aria-label="Confirm delete"
                                className="rounded p-1 text-red-500 hover:bg-red-500/10"
                              >
                                <Check size={13} />
                              </button>
                              <button
                                onClick={() => setPendingDeleteId(null)}
                                aria-label="Cancel delete"
                                className="rounded p-1 text-muted hover:bg-surface hover:text-ink"
                              >
                                <X size={13} />
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => setPendingDeleteId(c.id)}
                              aria-label="Delete conversation"
                              className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded p-1.5 text-faint hover:bg-surface hover:text-ink group-hover:block"
                            >
                              <Trash2 size={13} />
                            </button>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </Fragment>
            ))}
          </nav>
        </div>

        <div className="flex flex-col gap-1 border-t border-line p-3">
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
          <h1 className="min-w-0 truncate text-sm font-medium">
            {tab === "chat" && activeTitle ? activeTitle : TITLES[tab]}
          </h1>
        </header>

        <main className="min-h-0 flex-1 overflow-hidden">
          {tab === "chat" && (
            <Chat
              key={chatKey}
              onAskKey={() => setKeyModalOpen(true)}
              conversationId={activeId}
              onConversationCreated={handleConversationCreated}
            />
          )}
          {tab === "documents" && (
            <Documents onAskKey={() => setKeyModalOpen(true)} />
          )}
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
