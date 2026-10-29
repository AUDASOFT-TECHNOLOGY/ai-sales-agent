/** Under the Email row, for an account whose mailbox Revansa provides: the request to activate it and
 *  whether emails can go out today. Writing as a name and a subject is done in the rules, not here. */

import { Link } from "react-router-dom";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { clientRequestEmailActivation, type ChannelState } from "../api";
import { Note } from "../kit";
import { useAddress } from "../prepare/links";
import { RowError, useRowState } from "./connectionKit";

export function EmailActivation({ channel, onChanged }: { channel: ChannelState; onChanged: () => Promise<void> | void }) {
  const { t } = useI18n();
  const address = useAddress();
  const s = useRowState();
  const email = channel.email;
  if (!email) return null;
  const activation = email.activation;
  const sending = email.sending;
  const canAsk = !activation || activation.status === "rejected";
  const named = email.sender_name_confirmed;
  return (
    <>
      {sending && sending.state !== "ok" ? <Note>{t(`channel.sending.${sending.state}` as MessageKey, { sent: sending.sent_today ?? 0, limit: sending.daily_limit ?? 0 })}</Note> : null}
      {activation ? (
        <Note tone={activation.status === "rejected" ? "danger" : "info"} title={t(`channel.activation.${activation.status}` as MessageKey)}>
          {activation.status === "rejected" && activation.reason ? t("channel.activation.reason", { reason: activation.reason }) : null}
        </Note>
      ) : null}
      {canAsk ? (
        <div className="rv-actions">
          <Button variant="primary" loading={s.busy} disabled={!named} onClick={() => void s.run(clientRequestEmailActivation, onChanged)}>
            {t(activation ? "channel.activation.requestAgain" : "channel.activation.request")}
          </Button>
          {named ? null : (
            <span className="c-3 t-body-sm">
              {t("channel.activation.needName")}{" "}
              <Link to={address({ to: "rules" })} className="link-btn">
                {t("rv.conn.act.rules")}
              </Link>
            </span>
          )}
        </div>
      ) : null}
      <RowError error={s.error} />
    </>
  );
}
