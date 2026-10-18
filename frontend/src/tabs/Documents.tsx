import { useCallback, useEffect, useState } from "react";
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
        record.content_hash && documents.some((d) => d.id === record.id)
          ? `“${record.original_filename}” is already indexed — showing the existing document.`
          : `“${record.original_filename}” uploaded and indexed (${record.chunk_count ?? 0} chunks).`,
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
      {notice && <div className="info-banner">{notice}</div>}

      <UploadDropzone onFile={handleFile} busy={busy} />

      {loading && <p className="muted">Loading documents…</p>}
      {!loading && documents.length === 0 && !error && (
        <p className="muted">No documents yet — upload one above.</p>
      )}

      {documents.length > 0 && (
        <table className="doc-table">
          <thead>
            <tr>
              <th>File</th>
              <th>Status</th>
              <th>Chunks</th>
              <th>Size</th>
              <th>Uploaded</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id}>
                <td title={doc.id}>
                  {doc.original_filename}
                  <div className="muted">.{doc.file_type}</div>
                </td>
                <td>
                  <span
                    className={`chip ${
                      doc.status === "completed"
                        ? "ok"
                        : doc.status === "failed"
                          ? "warn"
                          : "neutral"
                    }`}
                  >
                    {doc.status}
                  </span>
                  {doc.error_message && (
                    <div className="muted" title={doc.error_message}>
                      {doc.error_message.slice(0, 60)}
                    </div>
                  )}
                </td>
                <td>{doc.chunk_count ?? "—"}</td>
                <td>{formatBytes(doc.size_bytes)}</td>
                <td className="muted">{new Date(doc.created_at).toLocaleString()}</td>
                <td>
                  <div className="doc-actions">
                    <button className="ghost" onClick={() => setChunksFor(doc)}>
                      Chunks
                    </button>
                    <button className="danger" onClick={() => handleDelete(doc)}>
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

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
