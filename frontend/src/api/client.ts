// Fetch wrapper: request IDs, optional API key, typed HTTP errors.

import type {
  ChatRequest,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
  DocumentContentResponse,
  DocumentRecord,
  FeedbackRating,
  FeedbackResponse,
  ReadyResponse,
  RetrievalRequest,
  RetrievalResponse,
  StreamError,
  StreamMeta,
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

async function errorFromResponse(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* non-JSON error body */
  }
  const { message, documentId } = extractDetail(body);
  const retryHeader = response.headers.get("Retry-After");
  const retryAfter = retryHeader ? Number(retryHeader) : undefined;
  return new ApiError(
    response.status,
    message,
    Number.isFinite(retryAfter) ? retryAfter : undefined,
    documentId,
  );
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
    throw await errorFromResponse(response);
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) });
}

// ------------------------------------------------------------- SSE parsing

export interface SseFrame {
  event: string;
  data: string;
}

function parseFrame(block: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  // No data line -> comment or keep-alive; not a frame we care about.
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * Incremental Server-Sent Events decoder: feed it raw text chunks as they
 * arrive, get back only the frames that are complete. A frame split across
 * chunk boundaries stays buffered until its blank-line terminator shows up.
 */
export class SseParser {
  private buffer = "";

  push(chunk: string): SseFrame[] {
    if (chunk) this.buffer += chunk;
    const blocks = this.buffer.split("\n\n");
    this.buffer = blocks.pop() ?? ""; // trailing block may be partial
    const frames: SseFrame[] = [];
    for (const block of blocks) {
      const frame = parseFrame(block);
      if (frame) frames.push(frame);
    }
    return frames;
  }
}

export interface ChatStreamHandlers {
  /** Retrieval finished - answer text may follow. */
  onMeta?: (meta: StreamMeta) => void;
  /** Incremental answer text (cosmetic; `done` is authoritative). */
  onDelta?: (text: string) => void;
}

/**
 * POST /chat/stream and consume the SSE reply.
 *
 * Resolves with the authoritative `done` payload (validated citations) and
 * rejects with ApiError on pre-stream HTTP failures, an `error` event, or a
 * stream that ends without `done`. Deltas are best-effort UX: callers should
 * render them live but always replace the text with the resolved answer.
 */
export async function chatStream(
  req: ChatRequest,
  handlers: ChatStreamHandlers = {},
): Promise<ChatResponse> {
  const headers = new Headers();
  headers.set("X-Request-ID", newRequestId());
  const key = getApiKey();
  if (key) headers.set("X-API-Key", key);
  headers.set("Content-Type", "application/json");
  headers.set("Accept", "text/event-stream");

  let response: Response;
  try {
    response = await fetch("/chat/stream", {
      method: "POST",
      headers,
      body: JSON.stringify(req),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is it running?");
  }
  if (!response.ok) throw await errorFromResponse(response);
  if (!response.body) {
    throw new ApiError(0, "This browser cannot stream responses.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let result: ChatResponse | null = null;
  let failure: ApiError | null = null;

  try {
    while (!result && !failure) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        try {
          if (frame.event === "meta") {
            handlers.onMeta?.(JSON.parse(frame.data) as StreamMeta);
          } else if (frame.event === "delta") {
            const payload = JSON.parse(frame.data) as { text: string };
            handlers.onDelta?.(payload.text);
          } else if (frame.event === "done") {
            result = JSON.parse(frame.data) as ChatResponse;
          } else if (frame.event === "error") {
            const payload = JSON.parse(frame.data) as StreamError;
            failure = new ApiError(503, payload.message);
          }
        } catch {
          // Malformed frame: ignore it. The `done`/`error` contract still
          // decides the outcome, so a stray frame can't fake success.
        }
        if (result || failure) break;
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }

  if (failure) throw failure;
  if (!result) {
    throw new ApiError(0, "The stream ended before an answer arrived.");
  }
  return result;
}

export const api = {
  chat: (req: ChatRequest) => post<ChatResponse>("/chat", req),
  streamChat: (req: ChatRequest, handlers: ChatStreamHandlers = {}) =>
    chatStream(req, handlers),
  setFeedback: (messageId: string, rating: FeedbackRating) =>
    apiFetch<FeedbackResponse>(`/chat/messages/${messageId}/feedback`, {
      method: "PUT",
      body: JSON.stringify({ rating }),
    }),
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
