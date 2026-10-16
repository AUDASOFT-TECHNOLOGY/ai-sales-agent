/** The Dashboard, read from `GET /client/v1/summary` (counts of the client's own prospects) and
 *  `GET /client/v1/agent` (the agent's real state). It shows only what those two answers hold:
 *  no revenue, response rate, funnel or trend, because the platform stores none of them. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { clientSummary, type ProspectRow } from "../client/api";
import { crmFailureOf } from "../client/crmFailure";
import { useClientAgentStatus } from "../client/useClientAgentStatus";
import { useFetched } from "../client/useFetched";
import { Pulse } from "../agent/Pulse";
import { Button, LinkButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";

const STATUSES: Array<ProspectRow["status"]> = ["new", "active", "paused", "opted_out", "closed"];

export function Dashboard() {
  const { t, n, d } = useI18n();
  const { profile, logout } = useClientAuth();
  const agent = useClientAgentStatus();
  const [attempt, setAttempt] = useState(0);

  // `attempt` makes a retry a new request.
  const load = useCallback(() => clientSummary().then((summary) => ({ summary, attempt })), [attempt]);
  const { loading, value, error, failed } = useFetched(load);
  const failure = useMemo(() => (failed ? crmFailureOf(error) : null), [failed, error]);
  const signedOut = failure?.signedOut === true;
  useEffect(() => {
    if (signedOut) void logout();
  }, [signedOut, logout]);

  const summary = value?.summary;
  const tint = agent.pulse === "working" ? "agent" : agent.pulse === "not_live" && agent.failed ? "danger" : "attention";

  return (
    <div className="page page--focus dashboard">
      <div className="page-head">
        <h2 className="t-h1">{t("nav.command")}</h2>
      </div>

      <Glass level={3} tint={tint} className="hero" aria-labelledby="hero-title">
        <div className="hero__orb">
          <Pulse state={agent.pulse} size={56} className="hero__pulse" />
        </div>
        <div className="hero__text">
          <p className="hero__sentence" id="hero-title">
            {t("command.greeting", { name: profile?.name ?? "" })}
          </p>
          <p className="hero__status c-2">{t(agent.label)}</p>
        </div>
        <LinkButton to="/agent" variant="primary" trailing={<ArrowRight size={18} className="mirror" aria-hidden />}>
          {t("dashboard.live.seeAgent")}
        </LinkButton>
      </Glass>

      {failure ? (
        <>
          <p className="admin-alert admin-alert--danger" role="alert">
            {t(failure.key)}
          </p>
          <div>
            <Button onClick={() => setAttempt((a) => a + 1)}>{t("lanes.retry")}</Button>
          </div>
        </>
      ) : loading ? (
        <p className="c-3" role="status">
          {t("dashboard.live.loading")}
        </p>
      ) : summary && summary.total_prospects === 0 ? (
        <div className="empty" data-testid="dashboard-empty">
          <h3 className="t-h3">{t("dashboard.live.empty")}</h3>
        </div>
      ) : summary ? (
        <>
          <section aria-labelledby="dash-status-h" className="section">
            <div className="section__head">
              <h2 className="t-h2" id="dash-status-h">
                {t("dashboard.live.byStatus")}
              </h2>
            </div>
            <div className="dash-kpi-grid">
              <Glass level={2} tint="action" className="dash-kpi" data-testid="kpi-total">
                <span className="c-2 t-body-sm">{t("dashboard.live.prospects")}</span>
                <span className="t-numeral-l tabular">{n(summary.total_prospects)}</span>
                <Link to="/prospects" className="link-btn">
                  {t("dashboard.live.seeProspects")} <ArrowRight size={16} className="mirror" aria-hidden />
                </Link>
              </Glass>
              {STATUSES.map((status) => (
                <Glass key={status} level={2} className="dash-kpi" data-testid={`kpi-${status}`}>
                  <span className="c-2 t-body-sm">{t(`crm.status.${status}` as MessageKey)}</span>
                  <span className="t-numeral-l tabular">{n(summary.by_status[status])}</span>
                </Glass>
              ))}
            </div>
          </section>

          <section aria-labelledby="dash-upcoming-h" className="section">
            <div className="section__head">
              <h2 className="t-h2" id="dash-upcoming-h">
                {t("dashboard.live.upcoming")}
              </h2>
              <Chip tint="neutral">{t("dashboard.live.upcomingCount", { n: n(summary.upcoming_actions.scheduled) })}</Chip>
            </div>
            <Glass className="panel" data-testid="upcoming">
              {summary.upcoming_actions.scheduled === 0 ? (
                <p className="c-2">{t("dashboard.live.upcomingNone")}</p>
              ) : (
                <>
                  <ul className="stack">
                    {Object.entries(summary.upcoming_actions.by_type).map(([kind, count]) => (
                      <li key={kind} className="row">
                        <span className="t-label">{t(`crm.action.${kind}` as MessageKey)}</span>
                        <span className="tabular c-2">{n(count)}</span>
                      </li>
                    ))}
                  </ul>
                  {summary.upcoming_actions.next_due_at ? (
                    <p className="c-3 t-body-sm">
                      {t("dashboard.live.nextDue", { date: d(summary.upcoming_actions.next_due_at, { dateStyle: "medium", timeStyle: "short" }) })}
                    </p>
                  ) : null}
                </>
              )}
            </Glass>
          </section>
        </>
      ) : null}
    </div>
  );
}
