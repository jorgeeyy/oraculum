import { useEffect, useState } from "react";
import { X } from "lucide-react";
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border border-line bg-elevated shadow-2xl">
        <div className="flex shrink-0 items-center justify-between border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{filename}</h2>
            <p className="text-xs text-muted">
              {data
                ? `${data.chunk_count} chunks · ${data.chunk_size_tokens}-token target · ${data.chunk_overlap_tokens} overlap`
                : "Chunks"}
            </p>
          </div>
          <button
            className="rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {error && (
            <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
          {!data && !error && (
            <p className="py-8 text-center text-sm text-muted">Loading…</p>
          )}
          {data?.chunks.map((chunk) => (
            <div
              key={chunk.chunk_index}
              className="rounded-xl border border-line bg-surface p-3.5"
            >
              <div className="mb-2 flex items-center gap-3 text-[11px] text-faint">
                <span className="rounded-md bg-accent/10 px-1.5 py-0.5 font-semibold text-accent">
                  #{chunk.chunk_index}
                </span>
                <span>page {chunk.page}</span>
                <span>{chunk.token_count} tokens</span>
              </div>
              <p className="whitespace-pre-wrap text-[13px] leading-6 text-muted">
                {chunk.text}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
