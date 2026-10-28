/** Where each connection stands, from the client's side only. Pure: the screen reads, this decides.
 *
 *  A connection has two sides: Revansa's (the platform's application, the channel opened for the client)
 *  and the client's (which account it wants, then its permission on the provider's page). The client's
 *  side is independent of Revansa's: it can always state its account and ask to connect, and what it
 *  gives is kept. Whether Revansa has finished matters for one thing only, the moment the provider's page
 *  can open (`pending`), and only the circuits show it (a channel or a source stays greyed until then).
 *  Here a connection is `connected` (the server says the provider accepted the client's authorisation) or
 *  `todo` (not connected yet), nothing else. */

import type { MessageKey } from "../../i18n";
import type { ClientWhatsAppStatus } from "../../whatsapp/api";
import type { ChannelState, ConnectionsInfo, GmailStatus, HubSpotAccess, SheetsAccess, TelegramAccount } from "../api";
import type { Standing } from "./ConnectionRow";

export type ConnectionId = "email" | "whatsapp" | "telegram" | "hubspot" | "sheets";
/** The account the client states for a connection (WhatsApp's is the number of its profile). */
export type Field = "gmail" | "sheets" | "hubspot" | "telegram" | "whatsapp";

export interface ConnectionData {
  channel: ChannelState | null;
  gmail: GmailStatus | null;
  whatsapp: ClientWhatsAppStatus | null;
  telegram: TelegramAccount | null;
  hubspot: HubSpotAccess | null;
  sheets: SheetsAccess | null;
  /** What the client said about the accounts it wants (`null`: not read). */
  hints: ConnectionsInfo["hints"] | null;
  /** The sources Revansa offers this account (`null`: not read). */
  offered: string[] | null;
  /** A spreadsheet shared by hand with a service account instead of the client's own Google account. */
  shareWith: string | null;
  /** The client's own WhatsApp number, from its profile. */
  number: string | null;
}

export interface ConnectionView {
  id: ConnectionId;
  group: "write" | "read";
  standing: Standing;
  /** `todo` read as "to reconnect": the connection existed and needs the client's permission again. */
  reconnect: boolean;
  detail: MessageKey;
  vars?: Record<string, string>;
  /** The account the client states for this connection, until it is connected. */
  field: Field | null;
  /** What the client stated, if anything. */
  value: string | null;
  /** The provider's page cannot open yet: connecting then keeps what the client gave. Never shown as such. */
  pending: boolean;
  /** The mailbox Revansa provides is in use: it has no account to state. */
  provided?: boolean;
}

type Draft = Pick<ConnectionView, "standing" | "detail"> & Partial<Omit<ConnectionView, "id" | "group" | "standing" | "detail">>;
const make = (id: ConnectionId, group: ConnectionView["group"], p: Draft): ConnectionView => ({ id, group, reconnect: false, field: null, value: null, pending: false, ...p });

/** The Admin has opened the channel for this client. */
const opens = (channel: ChannelState | null, key: string): boolean => channel === null || channel.options.some((o) => o.key === key);

/** The client's own side for a connection that is not made yet: nothing said, or something said. */
function toConnect(id: ConnectionId, group: ConnectionView["group"], field: Field, value: string | null, pending: boolean, keys: { todo: MessageKey; saved: MessageKey; reconnect?: MessageKey }, reconnect = false, vars?: Record<string, string>): ConnectionView {
  const detail = reconnect && keys.reconnect ? keys.reconnect : value ? keys.saved : keys.todo;
  return make(id, group, { standing: "todo", reconnect, detail, vars: { ...vars, value: value ?? "" }, field, value, pending });
}

function email(d: ConnectionData): ConnectionView {
  const value = d.hints?.gmail ?? null;
  const mode = d.channel?.channel === "email" ? d.channel.email?.mode : undefined;
  if (mode === "platform" || mode === "custom") {
    // The mailbox Revansa provides: the client asks for it, Revansa sets it up.
    const ready = d.channel?.status === "ready";
    return make("email", "write", { standing: ready ? "connected" : "todo", detail: ready ? "rv.conn.d.email.platformReady" : "rv.conn.d.email.platformTodo", provided: true });
  }
  const g = d.gmail;
  const pending = !g || g.state === "unavailable" || !opens(d.channel, "email");
  const keys = { todo: "rv.conn.d.email.todo", saved: "rv.conn.d.email.saved", reconnect: "rv.conn.d.email.reconnect" } as const;
  if (g?.state === "connected") return make("email", "write", { standing: "connected", detail: "rv.conn.d.email.connected", vars: { address: g.address ?? "" } });
  return toConnect("email", "write", "gmail", value, pending, keys, g?.state === "reauth_required", { address: g?.address ?? "" });
}

function whatsapp(d: ConnectionData): ConnectionView {
  const w = d.whatsapp;
  const number = w?.number ?? d.number ?? null;
  if (w?.state === "connected") return make("whatsapp", "write", { standing: "connected", detail: "rv.conn.d.whatsapp.connected", vars: { name: w.verified_name ?? number ?? "", number: number ?? "" } });
  const pending = !w || w.state === "unavailable" || !opens(d.channel, "whatsapp");
  const keys = { todo: "rv.conn.d.whatsapp.todo", saved: "rv.conn.d.whatsapp.saved", reconnect: "rv.conn.d.whatsapp.reconnect" } as const;
  return toConnect("whatsapp", "write", "whatsapp", number, pending, keys, w?.state === "reauth_required");
}

function telegram(d: ConnectionData): ConnectionView {
  const a = d.telegram;
  const value = d.hints?.telegram ?? null;
  if (a?.state === "connected") return make("telegram", "write", { standing: "connected", detail: a.limited ? "rv.conn.d.telegram.limited" : "rv.conn.d.telegram.connected" });
  const pending = !a || !a.available || !opens(d.channel, "telegram");
  const keys = { todo: "rv.conn.d.telegram.todo", saved: "rv.conn.d.telegram.saved", reconnect: "rv.conn.d.telegram.reconnect" } as const;
  return toConnect("telegram", "write", "telegram", value, pending, keys, a?.state === "reauth_required");
}

function hubspot(d: ConnectionData): ConnectionView | null {
  if (!d.offered?.includes("hubspot")) return null;
  const h = d.hubspot;
  const value = d.hints?.hubspot ?? null;
  if (h?.state === "connected") return make("hubspot", "read", { standing: "connected", detail: "rv.conn.d.hubspot.connected", vars: { account: h.account ?? "" } });
  const keys = { todo: "rv.conn.d.hubspot.todo", saved: "rv.conn.d.hubspot.saved", reconnect: "rv.conn.d.hubspot.reconnect" } as const;
  return toConnect("hubspot", "read", "hubspot", value, !h || h.state === "unavailable", keys, h?.state === "reauth_required");
}

function sheets(d: ConnectionData): ConnectionView | null {
  if (!d.offered?.includes("google_sheets")) return null;
  if (d.shareWith) return make("sheets", "read", { standing: "todo", detail: "rv.conn.d.sheets.share" });
  const s = d.sheets;
  const value = d.hints?.sheets ?? null;
  if (s?.state === "connected") return make("sheets", "read", { standing: "connected", detail: "rv.conn.d.sheets.connected", vars: { address: s.address ?? "" } });
  const keys = { todo: "rv.conn.d.sheets.todo", saved: "rv.conn.d.sheets.saved", reconnect: "rv.conn.d.sheets.reconnect" } as const;
  return toConnect("sheets", "read", "sheets", value, !s || s.state === "unavailable", keys, s?.state === "reauth_required");
}

/** Every connection that exists for this account, in the order they are shown. */
export function viewsOf(d: ConnectionData): ConnectionView[] {
  return [email(d), whatsapp(d), telegram(d), hubspot(d), sheets(d)].filter((v): v is ConnectionView => v !== null);
}

export interface Summary {
  connected: number;
  total: number;
  /** The first connection the client has not made yet. */
  next: ConnectionId | null;
}

export function summaryOf(views: ConnectionView[]): Summary {
  return {
    connected: views.filter((v) => v.standing === "connected").length,
    total: views.length,
    next: views.find((v) => v.standing === "todo")?.id ?? null,
  };
}
