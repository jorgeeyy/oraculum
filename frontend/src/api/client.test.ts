import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, setApiKey } from "./client";

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
