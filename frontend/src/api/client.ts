// Fetch wrapper: request IDs, optional API key, typed HTTP errors.

import type {
  ChatRequest,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  DocumentContentResponse,
  DocumentRecord,
  ReadyResponse,
  RetrievalRequest,
  RetrievalResponse,
  UploadDocumentResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly retryAfterSeconds?: number,
    readonly documentId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const API_KEY_STORAGE = "rag.apiKey";

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(API_KEY_STORAGE);
  } catch {
    return null; // non-browser context (tests)
  }
}

export function setApiKey(key: string | null): void {
  try {
    if (key) {
      localStorage.setItem(API_KEY_STORAGE, key);
    } else {
      localStorage.removeItem(API_KEY_STORAGE);
    }
  } catch {
    /* ignore: key simply won't persist */
  }
}

export function newRequestId(): string {
  const part = () =>
    Math.random().toString(16).slice(2, 10) +
    Date.now().toString(16).slice(-6);
  return `${part()}${part()}`;
}

function extractDetail(body: unknown): {
  message: string;
  documentId?: string;
} {
  if (typeof body === "string") return { message: body };
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    if (typeof obj.detail === "string") return { message: obj.detail };
    if (obj.detail && typeof obj.detail === "object") {
      const detail = obj.detail as Record<string, unknown>;
      const message =
        typeof detail.message === "string"
          ? detail.message
          : typeof detail.detail === "string"
            ? detail.detail
            : "Request failed.";
      const documentId =
        typeof detail.document_id === "string" ? detail.document_id : undefined;
      return { message, documentId };
    }
    if (Array.isArray(obj.detail)) {
      // FastAPI validation errors: [{loc, msg, ...}, ...]
      const first = obj.detail[0] as { msg?: string } | undefined;
      return { message: first?.msg ? `Validation: ${first.msg}` : "Validation error." };
    }
  }
  return { message: "Request failed." };
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Request-ID", newRequestId());
  const key = getApiKey();
  if (key) headers.set("X-API-Key", key);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is it running?");
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* non-JSON error body */
    }
    const { message, documentId } = extractDetail(body);
    const retryHeader = response.headers.get("Retry-After");
    const retryAfter = retryHeader ? Number(retryHeader) : undefined;
    throw new ApiError(
      response.status,
      message,
      Number.isFinite(retryAfter) ? retryAfter : undefined,
      documentId,
    );
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export const api = {
  chat: (req: ChatRequest) => post<ChatResponse>("/chat", req),
  retrieve: (req: RetrievalRequest) => post<RetrievalResponse>("/retrieve", req),
  listConversations: () => apiFetch<ConversationSummary[]>("/conversations"),
  getConversation: (id: string) =>
    apiFetch<ConversationDetail>(`/conversations/${id}`),
  deleteConversation: (id: string) =>
    apiFetch<{ deleted: string; conversation_id: string }>(
      `/conversations/${id}`,
      { method: "DELETE" },
    ),
  renameConversation: (id: string, title: string) =>
    apiFetch<ConversationSummary>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  listDocuments: () => apiFetch<DocumentRecord[]>("/documents"),
  getContent: (id: string) =>
    apiFetch<DocumentContentResponse>(`/documents/${id}/content`),
  deleteDocument: (id: string) =>
    apiFetch<{ deleted: boolean; document_id: string }>(
      `/documents/${id}`,
      { method: "DELETE" },
    ),
  upload: async (file: File): Promise<UploadDocumentResponse> => {
    const form = new FormData();
    form.append("file", file);
    const headers = new Headers();
    headers.set("X-Request-ID", newRequestId());
    const key = getApiKey();
    if (key) headers.set("X-API-Key", key);
    const response = await fetch("/documents", {
      method: "POST",
      headers,
      body: form,
    });
    if (!response.ok) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        /* ignore */
      }
      const { message, documentId } = extractDetail(body);
      throw new ApiError(response.status, message, undefined, documentId);
    }
    return (await response.json()) as UploadDocumentResponse;
  },
  ready: () => apiFetch<ReadyResponse>("/health/ready"),
};
