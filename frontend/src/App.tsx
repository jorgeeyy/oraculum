import { useState } from "react";
import { getApiKey, setApiKey } from "./api/client";
import HealthBadge from "./components/HealthBadge";
import Chat from "./tabs/Chat";
import Documents from "./tabs/Documents";
import Retrieve from "./tabs/Retrieve";

type Tab = "chat" | "documents" | "retrieve";

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [prefill, setPrefill] = useState("");
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState(getApiKey() ?? "");
  const [apiKey, setApiKeyState] = useState(getApiKey());

  const sendToChat = (question: string) => {
    setPrefill(question);
    setTab("chat");
  };

  const saveKey = () => {
    const trimmed = keyDraft.trim();
    setApiKey(trimmed || null);
    setApiKeyState(trimmed || null);
    setKeyModalOpen(false);
  };

  return (
    <div className="app">
      <header className="header">
        <h1>
          RAG Knowledge Assistant <span>· grounded answers with sources</span>
        </h1>
        <HealthBadge />
        <button
          className="icon-button"
          onClick={() => setKeyModalOpen(true)}
          title="API key"
        >
          {apiKey ? "🔑 Key set" : "🔑 API key"}
        </button>
      </header>

      <nav className="tabs">
        {(
          [
            ["chat", "Chat"],
            ["documents", "Documents"],
            ["retrieve", "Retrieve"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={`tab ${tab === id ? "active" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "chat" && (
        <Chat
          apiKeyVersion={apiKey ?? ""}
          prefill={prefill}
          onAskKey={() => setKeyModalOpen(true)}
        />
      )}
      {tab === "documents" && <Documents onAskKey={() => setKeyModalOpen(true)} />}
      {tab === "retrieve" && <Retrieve onSendToChat={sendToChat} />}

      {keyModalOpen && (
        <div
          className="modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) setKeyModalOpen(false);
          }}
        >
          <div className="modal" style={{ width: "min(440px, 100%)" }}>
            <div className="modal-header">
              <h3>API key</h3>
              <button className="ghost" onClick={() => setKeyModalOpen(false)}>
                Close
              </button>
            </div>
            <div className="modal-body">
              <p className="muted" style={{ marginTop: 0 }}>
                Sent as <code>X-API-Key</code> with every request. Required only
                when the server has <code>API_KEY</code> configured.
              </p>
              <input
                type="text"
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                placeholder="paste API key"
                style={{ width: "100%" }}
                autoFocus
              />
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <button className="primary" onClick={saveKey}>
                  Save
                </button>
                {apiKey && (
                  <button
                    className="ghost"
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
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
