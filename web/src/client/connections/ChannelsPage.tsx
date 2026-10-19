/** The channels Revansa supports (Email, Telegram, WhatsApp), each with the same four answers:
 *  is it available to me, is it connected, what is left for me to do, and does Revansa have to step in.
 *  Read from `GET /client/v1/channel`; a channel is "connected" only when the server says it is ready.
 *  The wording and the choice of channel stay in the form below (`ClientChannelPage`); nothing
 *  technical (server, mailbox password, bot, webhook) is ever asked of the client. */

import { useEffect, useState } from "react";
import { Mail, MessageCircle, Send } from "lucide-react";
import { Link } from "react-router-dom";
import { useI18n, type MessageKey } from "../../i18n";
import { Chip } from "../../ui/Chip";
import { clientChannel, type ChannelState } from "../api";
import { ClientChannelPage } from "../ClientChannelPage";
import { ClientWhatsAppCard } from "../ClientWhatsAppCard";
import { useClientAuth } from "../ClientAuthContext";
import { Card, Note } from "../kit";

const CHANNELS = [
  { key: "email", icon: Mail },
  { key: "telegram", icon: Send },
  { key: "whatsapp", icon: MessageCircle },
] as const;

type Standing = "unavailable" | "choose" | "connected" | "waiting" | "todo";

function standingOf(state: ChannelState, key: string): Standing {
  if (!state.options.some((o) => o.key === key)) return "unavailable";
  if (state.channel !== key) return "choose";
  if (state.status === "ready") return "connected";
  return state.status === "waiting_platform" ? "waiting" : "todo";
}

const CHIP = {
  unavailable: { tint: "neutral", key: "rv.chan.s.unavailable" },
  choose: { tint: "action", key: "rv.chan.s.choose" },
  connected: { tint: "success", key: "rv.chan.s.connected" },
  waiting: { tint: "attention", key: "rv.chan.s.waiting" },
  todo: { tint: "attention", key: "rv.chan.s.todo" },
} as const;

export function ChannelsPage() {
  const { t } = useI18n();
  const { profile } = useClientAuth();
  const [state, setState] = useState<ChannelState | null>(null);

  // The form below saves on its own; this overview reads again whenever the user comes back to it.
  useEffect(() => {
    let cancelled = false;
    clientChannel().then(
      (value) => !cancelled && setState(value),
      () => !cancelled && setState(null),
    );
    const refresh = () => clientChannel().then((value) => !cancelled && setState(value), () => undefined);
    window.addEventListener("focus", refresh);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refresh);
    };
  }, []);

  return (
    <div className="rv-page">
      <div className="rv-head">
        <div className="rv-head__text">
          <h3 className="t-h2">{t("rv.chan.title")}</h3>
          <p>{t("rv.chan.intro")}</p>
        </div>
      </div>

      {state ? (
        <Card flush aria-label={t("rv.chan.title")}>
          <ul className="rv-list">
            {CHANNELS.map(({ key, icon: Icon }) => {
              const standing = standingOf(state, key);
              const chip = CHIP[standing];
              const whatsappMissing = key === "whatsapp" && standing !== "unavailable" && !profile?.whatsapp_number;
              return (
                <li key={key} className="rv-channel" data-testid={`channel-${key}`}>
                  <span className="rv-channel__icon" aria-hidden>
                    <Icon size={22} />
                  </span>
                  <div className="rv-row__main">
                    <span className="rv-row__title">{t(`rv.chan.name.${key}` as MessageKey)}</span>
                    <span className="rv-row__sub">
                      {standing === "unavailable" ? t("rv.chan.d.unavailable") : null}
                      {standing === "choose" ? t(`rv.chan.d.choose.${key}` as MessageKey) : null}
                      {standing === "connected" ? t("rv.chan.d.connected") : null}
                      {standing === "waiting" ? t("rv.chan.d.waiting") : null}
                      {standing === "todo" ? (state.status_message ?? t("rv.chan.d.todo")) : null}
                    </span>
                    {whatsappMissing ? (
                      <span className="rv-row__sub">
                        {t("rv.chan.d.whatsappNumber")}{" "}
                        <Link to="/profile" className="link-btn">
                          {t("rv.chan.d.addNumber")}
                        </Link>
                      </span>
                    ) : null}
                  </div>
                  <Chip tint={chip.tint}>{t(chip.key)}</Chip>
                </li>
              );
            })}
          </ul>
        </Card>
      ) : null}

      {state?.channel === "whatsapp" && state.options.some((o) => o.key === "whatsapp") ? (
        <ClientWhatsAppCard onChanged={() => void clientChannel().then(setState, () => undefined)} />
      ) : null}

      {state && state.options.length === 0 ? <Note tone="warn">{t("rv.chan.noneOpen")}</Note> : null}

      <ClientChannelPage embedded />
    </div>
  );
}
