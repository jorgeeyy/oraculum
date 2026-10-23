import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { DocumentRecord } from "../api/types";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

export function isIndexing(doc: DocumentRecord): boolean {
  return doc.status === "pending" || doc.status === "processing";
}

// Single source of truth for the document list, shared by the sidebar and
// the Documents tab. Polls while anything is being indexed, so the sidebar
// stays live even when the user is on another tab.
export function useDocuments() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const pollStartedAtRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments());
      setError(null);
      setUnauthorized(false);
    } catch (err) {
      // Distinguish "no docs" from "couldn't fetch" so the sidebar never
      // claims the list is empty when the server is unreachable.
      setError(
        err instanceof ApiError ? err.message : "Could not load documents.",
      );
      if (err instanceof ApiError && err.status === 401) setUnauthorized(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while any document is still being indexed; stops once none are
  // in flight, and gives up after 5 minutes with a visible notice.
  useEffect(() => {
    if (!documents.some(isIndexing)) {
      pollStartedAtRef.current = null;
      setPollTimedOut(false);
      return;
    }
    if (pollStartedAtRef.current === null) {
      pollStartedAtRef.current = Date.now();
    } else if (Date.now() - pollStartedAtRef.current > POLL_TIMEOUT_MS) {
      setPollTimedOut(true);
      return;
    }
    const timer = window.setTimeout(refresh, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [documents, refresh]);

  return {
    documents,
    loading,
    error: pollTimedOut
      ? "Indexing is taking longer than expected. Refresh the page to check again."
      : error,
    unauthorized,
    refresh,
  };
}
