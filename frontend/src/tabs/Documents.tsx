import { useEffect, useState } from "react";
import { ArrowLeft, FileText, Trash2 } from "lucide-react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { ApiError, api } from "../api/client";
import type { DocumentContentResponse, DocumentRecord } from "../api/types";
import UploadDropzone from "../components/UploadDropzone";

interface Props {
  onAuthRequired: () => void;
  documents: DocumentRecord[];
  loading: boolean;
  loadError: string | null;
  unauthorized: boolean;
  selectedDocId: string | null;
  onClearSelection: () => void;
  refresh: () => Promise<void>;
}

function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false });
  return DOMPurify.sanitize(html);
}

export default function Documents({
  onAuthRequired,
  documents,
  loading,
  loadError,
  unauthorized,
  selectedDocId,
  onClearSelection,
  refresh,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [content, setContent] = useState<DocumentContentResponse | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);

  // Load metadata + text whenever the selection changes.
  useEffect(() => {
    if (!selectedDocId) {
      setContent(null);
      setContentError(null);
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    setContentError(null);
    setContent(null);
    api
      .getContent(selectedDocId)
      .then((data) => {
        if (!cancelled) setContent(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setContentError(
            err instanceof ApiError
              ? err.message
              : "Could not load document contents.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedDocId]);

  async function handleFile(file: File) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const record = await api.upload(file);
      if (documents.some((d) => d.id === record.id)) {
        setNotice(
          record.status === "completed"
            ? `“${record.original_filename}” is already indexed — showing the existing document.`
            : `“${record.original_filename}” is already being indexed — showing the existing document.`,
        );
      } else {
        setNotice(
          `“${record.original_filename}” uploaded — indexing in the background. This page updates automatically.`,
        );
      }
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 400) setError(`Unsupported file: ${err.message}`);
        else if (err.status === 413) setError(err.message);
        else if (err.status === 503) setError(`Indexing unavailable: ${err.message}`);
        else setError(err.message);
        if (err.status === 401) setNeedsAuth(true);
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

  // Full-screen viewer for the selected document.
  if (selectedDocId) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-4">
          <button
            onClick={onClearSelection}
            className="flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-muted transition-colors hover:bg-surface hover:text-ink"
          >
            <ArrowLeft size={16} strokeWidth={2} />
            Back
          </button>
          <span className="min-w-0 truncate text-sm font-medium">
            {content?.filename ?? "Document"}
          </span>
          {content && (
            <span className="ml-auto shrink-0 rounded-full bg-surface px-2.5 py-0.5 text-xs text-faint ring-1 ring-line">
              .{content.file_type}
              {content.file_type !== "pdf" &&
                ` · ${content.page_count} ${content.page_count === 1 ? "page" : "pages"}`}
            </span>
          )}
        </div>

        <div className="min-h-0 flex-1">
          {contentError && (
            <div className="m-4 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
              {contentError}
            </div>
          )}
          {contentLoading && (
            <p className="py-12 text-center text-sm text-muted">
              Loading document…
            </p>
          )}

          {content && content.file_type === "pdf" && !contentError && (
            <iframe
              src={`/documents/${content.document_id}/file`}
              title={content.filename}
              className="h-full w-full border-0"
            />
          )}

          {content && content.file_type === "md" && (
            <div className="h-full overflow-y-auto px-6 py-6">
              <div
                className="markdown-body mx-auto max-w-3xl"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(content.text) }}
              />
            </div>
          )}

          {content &&
            content.file_type !== "pdf" &&
            content.file_type !== "md" && (
              <div className="h-full overflow-y-auto px-6 py-6">
                <pre className="mx-auto max-w-3xl whitespace-pre-wrap font-mono text-[13px] leading-6 text-muted">
                  {content.text}
                </pre>
              </div>
            )}
        </div>
      </div>
    );
  }

  const bannerError = error ?? loadError;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-6">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">Documents</h2>
          <span className="text-sm text-faint">
            {documents.length} {documents.length === 1 ? "document" : "documents"}
          </span>
        </div>

        {bannerError && (
          <div className="mb-3 flex items-center gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-sm text-red-600 dark:text-red-400">
            <span className="flex-1">{bannerError}</span>
            {(needsAuth || unauthorized) && (
              <button
                className="rounded-lg border border-red-500/40 px-2.5 py-1 text-xs font-medium hover:bg-red-500/15"
                onClick={onAuthRequired}
              >
                Sign in
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
          {!loading && documents.length === 0 && !bannerError && (
            <p className="py-6 text-center text-sm text-muted">
              No documents yet — upload one above.
            </p>
          )}

          {documents.length > 0 && (
            <div className="overflow-hidden rounded-2xl border border-line bg-surface">
              {documents.map((doc) => (
                <div
                  key={doc.id}
                  className="flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0 hover:bg-canvas/60"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                    <FileText size={16} strokeWidth={2} />
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium" title={doc.id}>
                      {doc.original_filename}
                    </div>
                    {doc.status === "failed" && doc.error_message && (
                      <div className="truncate text-xs text-red-600 dark:text-red-400">
                        {doc.error_message}
                      </div>
                    )}
                  </div>

                  <button
                    className="rounded-lg p-1.5 text-muted transition-colors hover:bg-red-500/10 hover:text-red-500"
                    onClick={() => handleDelete(doc)}
                    aria-label={`Delete ${doc.original_filename}`}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
