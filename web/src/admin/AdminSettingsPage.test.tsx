import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AdminAuthProvider } from "./AdminAuthContext";
import { AdminRoute } from "./AdminRoute";
import { AdminShell } from "./AdminShell";
import { AdminSettingsPage } from "./pages/AdminSettingsPage";

const UNRESTRICTED = {
  read_only: true,
  restricted: false,
  providers: [{ provider: "openai", wired: true, allowed_models: null }],
};

const RESTRICTED = {
  read_only: true,
  restricted: true,
  providers: [
    { provider: "openai", wired: true, allowed_models: ["test-model"] },
    { provider: "anthropic", wired: false, allowed_models: ["claude"] },
  ],
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch(llmSettings: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/auth/me")) {
        return jsonResponse(200, { username: "alice", role: "owner", client_ids: [] });
      }
      if (url.endsWith("/settings/llm")) {
        return jsonResponse(200, llmSettings);
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
}

function renderApp() {
  render(
    <MemoryRouter initialEntries={["/admin/settings"]}>
      <AdminAuthProvider>
        <Routes>
          <Route
            path="/admin"
            element={
              <AdminRoute>
                <AdminShell />
              </AdminRoute>
            }
          >
            <Route path="settings" element={<AdminSettingsPage />} />
          </Route>
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminSettingsPage", () => {
  it("shows the unrestricted default: any model accepted from a wired provider", async () => {
    stubFetch(UNRESTRICTED);
    renderApp();
    expect(await screen.findByText("openai")).toBeInTheDocument();
    expect(screen.getByText("Wired")).toBeInTheDocument();
    expect(screen.getByText("Unrestricted")).toBeInTheDocument();
    expect(screen.getByText("Any")).toBeInTheDocument();
  });

  it("shows allow-listed models per provider, including a provider that isn't wired", async () => {
    stubFetch(RESTRICTED);
    renderApp();
    expect(await screen.findByText("Allow-list active")).toBeInTheDocument();
    expect(screen.getByText("test-model")).toBeInTheDocument();
    expect(screen.getByText("anthropic")).toBeInTheDocument();
    expect(screen.getByText("claude")).toBeInTheDocument();
    expect(screen.getByText("Not wired")).toBeInTheDocument();
  });

  it("states clearly that this is read-only", async () => {
    stubFetch(UNRESTRICTED);
    renderApp();
    await screen.findByText("openai");
    expect(screen.getByRole("note")).toHaveTextContent(/Read-only/);
  });

  it("never shows a secret value", async () => {
    stubFetch(RESTRICTED);
    renderApp();
    await screen.findByText("openai");
    expect(document.body.textContent).not.toMatch(/sk-test|_env/);
  });
});
