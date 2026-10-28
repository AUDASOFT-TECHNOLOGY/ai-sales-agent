/** Connections: the one place where the client connects what Revansa needs, and sees where each
 *  connection stands. Five connections at most, drawn the same way: how the agent writes to prospects
 *  (Email, WhatsApp, Telegram) and where it reads them (HubSpot, Google Sheets). The circuits and the
 *  prospect lists never show a connector's settings: when something is missing they say so and link here.
 *  Choosing the channel of a circuit is done on the circuit, not here. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { clientChannel, clientConnections, clientGmailStatus, clientHubSpotAccess, clientLanes, clientSheetsAccess, clientSourceState, clientTelegramAccount } from "../api";
import { clientWhatsAppStatus } from "../../whatsapp/api";
import { useI18n } from "../../i18n";
import { useClientAuth } from "../ClientAuthContext";
import { Card, Loading, PageHead } from "../kit";
import { EmailRow, HubSpotRow, SheetsRow } from "./OAuthRows";
import { TelegramRow, WhatsAppRow } from "./ChatRows";
import { summaryOf, viewsOf, type ConnectionData, type ConnectionView } from "./connectionModel";
import { googleReturnOf, hubspotReturnOf, sheetsReturnOf } from "./returns";

const value = <T,>(r: PromiseSettledResult<T>): T | null => (r.status === "fulfilled" ? r.value : null);

async function read(number: string | null): Promise<ConnectionData> {
  const [channel, gmail, whatsapp, telegram, hubspot, sheets, source, lanes, info] = await Promise.allSettled([
    clientChannel(),
    clientGmailStatus(),
    clientWhatsAppStatus(),
    clientTelegramAccount(),
    clientHubSpotAccess(),
    clientSheetsAccess(),
    clientSourceState(),
    clientLanes(),
    clientConnections(),
  ]);
  const src = value(source);
  const lns = value(lanes);
  const offered = src || lns ? [...new Set([...(src?.options ?? []).map((o) => o.key), ...(lns?.source_options ?? []).map((o) => o.key)])] : null;
  return {
    channel: value(channel),
    gmail: value(gmail),
    whatsapp: value(whatsapp),
    telegram: value(telegram),
    hubspot: value(hubspot),
    hints: value(info)?.hints ?? null,
    sheets: value(sheets),
    offered,
    shareWith: lns?.share_with ?? src?.google_sheets?.share_with ?? null,
    number,
  };
}

export function ConnectionsPage() {
  const { t } = useI18n();
  const { profile } = useClientAuth();
  const number = profile?.whatsapp_number ?? null;
  const [data, setData] = useState<ConnectionData | null>(null);
  // The provider's page sends the client back with `?google=...`: read once, then the address is cleaned.
  const [returned] = useState(() => ({
    google: googleReturnOf(window.location.search),
    sheets: sheetsReturnOf(window.location.search),
    hubspot: hubspotReturnOf(window.location.search),
  }));
  useEffect(() => {
    if (returned.google || returned.sheets || returned.hubspot) window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  }, [returned]);

  const reload = useCallback(async () => {
    setData(await read(number));
  }, [number]);

  useEffect(() => {
    let cancelled = false;
    void read(number).then((next) => !cancelled && setData(next));
    return () => {
      cancelled = true;
    };
  }, [number]);

  const views = useMemo(() => (data ? viewsOf(data) : []), [data]);
  if (!data) return <Loading />;

  const summary = summaryOf(views);
  const renderRow = (view: ConnectionView) => {
    switch (view.id) {
      case "email":
        return <EmailRow key={view.id} view={view} channel={data.channel} returned={returned.google} onChanged={reload} />;
      case "whatsapp":
        return <WhatsAppRow key={view.id} view={view} status={data.whatsapp} onChanged={reload} />;
      case "telegram":
        return <TelegramRow key={view.id} view={view} account={data.telegram} onChanged={reload} />;
      case "hubspot":
        return <HubSpotRow key={view.id} view={view} returned={returned.hubspot} onChanged={reload} />;
      case "sheets":
        return <SheetsRow key={view.id} view={view} shareWith={data.shareWith} returned={returned.sheets} onChanged={reload} />;
    }
  };
  const group = (key: "write" | "read", title: "rv.conn.group.write" | "rv.conn.group.read") => {
    const rows = views.filter((v) => v.group === key);
    if (rows.length === 0) return null;
    return (
      <section className="rv-page" style={{ gap: 12 }} aria-labelledby={`conn-${key}`}>
        <h3 className="rv-section-title" id={`conn-${key}`}>
          {t(title)}
        </h3>
        <Card flush>
          <ul className="rv-list">{rows.map(renderRow)}</ul>
        </Card>
      </section>
    );
  };

  return (
    <div className="rv-page">
      <PageHead title={t("rv.conn.title")} text={t("rv.conn.intro")} />
      {summary.total > 0 ? (
        <Card aria-label={t("rv.conn.title")}>
          <div className="rv-conn__summary" data-testid="connections-summary">
            <strong>{t("rv.conn.summary", { ready: summary.connected, total: summary.total })}</strong>
            <span className="c-2">{summary.next ? t("rv.conn.summary.next", { name: t(`rv.conn.name.${summary.next}`) }) : t("rv.conn.summary.done")}</span>
          </div>
        </Card>
      ) : null}
      {group("write", "rv.conn.group.write")}
      {group("read", "rv.conn.group.read")}
    </div>
  );
}
