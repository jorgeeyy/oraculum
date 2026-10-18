import { useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Settings2 } from "lucide-react";
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

const SUGGESTIONS = [
  "How many days of annual leave do employees get?",
  "How do I reset a forgotten password?",
  "What is the process for requesting IT equipment?",
];

export default function Chat({ prefill, apiKeyVersion, onAskKey }: ChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsKey, setNeedsKey] = useState(false);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [scopeDoc, setScopeDoc] = useState("");
  const [topK, setTopK] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (prefill) {
      setInput(prefill);
      inputRef.current?.focus();
    }
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

  // Auto-grow the composer textarea.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

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
    <div className="flex h-full flex-col">
      {error && (
        <div className="mx-auto mt-3 flex w-full max-w-3xl items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
          <span className="flex-1">{error}</span>
          {needsKey && (
            <button
              className="rounded-lg border border-red-500/40 px-2.5 py-1 text-xs font-medium hover:bg-red-500/15"
              onClick={onAskKey}
            >
              Set API key
            </button>
          )}
        </div>
      )}

      {/* Thread */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-4 py-12">
            <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent text-on-accent text-xl font-bold">
              R
            </span>
            <h2 className="text-2xl font-semibold tracking-tight">
              What's in your knowledge base?
            </h2>
            <p className="mt-2 max-w-md text-center text-sm text-muted">
              Ask a question about your indexed documents. Answers cite only
              retrieved evidence — if the corpus doesn't cover it, you'll get an
              explicit refusal instead of a guess.
            </p>
            <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-3">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    setInput(s);
                    inputRef.current?.focus();
                  }}
                  className="rounded-2xl border border-line bg-surface px-4 py-3 text-left text-[13px] leading-5 text-muted shadow-sm transition-colors hover:border-accent/50 hover:text-ink"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg bg-surface px-4 py-2.5 text-[15px] leading-6 ring-1 ring-line">
                    {m.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="text-[15px] leading-7">
                  <p className="whitespace-pre-wrap">{m.text}</p>
                  {m.response && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          m.response.status === "answered"
                            ? "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400"
                            : "bg-amber-500/12 text-amber-600 dark:text-amber-400"
                        }`}
                      >
                        {m.response.status === "answered"
                          ? "grounded answer"
                          : "insufficient evidence"}
                      </span>
                      <span className="text-xs text-faint">
                        {m.response.model} · retrieval{" "}
                        {Math.round(m.response.retrieval_ms)}ms · generation{" "}
                        {Math.round(m.response.generation_ms)}ms
                      </span>
                    </div>
                  )}
                  {m.response && m.response.sources.length > 0 && (
                    <div className="mt-4">
                      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
                        Sources
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {m.response.sources.map((s, idx) => (
                          <SourceCard key={s.source_id} source={s} index={idx + 1} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ),
            )}
            {busy && (
              <div className="flex items-center gap-2.5 text-[15px] text-muted">
                <Loader2 size={17} className="animate-spin text-accent" />
                Searching evidence and thinking…
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t border-line bg-canvas/95 backdrop-blur">
        <div className="mx-auto w-full max-w-3xl px-4 py-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <select
              value={scopeDoc}
              onChange={(e) => setScopeDoc(e.target.value)}
              className="rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-muted outline-none hover:text-ink focus:border-accent"
              title="Limit answers to one document"
            >
              <option value="">All documents</option>
              {documents.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.original_filename}
                </option>
              ))}
            </select>

            <details className="group relative">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-xs text-muted outline-none hover:text-ink [&::-webkit-details-marker]:hidden">
                <Settings2 size={13} />
                top_k
                {topK && <span className="text-accent">{topK}</span>}
              </summary>
              <div className="absolute bottom-full left-0 z-10 mb-2 w-64 rounded-xl border border-line bg-elevated p-3 shadow-xl">
                <label className="block text-xs font-medium text-muted">
                  Results to retrieve
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={topK}
                    placeholder="server default (5)"
                    onChange={(e) => setTopK(e.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm text-ink outline-none placeholder:text-faint focus:border-accent"
                  />
                </label>
                <p className="mt-1.5 text-[11px] text-faint">
                  1–20; empty uses the server default.
                </p>
              </div>
            </details>
          </div>

          <div className="flex items-end gap-2 rounded-3xl border border-line bg-surface px-4 py-3 shadow-lg transition-colors focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/20">
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              placeholder="Ask anything about your documents…"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  submit();
                }
              }}
              disabled={busy}
              className="max-h-40 flex-1 resize-none bg-transparent text-[15px] leading-6 outline-none placeholder:text-faint disabled:opacity-60"
            />
            <button
              onClick={submit}
              disabled={busy || !input.trim()}
              aria-label="Send"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition-colors hover:bg-accent-hover disabled:opacity-35"
            >
              {busy ? (
                <Loader2 size={17} className="animate-spin" />
              ) : (
                <ArrowUp size={17} strokeWidth={2.5} />
              )}
            </button>
          </div>

          <p className="mt-2 text-center text-[11px] text-faint">
            Enter to send · Shift+Enter for a new line · grounded in retrieved
            evidence only
          </p>
        </div>
      </div>
    </div>
  );
}
