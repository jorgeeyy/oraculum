import { useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2 } from "lucide-react";
import { ApiError, api } from "../api/client";
import type { ChatResponse, MessageRecord, Source } from "../api/types";
import EvidencePanel from "../components/EvidencePanel";
import SourceCard from "../components/SourceCard";

interface ChatProps {
  onAskKey: () => void;
  conversationId: string | null;
  onConversationCreated: (conversationId: string) => void;
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

function toMessage(record: MessageRecord, conversationId: string | null): Message {
  if (record.role === "user") return { role: "user", text: record.content };
  return {
    role: "assistant",
    text: record.content,
    response: {
      question: "",
      answer: record.content,
      status:
        record.status === "insufficient_evidence"
          ? "insufficient_evidence"
          : "answered",
      sources: record.sources,
      model: record.model ?? "",
      retrieval_ms: record.retrieval_ms ?? 0,
      generation_ms: record.generation_ms ?? 0,
      conversation_id: conversationId,
    },
  };
}

export default function Chat({
  onAskKey,
  conversationId,
  onConversationCreated,
}: ChatProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingThread, setLoadingThread] = useState(conversationId !== null);
  const [error, setError] = useState<string | null>(null);
  const [needsKey, setNeedsKey] = useState(false);
  const [evidence, setEvidence] = useState<Source | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Load history whenever this thread is opened. Keyed on the id only (no
  // ref guard): React StrictMode runs effects twice, and a "skip if already
  // started" ref would cancel the first run and never start a second one,
  // leaving the thread stuck loading. The id that arrives after the first
  // message (null -> created id) simply refetches the just-saved exchange.
  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    setLoadingThread(true);
    setError(null);
    (async () => {
      try {
        const detail = await api.getConversation(conversationId);
        if (cancelled) return;
        const loaded = detail.messages.map((m) => toMessage(m, conversationId));
        setMessages(loaded);
        // Show the evidence for the latest grounded answer, like a fresh one.
        const last = loaded[loaded.length - 1];
        if (last?.response && last.response.sources.length > 0) {
          setEvidence(last.response.sources[0]);
        }
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err.status === 404
              ? "Conversation not found — it may have been deleted."
              : err.message
            : "Unexpected error.",
        );
        if (err instanceof ApiError && err.status === 401) setNeedsKey(true);
      } finally {
        if (!cancelled) setLoadingThread(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

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
      const response = await api.chat({
        question,
        conversation_id: conversationId ?? null,
      });
      setMessages((prev) => [...prev, { role: "assistant", text: response.answer, response }]);
      // Grounded answer -> open its top citation beside the chat.
      setEvidence(response.sources.length > 0 ? response.sources[0] : null);
      if (!conversationId && response.conversation_id) {
        onConversationCreated(response.conversation_id);
      }
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
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
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
        {loadingThread ? (
          <div className="flex h-full items-center justify-center gap-2.5 text-sm text-muted">
            <Loader2 size={17} className="animate-spin text-accent" />
            Loading conversation…
          </div>
        ) : messages.length === 0 ? (
          <div className="flex min-h-full flex-col items-center justify-center px-4 py-12">
            <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent text-on-accent text-xl font-bold">
              O
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
            <p className="mt-6 text-center text-xs text-faint">
              Chats save automatically — resume any of them from the sidebar.
            </p>
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
                  {m.response && m.response.sources.length > 0 && (
                    <div className="mt-4">
                      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
                        Sources
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {m.response.sources.map((s, idx) => (
                          <SourceCard
                            key={s.source_id}
                            source={s}
                            index={idx + 1}
                            active={evidence?.source_id === s.source_id}
                            onOpen={() => setEvidence(s)}
                          />
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
              disabled={busy || loadingThread}
              className="max-h-40 flex-1 resize-none bg-transparent text-[15px] leading-6 outline-none placeholder:text-faint disabled:opacity-60"
            />
            <button
              onClick={submit}
              disabled={busy || loadingThread || !input.trim()}
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
            Enter to send · Shift+Enter for a new line
          </p>
        </div>
      </div>
      </div>

      {/* Evidence beside the chat, like the cited document in a viewer */}
      {evidence && (
        <EvidencePanel
          key={evidence.source_id}
          source={evidence}
          onClose={() => setEvidence(null)}
        />
      )}
    </div>
  );
}
