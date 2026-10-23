import { useEffect, useState } from "react";
import { X } from "lucide-react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { ApiError, api } from "../api/client";
import type { DocumentContentResponse, Source } from "../api/types";

interface Props {
  source: Source;
  onClose: () => void;
}

function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot + 1).toLowerCase();
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Wrap the cited excerpt in the raw text with a <mark>, tolerating
// whitespace differences between the stored file and the cleaned chunk.
function highlightExcerpt(text: string, excerpt: string): string {
  const escaped = escapeHtml(text);
  const parts = excerpt
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (parts.length === 0) return escaped;
  const re = new RegExp(parts.join("\\s+"), "i");
  return escaped.replace(
    re,
    (m) =>
      `<mark class="rounded bg-amber-300/60 px-0.5 dark:bg-amber-500/30">${m}</mark>`,
  );
}

export default function EvidencePanel({ source, onClose }: Props) {
  const ext = fileExtension(source.filename);
  const isPdf = ext === "pdf";
  const isMarkdown = ext === "md" || ext === "markdown";
  const [doc, setDoc] = useState<DocumentContentResponse | null>(null);
  const [loading, setLoading] = useState(!isPdf);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isPdf) return; // the native PDF viewer fetches the file itself
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDoc(null);
    api
      .getContent(source.document_id)
      .then((data) => {
        if (!cancelled) setDoc(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Could not load the document.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [source.document_id, isPdf]);

  return (
    <aside className="fixed inset-0 z-40 flex flex-col bg-canvas lg:static lg:z-auto lg:w-[44%] lg:max-w-2xl lg:shrink-0 lg:border-l lg:border-line">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-4">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {source.filename}
        </span>
        <span className="shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs text-faint ring-1 ring-line">
          page {source.page}
        </span>
        <button
          onClick={onClose}
          aria-label="Close evidence"
          className="rounded-md p-1.5 text-muted hover:bg-surface hover:text-ink"
        >
          <X size={16} />
        </button>
      </div>

      {/* The excerpt the answer was grounded in */}
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">
          Cited evidence
        </div>
        <p className="text-[13px] leading-5 text-muted">{source.excerpt}</p>
      </div>

      <div className="min-h-0 flex-1">
        {error && (
          <div className="m-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
            {error}
          </div>
        )}
        {loading && !error && (
          <p className="py-8 text-center text-sm text-muted">
            Loading document…
          </p>
        )}

        {/* PDFs open in the browser's native viewer, jumped to the cited page */}
        {isPdf && (
          <iframe
            key={source.source_id}
            src={`/documents/${source.document_id}/file#page=${source.page}`}
            title={source.filename}
            className="h-full w-full border-0"
          />
        )}

        {!isPdf && !loading && !error && doc && isMarkdown && (
          <div className="h-full overflow-y-auto px-5 py-4">
            <div
              className="markdown-body"
              dangerouslySetInnerHTML={{
                __html: DOMPurify.sanitize(
                  marked.parse(doc.text, { async: false }),
                ),
              }}
            />
          </div>
        )}

        {!isPdf && !loading && !error && doc && !isMarkdown && (
          <div className="h-full overflow-y-auto px-5 py-4">
            <pre
              className="whitespace-pre-wrap font-mono text-[13px] leading-6 text-muted"
              dangerouslySetInnerHTML={{
                __html: highlightExcerpt(doc.text, source.excerpt),
              }}
            />
          </div>
        )}
      </div>
    </aside>
  );
}
