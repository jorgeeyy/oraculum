import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ChunksResponse } from "../api/types";

interface Props {
  documentId: string;
  filename: string;
  onClose: () => void;
}

export default function ChunksModal({ documentId, filename, onClose }: Props) {
  const [data, setData] = useState<ChunksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getChunks(documentId)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Could not load chunks.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal">
        <div className="modal-header">
          <h3>{filename} — chunks</h3>
          <button className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body">
          {error && <div className="error-banner">{error}</div>}
          {!data && !error && <p className="muted">Loading…</p>}
          {data && (
            <>
              <p className="muted" style={{ marginTop: 0 }}>
                {data.chunk_count} chunks · {data.chunk_size_tokens} tokens
                target · {data.chunk_overlap_tokens} overlap
              </p>
              {data.chunks.map((chunk) => (
                <div className="chunk-item" key={chunk.chunk_index}>
                  <div className="chunk-meta">
                    <span>#{chunk.chunk_index}</span>
                    <span>page {chunk.page}</span>
                    <span>{chunk.token_count} tokens</span>
                  </div>
                  <div className="chunk-text">{chunk.text}</div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
