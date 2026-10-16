/** The client's agent: its state, whether it is ready, and the start / pause switch. Everything comes
 *  from the server (`/client/v1/agent`); nothing is kept here. "Started" is shown only after the
 *  server answered that it is, and a start the server refuses lists what is still missing. */

import { useCallback, useEffect, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Glass } from "../ui/Glass";
import { useClientAuth } from "./ClientAuthContext";
import { clientAgent, clientPauseAgent, clientStartAgent, type AgentState } from "./api";
import { settingsFailureOf, type SettingsFailure } from "./settingsFailure";

export function ClientAgentPage() {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [agent, setAgent] = useState<AgentState | null>(null);
  const [loadFailure, setLoadFailure] = useState<SettingsFailure | null>(null);
  const [failure, setFailure] = useState<SettingsFailure | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [busy, setBusy] = useState<null | "load" | "start" | "pause">(null);

  const apply = useCallback(
    (result: PromiseSettledResult<AgentState>) => {
      if (result.status === "fulfilled") {
        setAgent(result.value);
        setLoadFailure(null);
        return;
      }
      const problem = settingsFailureOf(result.reason);
      setLoadFailure(problem);
      if (problem.signedOut) void logout();
    },
    [logout],
  );

  const load = useCallback(async () => apply((await Promise.allSettled([clientAgent()]))[0]!), [apply]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([clientAgent()]).then(([result]) => {
      if (!cancelled) apply(result!);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  async function refresh() {
    setFailure(null);
    setNotice(null);
    setBusy("load");
    await load();
    setBusy(null);
  }

  async function change(action: "start" | "pause") {
    setFailure(null);
    setNotice(null);
    setBusy(action);
    try {
      setAgent(await (action === "start" ? clientStartAgent() : clientPauseAgent()));
      setNotice(action === "start" ? "agent.done.started" : "agent.done.paused");
    } catch (err) {
      const problem = settingsFailureOf(err);
      setFailure(problem);
      if (problem.signedOut) void logout();
      await load(); // what the server now says, not what was expected
    }
    setBusy(null);
  }

  if (!agent) {
    return (
      <div className="page page--focus">
        <div className="page-head">
          <h2 className="t-h1">{t("agent.title")}</h2>
        </div>
        {loadFailure ? (
          <>
            <p className="admin-alert admin-alert--danger" role="alert">
              {t(loadFailure.key)}
            </p>
            <div>
              <Button onClick={() => void refresh()} loading={busy === "load"}>
                {t("lanes.retry")}
              </Button>
            </div>
          </>
        ) : (
          <p className="c-3" role="status">
            {t("agent.loading")}
          </p>
        )}
      </div>
    );
  }

  const working = busy !== null;
  const running = agent.state === "running";
  const headline: { key: MessageKey; tint: "success" | "attention" | "neutral" } = agent.active
    ? { key: "agent.page.active", tint: "success" }
    : running
      ? { key: "agent.page.waiting", tint: "attention" }
      : agent.state === "paused"
        ? { key: "agent.page.paused", tint: "neutral" }
        : { key: "agent.page.notStarted", tint: "neutral" };
  const shown = failure?.reasons ?? agent.reasons;

  return (
    <div className="page page--focus">
      <div className="page-head">
        <h2 className="t-h1">{t("agent.title")}</h2>
        <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void refresh()} loading={busy === "load"} disabled={working && busy !== "load"}>
          {t("lanes.refresh")}
        </Button>
      </div>
      <p className="c-2">{t("agent.intro")}</p>

      {loadFailure ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(loadFailure.key)}
        </p>
      ) : null}
      {failure ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(failure.key)}
          {failure.detail ? ` (${failure.detail})` : ""}
        </p>
      ) : null}
      {notice ? (
        <p className="admin-alert admin-alert--success" role="status">
          {t(notice)}
        </p>
      ) : null}

      <Glass level={1} className="client-card" aria-label={t("agent.title")}>
        <div className="row row--wrap">
          <span data-testid="agent-state">
            <Chip tint={headline.tint}>{t(headline.key)}</Chip>
          </span>
          {agent.changed_at ? (
            <span className="c-3 t-body-sm">{t("agent.changedAt", { date: d(agent.changed_at, { dateStyle: "medium", timeStyle: "short" }) })}</span>
          ) : null}
        </div>

        {agent.ready ? (
          <p className="c-success" data-testid="agent-ready">
            {t("agent.ready")}
          </p>
        ) : (
          <div data-testid="agent-not-ready">
            <p className="c-2">{t("agent.notReady")}</p>
            <ul className="stack">
              {shown.map((r) => (
                <li key={r.id}>{r.message ?? r.id}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="row row--wrap">
          {running ? (
            <Button leading={<Pause size={16} aria-hidden />} onClick={() => void change("pause")} loading={busy === "pause"} disabled={working && busy !== "pause"}>
              {t("agent.pause")}
            </Button>
          ) : (
            <Button
              variant="primary"
              leading={<Play size={16} aria-hidden />}
              onClick={() => void change("start")}
              loading={busy === "start"}
              disabled={working && busy !== "start"}
              disabledReason={!agent.ready ? t("agent.start.blocked") : undefined}
            >
              {t(agent.state === "paused" ? "agent.resume" : "agent.start")}
            </Button>
          )}
        </div>
      </Glass>
    </div>
  );
}
