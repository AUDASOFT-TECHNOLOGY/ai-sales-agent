import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nProvider } from "../i18n";
import { ClientMailboxCard } from "./ClientMailboxCard";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const info = (over: Record<string, unknown> = {}) => ({
  status: "active",
  message: "Your mailbox is active.",
  reason: null,
  address: "me@mail.example.org",
  login: "me@mail.example.org",
  credentials_shared: true,
  password_revealable: true,
  updated_at: null,
  ...over,
});

function stub(body: unknown, reveal: Response = json(200, { password: "Secret-1", shown_for_seconds: 60 })) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /client/v1/channel/mailbox") return json(200, body);
      if (key === "POST /client/v1/channel/mailbox/reveal-password") return reveal;
      throw new Error(`unexpected ${key}`);
    }),
  );
}

const renderCard = () =>
  render(
    <I18nProvider>
      <ClientMailboxCard />
    </I18nProvider>,
  );

afterEach(() => vi.unstubAllGlobals());

describe("ClientMailboxCard", () => {
  it("never shows the password until asked, then hides it again", async () => {
    stub(info());
    renderCard();
    expect((await screen.findAllByText("me@mail.example.org", { selector: "dd" })).length).toBe(2);
    expect(screen.queryByText("Secret-1")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /show the password|afficher le mot de passe/i }));
    expect(await screen.findByText("Secret-1")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /hide|masquer/i }));
    expect(screen.queryByText("Secret-1")).toBeNull();
  });

  it("offers no password when the team keeps the login back", async () => {
    stub(info({ login: null, credentials_shared: false, password_revealable: false }));
    renderCard();
    await screen.findAllByText("me@mail.example.org", { selector: "dd" });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("tells a request in progress and an error, and says nothing when never requested", async () => {
    stub(info({ status: "in_progress", message: "Being created.", address: null, password_revealable: false }));
    const first = renderCard();
    expect(await screen.findByText("Being created.", { exact: false })).toBeTruthy();
    first.unmount();
    stub(info({ status: "not_requested" }));
    const second = renderCard();
    await new Promise((r) => setTimeout(r, 20));
    expect(second.container.innerHTML).toBe("");
  });

  it("explains a limit without repeating anything", async () => {
    stub(info(), json(429, { detail: "too many requests" }));
    renderCard();
    await userEvent.click(await screen.findByRole("button", { name: /show the password|afficher le mot de passe/i }));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("can show a mailbox that was never requested", async () => {
    stub(info({ status: "not_requested", message: "No mailbox requested.", address: null, login: null, password_revealable: false }));
    render(
      <I18nProvider>
        <ClientMailboxCard showNotRequested />
      </I18nProvider>,
    );
    expect(await screen.findByText("Not requested")).toBeTruthy();
  });
});
