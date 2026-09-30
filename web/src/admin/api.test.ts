import { afterEach, describe, expect, it, vi } from "vitest";
import { AdminApiError, adminLogin, adminLogout, adminMe, adminUpdateClientLLM } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("adminMe", () => {
  it("returns the session on 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(200, { username: "alice", role: "owner", client_ids: [] })),
    );
    await expect(adminMe()).resolves.toEqual({ username: "alice", role: "owner", client_ids: [] });
  });

  it("throws AdminApiError with the server's detail message on failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(401, { detail: "session expired or invalid" })));
    await expect(adminMe()).rejects.toMatchObject(
      new AdminApiError(401, "session expired or invalid"),
    );
  });

  it("falls back to a generic message when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>not json</html>", { status: 500, statusText: "Server Error" })),
    );
    const error = await adminMe().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AdminApiError);
    expect((error as AdminApiError).status).toBe(500);
  });

  it("reports a network failure as status 0, not a thrown TypeError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const error = await adminMe().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AdminApiError);
    expect((error as AdminApiError).status).toBe(0);
  });
});

describe("adminLogin", () => {
  it("sends credentials as JSON and includes cookies", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
    fetchMock.mockResolvedValue(jsonResponse(200, { username: "alice", role: "editor", client_ids: ["alpha"] }));
    vi.stubGlobal("fetch", fetchMock);
    await adminLogin("alice", "secret");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/admin/v1/auth/login");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({ username: "alice", password: "secret" });
  });
});

describe("adminUpdateClientLLM", () => {
  it("PUTs the provider and model as JSON and includes cookies", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
    fetchMock.mockResolvedValue(jsonResponse(200, { provider: "openai", model: "gpt-5", api_key_env: "OPENAI_API_KEY" }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await adminUpdateClientLLM("alpha", { provider: "openai", model: "gpt-5" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/admin/v1/clients/alpha/llm");
    expect(init.method).toBe("PUT");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body))).toEqual({ provider: "openai", model: "gpt-5" });
    expect(result).toEqual({ provider: "openai", model: "gpt-5", api_key_env: "OPENAI_API_KEY" });
  });

  it("surfaces the server's error detail, e.g. an unwired provider or disallowed model", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(400, { detail: "llm.provider 'anthropic' is not wired in this deployment" })),
    );
    await expect(adminUpdateClientLLM("alpha", { provider: "anthropic", model: "claude" })).rejects.toMatchObject(
      new AdminApiError(400, "llm.provider 'anthropic' is not wired in this deployment"),
    );
  });
});

describe("adminLogout", () => {
  it("posts to /auth/logout", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
    fetchMock.mockResolvedValue(jsonResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    await adminLogout();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/admin/v1/auth/logout");
    expect(init.method).toBe("POST");
  });
});
