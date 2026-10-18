import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminClientTelegramCard } from "./pages/AdminClientTelegramCard";

const OK = { ok: true, message: "Telegram: the bot works and delivers to the platform.", details: {} };

function setup(over: Record<string, unknown> = {}) {
  const props = {
    clientId: "alpha",
    tokenConfigured: false,
    connected: false,
    onSaveToken: vi.fn(async () => undefined),
    onConnect: vi.fn(async () => OK),
    onTest: vi.fn(async () => OK),
    onDisconnect: vi.fn(async () => undefined),
    ...over,
  };
  render(<AdminClientTelegramCard {...(props as unknown as ComponentProps<typeof AdminClientTelegramCard>)} />);
  return props;
}

const tokenInput = () => screen.getByLabelText("Bot token") as HTMLInputElement;
const conflict = (message: string) => Object.assign(new Error(message), { status: 409 });

afterEach(() => vi.restoreAllMocks());

describe("AdminClientTelegramCard", () => {
  it("shows the empty state with actions disabled", () => {
    setup();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test bot" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeDisabled();
    expect(screen.getByText(/invitation link/)).toBeInTheDocument();
  });

  it("never shows a stored token", () => {
    setup({ tokenConfigured: true, connected: true });
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(tokenInput().value).toBe("");
    expect(tokenInput().type).toBe("password");
  });

  it("saves then connects and clears the token from the DOM", async () => {
    const props = setup();
    const user = userEvent.setup();
    await user.type(tokenInput(), "123456:SECRET-TOKEN");
    await user.click(screen.getByRole("button", { name: "Save token" }));
    expect(await screen.findByText(/Connected\. Telegram: the bot works/)).toBeInTheDocument();
    expect(props.onSaveToken).toHaveBeenCalledWith("123456:SECRET-TOKEN");
    expect(props.onConnect).toHaveBeenCalledTimes(1);
    expect(tokenInput().value).toBe("");
    expect(document.body.innerHTML).not.toContain("SECRET-TOKEN");
    expect(screen.getByText("Configured")).toBeInTheDocument();
  });

  it("shows the failure message when connecting fails", async () => {
    setup({
      tokenConfigured: true,
      onConnect: vi.fn(async () => ({ ok: false, message: "Telegram: Telegram refused this bot token.", details: {} })),
    });
    await userEvent.setup().click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText(/Connection failed\. Telegram: Telegram refused this bot token/)).toBeInTheDocument();
    expect(screen.getByText("Configured, not connected")).toBeInTheDocument();
  });

  it.each([
    ["this client has no Telegram bot token yet", /No bot token is saved/],
    ["the platform has no public https address to receive Telegram messages on", /no public https address/],
    ["this bot cannot be used for this account", /already used by another client/],
  ])("explains the 409 %s", async (message, expected) => {
    setup({ tokenConfigured: true, onConnect: vi.fn(async () => Promise.reject(conflict(message))) });
    await userEvent.setup().click(screen.getByRole("button", { name: "Connect" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(expected);
    expect(alert.textContent).not.toContain(message);
  });

  it("runs the read-only test and shows its message", async () => {
    const props = setup({ tokenConfigured: true });
    await userEvent.setup().click(screen.getByRole("button", { name: "Test bot" }));
    expect(await screen.findByText(/Test passed\. Telegram: the bot works/)).toBeInTheDocument();
    expect(props.onTest).toHaveBeenCalledTimes(1);
    expect(props.onConnect).not.toHaveBeenCalled();
  });

  it("disconnects only after confirmation", async () => {
    const props = setup({ tokenConfigured: true, connected: true });
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(props.onDisconnect).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Bot disconnected.")).toBeInTheDocument();
    expect(props.onDisconnect).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Configured, not connected")).toBeInTheDocument();
  });

  it("replaces a token: save then reconnect, without re-showing either token", async () => {
    const order: string[] = [];
    const props = setup({
      tokenConfigured: true,
      connected: true,
      onSaveToken: vi.fn(async () => void order.push("save")),
      onConnect: vi.fn(async () => (order.push("connect"), OK)),
    });
    const user = userEvent.setup();
    await user.type(tokenInput(), "999:NEW-SECRET");
    await user.click(screen.getByRole("button", { name: "Replace token" }));
    expect(await screen.findByText(/Connected\. /)).toBeInTheDocument();
    expect(order).toEqual(["save", "connect"]);
    expect(props.onSaveToken).toHaveBeenCalledWith("999:NEW-SECRET");
    expect(document.body.innerHTML).not.toContain("NEW-SECRET");
  });

  it("keeps the token saved but reports a connect refusal after saving", async () => {
    setup({ onConnect: vi.fn(async () => Promise.reject(conflict("the platform has no public https address to receive Telegram messages on"))) });
    const user = userEvent.setup();
    await user.type(tokenInput(), "1:abc");
    await user.click(screen.getByRole("button", { name: "Save token" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no public https address/);
    expect(screen.getByText("Token saved.")).toBeInTheDocument();
    expect(tokenInput().value).toBe("");
  });

  it("is read-only for a non-owner", () => {
    setup({ canEdit: false, tokenConfigured: true });
    expect(tokenInput()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  });
});
