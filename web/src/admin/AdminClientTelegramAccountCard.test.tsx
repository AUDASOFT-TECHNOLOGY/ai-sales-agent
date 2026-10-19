import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AdminClientTelegramAccountCard,
  type TelegramAccountAdminState,
} from "./pages/AdminClientTelegramAccountCard";

const base: TelegramAccountAdminState = {
  platform_configured: true,
  platform_missing: [],
  state: "connected",
  limited: false,
  message: "The account is connected.",
};

function setup(initial: Partial<TelegramAccountAdminState> = {}, canEdit = true) {
  const props = {
    canEdit,
    initial: { ...base, ...initial },
    onDisconnect: vi.fn(async () => ({ ...base, state: "not_connected" as const, message: "The client has not connected its Telegram account." })),
    onClearLimit: vi.fn(async () => ({ ...base, limited: false })),
  };
  render(<AdminClientTelegramAccountCard {...props} />);
  return props;
}

afterEach(() => vi.restoreAllMocks());

describe("AdminClientTelegramAccountCard", () => {
  it("shows where the account stands and never a number, a name or a session", () => {
    setup();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("The account is connected.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/session|\+\d{6}/i);
  });

  it("names what the platform still lacks", () => {
    setup({ platform_configured: false, platform_missing: ["Telegram application id (api_id)"], state: "not_connected" });
    expect(screen.getByText(/Telegram application id \(api_id\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect account" })).toBeDisabled();
  });

  it("disconnects after a confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const props = setup();
    await userEvent.click(screen.getByRole("button", { name: "Disconnect account" }));
    expect(props.onDisconnect).toHaveBeenCalledOnce();
    expect(await screen.findByText("Not connected")).toBeInTheDocument();
  });

  it("does nothing when the Admin does not confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const props = setup();
    await userEvent.click(screen.getByRole("button", { name: "Disconnect account" }));
    expect(props.onDisconnect).not.toHaveBeenCalled();
  });

  it("lifts Telegram's limit only when there is one", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const props = setup({ limited: true });
    expect(screen.getByText("Limited by Telegram")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Lift Telegram limit" }));
    expect(props.onClearLimit).toHaveBeenCalledOnce();
    expect(screen.queryByText("Limited by Telegram")).not.toBeInTheDocument();
  });

  it("keeps the buttons off for someone who is not an owner", () => {
    setup({ limited: true }, false);
    expect(screen.getByRole("button", { name: "Disconnect account" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Lift Telegram limit" })).toBeDisabled();
  });

  it("says when an action failed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const props = setup();
    props.onDisconnect.mockRejectedValueOnce(new Error("boom"));
    await userEvent.click(screen.getByRole("button", { name: "Disconnect account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not disconnect the account.");
  });
});
