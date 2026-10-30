import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiFetch, onUnauthorized } from "../api/client";
import type { AuthUser } from "./types";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

const sampleUser: AuthUser = {
  id: "u-1",
  email: "user@example.com",
  display_name: null,
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
  last_login_at: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("auth client", () => {
  it("authConfig reads public pre-login facts", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ auth_required: true, registration_enabled: false }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const config = await api.authConfig();

    expect(config).toEqual({ auth_required: true, registration_enabled: false });
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/auth/config");
  });

  it("login posts credentials and returns the account", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleUser));
    vi.stubGlobal("fetch", fetchMock);

    const user = await api.login("user@example.com", "hunter2!!");

    expect(user.email).toBe("user@example.com");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      email: "user@example.com",
      password: "hunter2!!",
    });
  });

  it("register posts to /auth/register", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(sampleUser));
    vi.stubGlobal("fetch", fetchMock);

    await api.register("user@example.com", "hunter2!!");

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/auth/register");
  });

  it("me surfaces 401 as ApiError when no session exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Not signed in." }, { status: 401 }),
      ),
    );

    try {
      await api.me();
      expect.unreachable("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(401);
    }
  });

  it("logout posts to /auth/logout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ logged_out: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    expect(await api.logout()).toEqual({ logged_out: true });
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/auth/logout");
  });
});

describe("onUnauthorized bus", () => {
  it("fires on a mid-session 401 and can unsubscribe", async () => {
    const handler = vi.fn();
    const unsubscribe = onUnauthorized(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Not signed in." }, { status: 401 }),
      ),
    );

    await apiFetch("/documents").catch(() => undefined);

    expect(handler).toHaveBeenCalledTimes(1);
    unsubscribe();

    await apiFetch("/documents").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1); // unsubscribed: no second call
  });

  it("does not fire on non-401 errors", async () => {
    const handler = vi.fn();
    const unsubscribe = onUnauthorized(handler);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "Rate limit exceeded." }, { status: 429 }),
      ),
    );

    await apiFetch("/chat", { method: "POST" }).catch(() => undefined);

    expect(handler).not.toHaveBeenCalled();
    unsubscribe();
  });
});
