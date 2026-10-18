import { useState } from "react";
import { ArrowRight, Loader2, Search } from "lucide-react";
import { ApiError, api } from "../api/client";
import type { RetrievalResponse } from "../api/types";

interface Props {
  onSendToChat: (question: string) => void;
}

export default function Retrieve({ onSendToChat }: Props) {
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RetrievalResponse | null>(null);

  async function search() {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    try {
      const parsed = Number(topK);
      setResult(
        await api.retrieve({
          question: q,
          ...(Number.isInteger(parsed) && parsed >= 1 && parsed <= 20
            ? { top_k: parsed }
            : {}),
        }),
      );
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError ? err.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-6">
        <div className="mb-1">
          <h2 className="text-lg font-semibold">Retrieve</h2>
          <p className="text-sm text-muted">
            Raw retrieval view — the same pipeline chat uses, without the LLM.
            See exactly which chunks (and scores) feed an answer.
          </p>
        </div>

        <div className="mt-4 rounded-3xl border border-line bg-surface px-4 py-3 shadow-sm transition-colors focus-within:border-accent/60 focus-within:ring-2 focus-within:ring-accent/20">
          <div className="flex items-end gap-2">
            <textarea
              rows={1}
              value={question}
              placeholder="e.g. How do I reset a forgotten password?"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  search();
                }
              }}
              disabled={busy}
              className="max-h-32 flex-1 resize-none bg-transparent text-[15px] leading-6 outline-none placeholder:text-faint disabled:opacity-60"
            />
            <button
              onClick={search}
              disabled={busy || !question.trim()}
              aria-label="Search"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition-colors hover:bg-accent-hover disabled:opacity-35"
            >
              {busy ? (
                <Loader2 size={17} className="animate-spin" />
              ) : (
                <Search size={16} strokeWidth={2.5} />
              )}
            </button>
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs">
            <label className="text-faint">
              top_k
              <input
                type="number"
                min={1}
                max={20}
                value={topK}
                placeholder="default"
                onChange={(e) => setTopK(e.target.value)}
                className="ml-1.5 w-16 rounded-lg border border-line bg-canvas px-2 py-1 text-xs text-ink outline-none placeholder:text-faint focus:border-accent"
              />
            </label>
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
            {error}
          </div>
        )}

        {result && (
          <div className="mt-6">
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-elevated px-2.5 py-1 text-muted ring-1 ring-line">
                top_k used: {result.top_k}
              </span>
              <span className="rounded-full bg-elevated px-2.5 py-1 text-muted ring-1 ring-line">
                {result.results.length} results
              </span>
              <span className="rounded-full bg-elevated px-2.5 py-1 text-muted ring-1 ring-line">
                {Math.round(result.elapsed_ms)}ms
              </span>
              <button
                className="ml-auto flex items-center gap-1 rounded-full px-2.5 py-1 font-medium text-accent transition-colors hover:bg-accent/10"
                onClick={() => onSendToChat(result.question)}
              >
                Ask in Chat
                <ArrowRight size={13} />
              </button>
            </div>

            {result.results.length === 0 && (
              <p className="rounded-2xl border border-line bg-surface px-4 py-8 text-center text-sm text-muted">
                No chunks matched — the index may be empty, or a document
                filter excluded everything.
              </p>
            )}

            <div className="space-y-3">
              {result.results.map((hit, i) => (
                <div
                  key={hit.source_id}
                  className="rounded-2xl border border-line bg-surface p-4 shadow-sm transition-colors hover:border-accent/40"
                >
                  <div className="mb-2.5 flex flex-wrap items-center gap-2.5">
                    <span className="flex h-5 min-w-5 items-center justify-center rounded-md bg-accent/12 px-1 text-[11px] font-semibold text-accent">
                      {i + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">
                      {hit.filename}
                    </span>
                    <span className="text-xs text-faint">
                      p.{hit.page} · chunk #{hit.chunk_index}
                    </span>
                    <span
                      className="flex items-center gap-2"
                      title={`similarity ${hit.score.toFixed(3)}`}
                    >
                      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-line">
                        <span
                          className="block h-full rounded-full bg-accent"
                          style={{
                            width: `${Math.max(4, Math.min(100, hit.score * 100))}%`,
                          }}
                        />
                      </span>
                      <span className="w-10 text-right font-mono text-xs text-muted">
                        {hit.score.toFixed(3)}
                      </span>
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap text-[13px] leading-6 text-muted">
                    {hit.text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
