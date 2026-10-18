import { useCallback, useEffect, useState } from "react";
import { FileText, Trash2 } from "lucide-react";
import { ApiError, api } from "../api/client";
import type { DocumentRecord } from "../api/types";
import ChunksModal from "../components/ChunksModal";
import UploadDropzone from "../components/UploadDropzone";

interface Props {
  onAskKey: () => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400",
  failed: "bg-red-500/12 text-red-600 dark:text-red-400",
  processing: "bg-amber-500/12 text-amber-600 dark:text-amber-400",
};

export default function Documents({ onAskKey }: Props) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [chunksFor, setChunksFor] = useState<DocumentRecord | null>(null);
  const [needsKey, setNeedsKey] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments());
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load documents.",
      );
      if (err instanceof ApiError && err.status === 401) setNeedsKey(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const record = await api.upload(file);
      setNotice(
        documents.some((d) => d.id === record.id)
          ? `“${record.original_filename}” is already indexed — showing the existing document.`
          : `“${record.original_filename}” uploaded and indexed.`,
      );
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400) setError(`Unsupported file: ${err.message}`);
        else if (err.status === 413) setError(err.message);
        else if (err.status === 422)
          setError(
            `${err.message}${err.documentId ? ` (document ${err.documentId.slice(0, 8)}… marked failed)` : ""}`,
          );
        else if (err.status === 503) setError(`Indexing unavailable: ${err.message}`);
        else setError(err.message);
        if (err.status === 401) setNeedsKey(true);
      } else {
        setError("Upload failed.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(doc: DocumentRecord) {
    if (!window.confirm(`Delete “${doc.original_filename}” and its vectors?`)) {
      return;
    }
    try {
      await api.deleteDocument(doc.id);
      setNotice(`Deleted “${doc.original_filename}”.`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed.");
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-6">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">Documents</h2>
          <span className="text-sm text-faint">
            {documents.length} indexed
          </span>
        </div>

        {error && (
          <div className="mb-3 flex items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
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
        {notice && (
          <div className="mb-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-sm text-emerald-700 dark:text-emerald-400">
            {notice}
          </div>
        )}

        <UploadDropzone onFile={handleFile} busy={busy} />

        <div className="mt-6">
          {loading && (
            <p className="py-6 text-center text-sm text-muted">
              Loading documents…
            </p>
          )}
          {!loading && documents.length === 0 && !error && (
            <p className="py-6 text-center text-sm text-muted">
              No documents yet — upload one above.
            </p>
          )}

          {documents.length > 0 && (
            <div className="overflow-hidden rounded-2xl border border-line bg-surface">
              {documents.map((doc) => (
                <div
                  key={doc.id}
                  className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-b-0 hover:bg-canvas/60"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                    <FileText size={16} strokeWidth={2} />
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium" title={doc.id}>
                      {doc.original_filename}
                    </div>
                    <div className="truncate text-xs text-faint">
                      .{doc.file_type} · {formatBytes(doc.size_bytes)} ·{" "}
                      {new Date(doc.created_at).toLocaleString()} ·{" "}
                      {doc.chunk_count ?? "—"} chunks
                      {doc.error_message && ` · ${doc.error_message.slice(0, 60)}`}
                    </div>
                  </div>

                  <span
                    className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                      STATUS_STYLES[doc.status] ?? "bg-elevated text-muted"
                    }`}
                  >
                    {doc.status}
                  </span>

                  <div className="flex items-center gap-1">
                    <button
                      className="rounded-lg px-2.5 py-1.5 text-xs text-muted transition-colors hover:bg-canvas hover:text-ink"
                      onClick={() => setChunksFor(doc)}
                    >
                      Chunks
                    </button>
                    <button
                      className="rounded-lg p-1.5 text-muted transition-colors hover:bg-red-500/10 hover:text-red-500"
                      onClick={() => handleDelete(doc)}
                      aria-label={`Delete ${doc.original_filename}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {chunksFor && (
        <ChunksModal
          documentId={chunksFor.id}
          filename={chunksFor.original_filename}
          onClose={() => setChunksFor(null)}
        />
      )}
    </div>
  );
}
