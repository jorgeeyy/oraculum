import { Fragment, useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FileText,
  LogIn,
  LogOut,
  Menu,
  MessageSquare,
  MessageSquareDashed,
  Moon,
  Plus,
  ShieldCheck,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import { api } from "./api/client";
import { useAuth } from "./auth/AuthProvider";
import type { ConversationSummary, DocumentRecord } from "./api/types";
import HealthBadge from "./components/HealthBadge";
import { useDocuments } from "./hooks/useDocuments";
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

function readSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem("rag.sidebarCollapsed") === "1";
  } catch {
    return false;
  }
}

const DOC_STATUS_DOT: Record<DocumentRecord["status"], string> = {
  completed: "bg-emerald-500",
  processing: "bg-amber-500 animate-pulse",
  pending: "bg-sky-500",
  failed: "bg-red-500",
};

export default function App() {
  // The active tab follows the route (/chat | /documents), not local state:
  // guards wrap these routes and deep links must land in the right view.
  const routerNavigate = useNavigate();
  const location = useLocation();
  const { user, authRequired, logout } = useAuth();
  const tab: Tab = location.pathname.startsWith("/documents")
    ? "documents"
    : "chat";
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed);
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
  const [docsExpanded, setDocsExpanded] = useState(true);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

  const {
    documents: docList,
    loading: docsLoading,
    error: docsError,
    unauthorized: docsUnauthorized,
    refresh: refreshDocuments,
  } = useDocuments();

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
  }, [refreshConversations]);

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

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("rag.sidebarCollapsed", next ? "1" : "0");
      } catch {
        /* storage unavailable; collapse still applies for this session */
      }
      return next;
    });
  };

  const goToTab = (next: Tab) => {
    routerNavigate(next === "chat" ? "/chat" : "/documents");
    setSidebarOpen(false);
  };

  const handleSignOut = async () => {
    setSidebarOpen(false);
    try {
      await logout();
    } catch {
      // Logout is best-effort (network): local state is already cleared.
    }
    routerNavigate(authRequired ? "/login" : "/", { replace: true });
  };

  const startNewChat = () => {
    setActiveId(null);
    setChatKey("new");
    setPendingDeleteId(null);
    routerNavigate("/chat");
    setSidebarOpen(false);
  };

  const selectConversation = (id: string) => {
    setActiveId(id);
    setChatKey(id);
    setPendingDeleteId(null);
    routerNavigate("/chat");
    setSidebarOpen(false);
  };

  const selectDocument = (id: string) => {
    setSelectedDocId(id);
    routerNavigate("/documents");
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
        className={`fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-line bg-panel transition-[width,transform] duration-200 lg:static lg:z-auto lg:translate-x-0 ${
          collapsed ? "lg:w-16" : ""
        } ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div
          className={`flex h-14 shrink-0 items-center px-4 ${
            collapsed ? "lg:justify-center lg:px-2" : "justify-between"
          }`}
        >
          <div
            className={`flex items-center gap-2.5 ${collapsed ? "lg:hidden" : ""}`}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-on-accent">
              <MessageSquare size={16} strokeWidth={2.25} />
            </span>
            <span className="text-sm font-semibold tracking-tight">
              Oraculum
            </span>
          </div>
          <div className="flex items-center gap-0.5">
            <button
              className="hidden rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink lg:block"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
            </button>
            <button
              className="rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink lg:hidden"
              onClick={() => setSidebarOpen(false)}
              aria-label="Close sidebar"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Nav + nested chat history (scrolls as one; footer stays pinned) */}
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          <nav className="flex flex-col gap-0.5">
            {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
              <Fragment key={id}>
                {collapsed ? (
                  <button
                    onClick={() => {
                      if (id === "chat") {
                        goToTab(id);
                        setChatsExpanded(true);
                      } else {
                        setSelectedDocId(null);
                        goToTab(id);
                        setDocsExpanded(true);
                      }
                    }}
                    title={label}
                    className={`flex w-full items-center justify-center rounded-lg py-2 text-sm transition-colors ${
                      tab === id
                        ? "bg-surface font-medium text-ink shadow-sm ring-1 ring-line"
                        : "text-muted hover:bg-surface/70 hover:text-ink"
                    }`}
                  >
                    <Icon size={16} strokeWidth={2} />
                  </button>
                ) : id === "chat" ? (
                  <div
                    className={`group flex items-center rounded-lg transition-colors ${
                      tab === id
                        ? "bg-surface shadow-sm ring-1 ring-line"
                        : "hover:bg-surface/70"
                    }`}
                  >
                    <button
                      onClick={() => {
                        goToTab(id);
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
                  <div
                    className={`group flex items-center rounded-lg transition-colors ${
                      tab === id
                        ? "bg-surface shadow-sm ring-1 ring-line"
                        : "hover:bg-surface/70"
                    }`}
                  >
                    <button
                      onClick={() => {
                        setSelectedDocId(null);
                        goToTab(id);
                        setDocsExpanded(true);
                      }}
                      className={`flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors ${
                        tab === id
                          ? "font-medium text-ink"
                          : "text-muted group-hover:text-ink"
                      }`}
                    >
                      <Icon size={16} strokeWidth={2} />
                      {label}
                      {docList.length > 0 && (
                        <span className="ml-auto shrink-0 text-xs text-faint">
                          {docList.length}
                        </span>
                      )}
                    </button>
                    <button
                      onClick={() => setDocsExpanded((v) => !v)}
                      aria-expanded={docsExpanded}
                      aria-label={
                        docsExpanded
                          ? "Collapse document list"
                          : "Expand document list"
                      }
                      className="mr-1.5 shrink-0 rounded-md p-1 text-faint transition-colors hover:bg-surface hover:text-ink"
                    >
                      <ChevronDown
                        size={14}
                        className={`transition-transform duration-150 ${
                          docsExpanded ? "" : "-rotate-90"
                        }`}
                      />
                    </button>
                  </div>
                )}

                {/* Document list, indented under the Documents nav item */}
                {!collapsed && id === "documents" && docsExpanded && (
                  <div className="mb-1 ml-5 mt-1 flex flex-col gap-0.5 border-l border-line pl-2">
                    {docList.length === 0 ? (
                      <div className="flex items-start gap-2 px-2.5 py-1.5 text-xs leading-4 text-faint">
                        <FileText size={14} className="mt-px shrink-0" />
                        <span>
                          {docsError && !docsLoading
                            ? "Documents couldn't be loaded — is the server reachable?"
                            : "No documents yet — upload from the Documents tab"}
                        </span>
                      </div>
                    ) : (
                      docList.map((doc) => (
                        <button
                          key={doc.id}
                          onClick={() => selectDocument(doc.id)}
                          className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] transition-colors ${
                            selectedDocId === doc.id
                              ? "bg-surface font-medium text-ink shadow-sm ring-1 ring-line"
                              : "text-muted hover:bg-surface/70 hover:text-ink"
                          }`}
                          title={doc.status}
                        >
                          <span
                            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                              DOC_STATUS_DOT[doc.status] ?? "bg-faint"
                            }`}
                          />
                          <span className="truncate">
                            {doc.original_filename}
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}

                {/* Chat history, indented under the Chat nav item */}
                {!collapsed && id === "chat" && chatsExpanded && (
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
            {user?.role === "admin" && (
              <Link
                to="/admin"
                onClick={() => setSidebarOpen(false)}
                title="Admin dashboard"
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                  location.pathname.startsWith("/admin")
                    ? "bg-surface font-medium text-ink shadow-sm ring-1 ring-line"
                    : "text-muted hover:bg-surface/70 hover:text-ink"
                } ${collapsed ? "lg:justify-center lg:px-0" : ""}`}
              >
                <ShieldCheck size={16} strokeWidth={2} />
                <span className={collapsed ? "lg:hidden" : ""}>Admin</span>
              </Link>
            )}
          </nav>
        </div>

        <div className="flex flex-col gap-1 border-t border-line p-3">
          <div className={collapsed ? "lg:hidden" : ""}>
            <HealthBadge />
          </div>
          {user ? (
            <button
              onClick={handleSignOut}
              title={`Signed in as ${user.email} — sign out`}
              className={`flex items-center rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface/70 hover:text-ink ${
                collapsed ? "lg:justify-center lg:px-0" : "gap-2.5"
              }`}
            >
              <LogOut size={16} strokeWidth={2} />
              <span className={`truncate ${collapsed ? "lg:hidden" : ""}`}>
                Sign out
              </span>
            </button>
          ) : (
            <button
              onClick={() => routerNavigate("/login")}
              title="Sign in"
              className={`flex items-center rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface/70 hover:text-ink ${
                collapsed ? "lg:justify-center lg:px-0" : "gap-2.5"
              }`}
            >
              <LogIn size={16} strokeWidth={2} />
              <span className={collapsed ? "lg:hidden" : ""}>Sign in</span>
            </button>
          )}
          <button
            onClick={toggleTheme}
            title={isDark ? "Light mode" : "Dark mode"}
            className={`flex items-center rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface/70 hover:text-ink ${
              collapsed ? "lg:justify-center lg:px-0" : "gap-2.5"
            }`}
          >
            {isDark ? <Sun size={16} strokeWidth={2} /> : <Moon size={16} strokeWidth={2} />}
            <span className={collapsed ? "lg:hidden" : ""}>
              {isDark ? "Light mode" : "Dark mode"}
            </span>
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
              onAuthRequired={() => routerNavigate("/login")}
              conversationId={activeId}
              onConversationCreated={handleConversationCreated}
            />
          )}
          {tab === "documents" && (
            <Documents
              onAuthRequired={() => routerNavigate("/login")}
              documents={docList}
              loading={docsLoading}
              loadError={docsError}
              unauthorized={docsUnauthorized}
              selectedDocId={selectedDocId}
              onClearSelection={() => setSelectedDocId(null)}
              refresh={refreshDocuments}
            />
          )}
        </main>
      </div>

    </div>
  );
}
