import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, SseParser, api, apiFetch, setApiKey } from "./client";
import type { ChatResponse, StreamMeta } from "./types";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

async function capturedError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected the promise to reject");
}

beforeEach(() => {
  // node test env has no localStorage; provide a tiny in-memory one.
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  setApiKey(null);
});

describe("apiFetch", () => {
  it("parses JSON and sends a generated X-Request-ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const body = await apiFetch<{ ok: boolean }>("/health");

    expect(body).toEqual({ ok: true });
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("X-Request-ID")).toBeTruthy();
    expect(headers.get("X-API-Key")).toBeNull();
  });

  it("attaches the stored API key as X-API-Key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    setApiKey("secret-key");

    await apiFetch("/documents");

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("X-API-Key")).toBe("secret-key");
  });

  it("maps 401 string detail to ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Invalid or missing API key." }, { status: 401 }),
      ),
    );

    const error = await capturedError(apiFetch("/chat", { method: "POST" }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(401);
    expect(error.message).toContain("API key");
  });

  it("parses Retry-After on 429", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { detail: "Rate limit exceeded. Try again later." },
          { status: 429, headers: { "Retry-After": "60" } },
        ),
      ),
    );

    const error = await capturedError(apiFetch("/chat", { method: "POST" }));

    expect(error.status).toBe(429);
    expect(error.retryAfterSeconds).toBe(60);
  });

  it("extracts message and document_id from object detail (503)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { detail: { document_id: "abc-123", message: "Indexing failed." } },
          { status: 503 },
        ),
      ),
    );

    const error = await capturedError(
      apiFetch("/documents", { method: "POST" }),
    );

    expect(error.status).toBe(503);
    expect(error.message).toBe("Indexing failed.");
    expect(error.documentId).toBe("abc-123");
  });

  it("turns network failure into ApiError status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("network down")),
    );

    const error = await capturedError(apiFetch("/health"));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.message).toContain("reach the server");
  });
});

describe("conversation endpoints", () => {
  it("chat includes conversation_id (null for a new thread) in the body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        question: "q",
        answer: "a",
        status: "answered",
        sources: [],
        model: "m",
        retrieval_ms: 1,
        generation_ms: 2,
        conversation_id: "conv-1",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await api.chat({ question: "q", conversation_id: null });

    expect(response.conversation_id).toBe("conv-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chat");
    expect(JSON.parse(init.body as string)).toEqual({
      question: "q",
      conversation_id: null,
    });
  });

  it("listConversations fetches GET /conversations", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse([
        {
          id: "conv-1",
          title: "Leave policy",
          message_count: 2,
          created_at: "2026-10-19T10:00:00Z",
          updated_at: "2026-10-19T10:00:00Z",
        },
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const list = await api.listConversations();

    expect(list).toHaveLength(1);
    expect(list[0].title).toBe("Leave policy");
    expect(fetchMock.mock.calls[0][0]).toBe("/conversations");
  });

  it("getConversation fetches the detail by id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: "conv-1",
        title: "Leave policy",
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
        messages: [],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const detail = await api.getConversation("conv-1");

    expect(detail.id).toBe("conv-1");
    expect(fetchMock.mock.calls[0][0]).toBe("/conversations/conv-1");
  });

  it("deleteConversation issues DELETE and returns the confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ deleted: "true", conversation_id: "conv-1" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body = await api.deleteConversation("conv-1");

    expect(body.conversation_id).toBe("conv-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/conversations/conv-1");
    expect(init.method).toBe("DELETE");
  });

  it("renameConversation PATCHes the new title", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: "conv-1",
        title: "New title",
        message_count: 4,
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const summary = await api.renameConversation("conv-1", "New title");

    expect(summary.title).toBe("New title");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/conversations/conv-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ title: "New title" });
  });

  it("surfaces a 404 from getConversation as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Conversation not found." }, { status: 404 }),
      ),
    );

    const error = await capturedError(api.getConversation("gone"));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
  });
});

// ------------------------------------------------------------- SSE / stream

const DONE_PAYLOAD: ChatResponse = {
  question: "q",
  answer: "Employees receive 20 days.",
  status: "answered",
  sources: [],
  model: "gpt-4o-mini",
  retrieval_ms: 4,
  generation_ms: 120,
  conversation_id: null,
  message_id: null,
};

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("SseParser", () => {
  it("returns complete frames and buffers a partial one", () => {
    const parser = new SseParser();

    expect(parser.push('event: meta\ndata: {"a":1}\n\n')).toEqual([
      { event: "meta", data: '{"a":1}' },
    ]);
    // Frame split mid-payload: nothing until the terminator arrives.
    expect(parser.push('event: delta\ndata: {"text":')).toEqual([]);
    expect(parser.push('"hi"}\n\n')).toEqual([
      { event: "delta", data: '{"text":"hi"}' },
    ]);
  });

  it("parses several frames arriving in one chunk", () => {
    const parser = new SseParser();
    const frames = parser.push(
      'event: delta\ndata: {"text":"A"}\n\nevent: done\ndata: {"ok":true}\n\n',
    );
    expect(frames).toEqual([
      { event: "delta", data: '{"text":"A"}' },
      { event: "done", data: '{"ok":true}' },
    ]);
  });

  it("ignores keep-alive comments without data", () => {
    const parser = new SseParser();
    expect(parser.push(": ping\n\nevent: done\ndata: {}\n\n")).toEqual([
      { event: "done", data: "{}" },
    ]);
  });

  it("keeps data with escaped newlines on one line", () => {
    const parser = new SseParser();
    const frames = parser.push('event: delta\ndata: {"text":"a\\nb"}\n\n');
    expect(JSON.parse(frames[0].data).text).toBe("a\nb");
  });
});

describe("chatStream", () => {
  it("emits meta and deltas in order, resolves with the done payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        'event: meta\ndata: {"model":"m","retrieval_ms":3,"rewritten":false,"has_evidence":true}\n\n',
        'event: delta\ndata: {"text":"Employees "}\n\n',
        'event: delta\ndata: {"text":"receive 20 days."}\n\n',
        `event: done\ndata: ${JSON.stringify(DONE_PAYLOAD)}\n\n`,
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const deltas: string[] = [];
    const metas: StreamMeta[] = [];
    const result = await api.streamChat(
      { question: "q" },
      {
        onDelta: (text) => deltas.push(text),
        onMeta: (meta) => metas.push(meta),
      },
    );

    expect(metas).toHaveLength(1);
    expect(metas[0].rewritten).toBe(false);
    expect(deltas).toEqual(["Employees ", "receive 20 days."]);
    // done is authoritative - same object the server validated.
    expect(result).toEqual(DONE_PAYLOAD);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chat/stream");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ question: "q" });
    expect((init.headers as Headers).get("Accept")).toBe("text/event-stream");
  });

  it("replaces accumulated deltas with done.answer via the caller's contract", async () => {
    // The client contract: deltas are cosmetic, done decides the final text.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamResponse([
          'event: delta\ndata: {"text":"par"}\n\n',
          'event: delta\ndata: {"text":"tial wrong text"}\n\n',
          `event: done\ndata: ${JSON.stringify(DONE_PAYLOAD)}\n\n`,
        ]),
      ),
    );

    let shown = "";
    const final = await api.streamChat({ question: "q" }, {
      onDelta: (text) => {
        shown += text;
      },
    });
    shown = final.answer; // what Chat.tsx does on done

    expect(shown).toBe("Employees receive 20 days.");
  });

  it("rejects with ApiError when the server emits an error event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamResponse([
          'event: meta\ndata: {"model":"m","retrieval_ms":1,"rewritten":false,"has_evidence":true}\n\n',
          'event: error\ndata: {"message":"The answer service is unavailable. Try again."}\n\n',
        ]),
      ),
    );

    const error = await capturedError(api.streamChat({ question: "q" }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(503);
    expect(error.message).toContain("unavailable");
  });

  it("maps a pre-stream HTTP failure (429 + Retry-After) to ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { detail: "Rate limit exceeded. Try again later." },
          { status: 429, headers: { "Retry-After": "42" } },
        ),
      ),
    );

    const error = await capturedError(api.streamChat({ question: "q" }));

    expect(error.status).toBe(429);
    expect(error.retryAfterSeconds).toBe(42);
  });

  it("rejects when the stream ends without a done event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamResponse(['event: delta\ndata: {"text":"half"}\n\n']),
      ),
    );

    const error = await capturedError(api.streamChat({ question: "q" }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toContain("before an answer");
  });

  it("turns network failure into ApiError status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("network down")),
    );

    const error = await capturedError(api.streamChat({ question: "q" }));

    expect(error.status).toBe(0);
  });
});

describe("setFeedback", () => {
  it("PUTs the rating to the message feedback route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message_id: "msg-1", feedback: "up" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const body = await api.setFeedback("msg-1", "up");

    expect(body.feedback).toBe("up");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/chat/messages/msg-1/feedback");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ rating: "up" });
  });

  it("sends null to clear a previous vote", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ message_id: "msg-1", feedback: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.setFeedback("msg-1", null);

    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      rating: null,
    });
  });

  it("surfaces a 404 (foreign or deleted message) as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Message not found." }, { status: 404 }),
      ),
    );

    const error = await capturedError(api.setFeedback("gone", "up"));

    expect(error.status).toBe(404);
  });
});
