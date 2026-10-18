import { useState } from "react";
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
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <p className="muted" style={{ marginTop: 0 }}>
          Raw retrieval view: same pipeline the chat uses, without the LLM —
          useful for seeing exactly which chunks (and scores) feed an answer.
        </p>
        <div className="chat-input-row">
          <input
            type="text"
            value={question}
            placeholder="e.g. How do I reset a forgotten password?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") search();
            }}
            disabled={busy}
          />
          <input
            type="number"
            min={1}
            max={20}
            value={topK}
            placeholder="top_k"
            onChange={(e) => setTopK(e.target.value)}
            style={{ width: 90 }}
            disabled={busy}
          />
          <button
            className="primary"
            onClick={search}
            disabled={busy || !question.trim()}
          >
            {busy ? "…" : "Search"}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {result && (
        <>
          <div className="msg-meta" style={{ marginBottom: 12 }}>
            <span className="chip neutral">top_k used: {result.top_k}</span>
            <span className="chip neutral">{result.results.length} results</span>
            <span className="chip neutral">{result.elapsed_ms}ms</span>
            <button className="ghost" onClick={() => onSendToChat(result.question)}>
              Ask this in Chat →
            </button>
          </div>

          {result.results.length === 0 && (
            <p className="muted">
              No chunks matched — the index may be empty, or a document filter
              excluded everything.
            </p>
          )}

          {result.results.map((hit, i) => (
            <div className="result-card" key={hit.source_id}>
              <div className="result-head">
                <strong>
                  #{i + 1} {hit.filename}
                </strong>
                <span className="muted">
                  p.{hit.page} · chunk #{hit.chunk_index}
                </span>
                <span className="score-bar" title={`similarity ${hit.score.toFixed(3)}`}>
                  <div
                    style={{
                      width: `${Math.max(0, Math.min(100, hit.score * 100))}%`,
                    }}
                  />
                </span>
                <span className="chip neutral">{hit.score.toFixed(3)}</span>
              </div>
              <div className="result-text">{hit.text}</div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
