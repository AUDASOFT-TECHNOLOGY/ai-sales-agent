/** One connection, drawn the same way for every connector: the service's mark, its name, where it stands
 *  for the client (connected or not), one sentence, and what the client does next under it. Nothing here
 *  depends on whether Revansa has finished its own configuration. */

import type { ReactNode } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Chip } from "../../ui/Chip";

/** What the client sees: its connection is made, or it is not. `reconnect` is the second with another word. */
export type Standing = "connected" | "todo";

const CHIP: Record<Standing, { tint: "success" | "danger"; key: MessageKey }> = {
  connected: { tint: "success", key: "rv.conn.st.connected" },
  todo: { tint: "danger", key: "rv.conn.st.todo" },
};

export interface ConnectionRowProps {
  id: string;
  icon: ReactNode;
  name: string;
  standing: Standing;
  reconnect?: boolean;
  detail: ReactNode;
  /** Beside the state: what can be done to a connection that exists (disconnect). */
  action?: ReactNode;
  /** Under the row: the form that connects it, a code to type, a result. */
  children?: ReactNode;
}

export function ConnectionRow({ id, icon, name, standing, reconnect, detail, action, children }: ConnectionRowProps) {
  const { t } = useI18n();
  const chip = CHIP[standing];
  return (
    <li data-testid={`connection-${id}`} data-standing={standing}>
      <div className="rv-channel">
        <span className="rv-channel__icon" aria-hidden>
          {icon}
        </span>
        <div className="rv-row__main">
          <span className="rv-row__title">{name}</span>
          <span className="rv-row__sub" dir="auto">
            {detail}
          </span>
        </div>
        <Chip tint={chip.tint}>{t(standing === "todo" && reconnect ? "rv.conn.st.reconnect" : chip.key)}</Chip>
        {action ? <div className="rv-actions">{action}</div> : null}
      </div>
      {children ? <div className="rv-conn__body">{children}</div> : null}
    </li>
  );
}
