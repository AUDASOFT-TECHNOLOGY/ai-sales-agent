/** Approvals: the messages the agent has written and is waiting to send. The client reads each one
 *  and approves it (it goes out at the agent's next dispatch, after the usual safety checks) or
 *  rejects it (it is never sent). Everything shown comes from the server for the logged-in client;
 *  the text is the agent's draft and is only ever rendered as text. After every decision the list is
 *  read again, so what is on screen is what the server holds, and a failure is said, never hidden. */

import { useCallback, useEffect, useState } from "react";
import { Check, RefreshCw, X } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { Button } from "../ui/Button";
import { Glass } from "../ui/Glass";
import { useClientAuth } from "./ClientAuthContext";
import { ClientApiError, clientDecideProposal, clientProposals, type Proposal, type ProposalDecision } from "./api";

type Where = "list" | "decision";

/** What to tell the client, in the interface language (the server's own sentences are English). */
function failureOf(err: unknown, where: Where): MessageKey {
  if (err instanceof ClientApiError) {
    switch (err.status) {
      case 0:
        return "auth.error.server";
      case 401:
        return "approvals.error.session";
      case 404:
        return "approvals.error.gone";
      case 409:
        return where === "list" ? "approvals.error.notReady" : "approvals.error.cannotApprove";
      case 503:
        return "approvals.error.unavailable";
    }
  }
  return "auth.error.server";
}

export function ClientApprovalsPage() {
  const { t, d } = useI18n();
  const { logout } = useClientAuth();
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [busy, setBusy] = useState<{ id: string; decision: ProposalDecision } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [failure, setFailure] = useState<MessageKey | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);

  /** An expired session ends the client's session, which sends them to the sign-in page. */
  const fail = useCallback(
    (err: unknown, where: Where) => {
      if (err instanceof ClientApiError && err.status === 401) void logout();
      setFailure(failureOf(err, where));
    },
    [logout],
  );

  const read = useCallback(
    async (where: Where) => {
      try {
        setProposals((await clientProposals()).proposals);
      } catch (err) {
        fail(err, where);
      }
    },
    [fail],
  );

  useEffect(() => {
    let cancelled = false;
    clientProposals()
      .then((value) => !cancelled && setProposals(value.proposals))
      .catch((err) => !cancelled && fail(err, "list"));
    return () => {
      cancelled = true;
    };
  }, [fail]);

  async function decide(proposal: Proposal, decision: ProposalDecision) {
    setFailure(null);
    setNotice(null);
    setBusy({ id: proposal.id, decision });
    try {
      await clientDecideProposal(proposal.id, decision);
      setNotice(decision === "approve" ? "approvals.done.approved" : "approvals.done.rejected");
    } catch (err) {
      fail(err, "decision");
    }
    await read("decision"); // whatever happened, show what the server holds now
    setBusy(null);
  }

  async function refresh() {
    setFailure(null);
    setNotice(null);
    setRefreshing(true);
    await read("list");
    setRefreshing(false);
  }

  const working = busy !== null || refreshing;

  return (
    <div className="page page--focus">
      <div className="page-head">
        <h2 className="t-h1">{t("approvals.title")}</h2>
        <Button leading={<RefreshCw size={16} aria-hidden />} onClick={() => void refresh()} loading={refreshing} disabled={busy !== null}>
          {t("approvals.refresh")}
        </Button>
      </div>
      <p className="c-2">{t("approvals.intro")}</p>

      {failure ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {t(failure)}
        </p>
      ) : null}
      {notice ? (
        <p className="admin-alert admin-alert--info" role="status">
          {t(notice)}
        </p>
      ) : null}

      {proposals === null ? (
        failure ? null : (
          <p className="c-3" role="status">
            {t("approvals.loading")}
          </p>
        )
      ) : proposals.length === 0 ? (
        <p className="c-3" data-testid="approvals-empty">
          {t("approvals.empty")}
        </p>
      ) : (
        <ul className="stack stack--lg approvals" aria-label={t("approvals.title")}>
          {proposals.map((proposal) => (
            <li key={proposal.id}>
              <Glass level={1} as="article" className="client-card" aria-label={proposal.prospect_name ?? t("approvals.unknownProspect")}>
                <header>
                  <h3 className="t-h2">{proposal.prospect_name ?? t("approvals.unknownProspect")}</h3>
                  <p className="c-3 t-body-sm">
                    {t("approvals.due", { date: d(proposal.due_at, { dateStyle: "medium", timeStyle: "short" }) })}
                    {proposal.language ? ` · ${proposal.language}` : ""}
                  </p>
                </header>
                <blockquote className="approvals__text" dir="auto">
                  {proposal.text}
                </blockquote>
                <div className="row row--wrap">
                  <Button
                    variant="success"
                    leading={<Check size={16} aria-hidden />}
                    loading={busy?.id === proposal.id && busy.decision === "approve"}
                    disabled={working}
                    onClick={() => void decide(proposal, "approve")}
                  >
                    {t("approvals.approve")}
                  </Button>
                  <Button
                    variant="danger"
                    leading={<X size={16} aria-hidden />}
                    loading={busy?.id === proposal.id && busy.decision === "reject"}
                    disabled={working}
                    onClick={() => void decide(proposal, "reject")}
                  >
                    {t("approvals.reject")}
                  </Button>
                </div>
              </Glass>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
