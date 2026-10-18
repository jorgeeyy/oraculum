import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ChatResponse, DocumentRecord } from "../api/types";
import SourceCard from "../components/SourceCard";

interface ChatProps {
  prefill: string;
  apiKeyVersion: string;
  onAskKey: () => void;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
}

export default function Chat({ prefill, apiKeyVersion, onAskKey }: ChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsKey, setNeedsKey] = useState(false);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [scopeDoc, setScopeDoc] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [topK, setTopK] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (prefill) setInput(prefill);
  }, [prefill]);

  useEffect(() => {
    let cancelled = false;
    api
      .listDocuments()
      .then((docs) => {
        if (!cancelled) setDocuments(docs.filter((d) => d.status === "completed"));
      })
      .catch(() => {
        /* scope dropdown is optional; ignore load failures */
      });
    return () => {
      cancelled = true;
    };
  }, [apiKeyVersion]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function submit() {
    const question = input.trim();
    if (!question || busy) return;
    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setInput("");
    setBusy(true);
    setError(null);
    setNeedsKey(false);
    try {
      const parsedTopK = Number(topK);
      const response = await api.chat({
        question,
        ...(scopeDoc ? { document_id: scopeDoc } : {}),
        ...(Number.isInteger(parsedTopK) && parsedTopK >= 1 && parsedTopK <= 20
          ? { top_k: parsedTopK }
          : {}),
      });
      setMessages((prev) => [...prev, { role: "assistant", text: response.answer, response }]);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.status === 429
            ? `Rate limit reached — retry in ${err.retryAfterSeconds ?? 60}s.`
            : err.message
          : "Unexpected error.";
      setError(message);
      if (err instanceof ApiError && err.status === 401) setNeedsKey(true);
      // Roll back the optimistic user message and restore the input for editing.
      setMessages((prev) => prev.slice(0, -1));
      setInput(question);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {error && (
        <div className="error-banner">
          {error}{" "}
          {needsKey && (
            <button className="ghost" onClick={onAskKey}>
              Set API key
            </button>
          )}
        </div>
      )}

      <div className="chat-messages">
        {messages.length === 0 && (
          <p className="muted">
            Ask a question about your indexed documents. Answers include only
            retrieved evidence — if the corpus doesn't cover it, you'll get an
            explicit insufficient-evidence refusal instead of a guess.
          </p>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg user">
              {m.text}
            </div>
          ) : (
            <div key={i} className="msg assistant">
              {m.text}
              {m.response && (
                <div className="msg-meta">
                  <span
                    className={`chip ${
                      m.response.status === "answered" ? "ok" : "warn"
                    }`}
                  >
                    {m.response.status === "answered"
                      ? "grounded answer"
                      : "insufficient evidence"}
                  </span>
                  <span className="muted">
                    {m.response.model} · retrieval {m.response.retrieval_ms}ms ·
                    generation {m.response.generation_ms}ms
                  </span>
                </div>
              )}
              {m.response?.sources.map((s) => (
                <SourceCard key={s.source_id} source={s} />
              ))}
            </div>
          ),
        )}
        {busy && (
          <div className="msg assistant muted">
            <span className="spinner" /> Searching evidence and thinking…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-row">
        <input
          type="text"
          value={input}
          placeholder="e.g. How many days of annual leave do employees get?"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          disabled={busy}
        />
        <button className="primary" onClick={submit} disabled={busy || !input.trim()}>
          {busy ? "…" : "Ask"}
        </button>
      </div>

      <div className="options-row">
        <label className="muted">
          Scope:{" "}
          <select value={scopeDoc} onChange={(e) => setScopeDoc(e.target.value)}>
            <option value="">All documents</option>
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.original_filename}
              </option>
            ))}
          </select>
        </label>
        <details
          className="advanced"
          open={showAdvanced}
          onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}
        >
          <summary>Advanced</summary>
          <div>
            <label>
              top_k
              <input
                type="number"
                min={1}
                max={20}
                value={topK}
                placeholder="server default"
                onChange={(e) => setTopK(e.target.value)}
                style={{ width: 110 }}
              />
            </label>
            <span>results to retrieve (1–20; empty = server default)</span>
          </div>
        </details>
      </div>
    </div>
  );
}
