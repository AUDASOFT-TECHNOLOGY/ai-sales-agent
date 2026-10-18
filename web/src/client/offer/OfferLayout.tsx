/** "My offer": the business, the catalogue and the payment methods, three views over one document
 *  (the client's commercial settings, `GET/PUT /client/v1/settings`). The layout loads it once, hands
 *  it to the pages and is the only place that writes it, so two pages never save over each other:
 *  every write names the version it was based on and what is on screen afterwards is the server's
 *  answer. A write sends only what changed, so what a page does not edit is kept by the server. */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Outlet } from "react-router-dom";
import { useI18n } from "../../i18n";
import { clientSaveSettings, clientSettings, type ClientSettings, type SettingsState } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { FailureNote, Loading, PageHead, SubTabs, failureOf, type Failure } from "../kit";
import type { Doc } from "../settingsModel";

interface OfferContext {
  state: SettingsState;
  /** The commercial document as the server holds it. */
  commercial: Doc;
  /** Writes `changes` (only the parts given); resolves with the failure to show, or null when saved. */
  save: (changes: ClientSettings) => Promise<Failure | null>;
  reload: () => Promise<void>;
}

const Ctx = createContext<OfferContext | null>(null);

export function useOffer(): OfferContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useOffer must be used inside OfferLayout");
  return ctx;
}

export function OfferLayout() {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [state, setState] = useState<SettingsState | null>(null);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);

  const reload = useCallback(async () => {
    try {
      setState(await clientSettings());
      setLoadFailure(null);
    } catch (err) {
      const problem = failureOf(err);
      setLoadFailure(problem);
      if (problem.signedOut) void logout();
    }
  }, [logout]);

  useEffect(() => {
    let cancelled = false;
    clientSettings().then(
      (value) => !cancelled && setState(value),
      (err: unknown) => {
        if (cancelled) return;
        const problem = failureOf(err);
        setLoadFailure(problem);
        if (problem.signedOut) void logout();
      },
    );
    return () => {
      cancelled = true;
    };
  }, [logout]);

  const version = state?.version ?? null;
  const save = useCallback(
    async (changes: ClientSettings): Promise<Failure | null> => {
      try {
        setState(await clientSaveSettings(version, changes));
        return null;
      } catch (err) {
        const problem = failureOf(err);
        if (problem.signedOut) void logout();
        if (problem.reload) await reload(); // the screen then shows what is now in force
        return problem;
      }
    },
    [version, logout, reload],
  );

  const value = useMemo<OfferContext | null>(
    () => (state ? { state, commercial: (state.settings.commercial as Doc | undefined) ?? {}, save, reload } : null),
    [state, save, reload],
  );

  return (
    <div className="rv-page">
      <PageHead title={t("rv.offer.title")} text={t("rv.offer.intro")} />
      <SubTabs
        label={t("rv.nav.offer")}
        items={[
          { to: "/offer/products", label: t("rv.offer.tab.products") },
          { to: "/offer/payments", label: t("rv.offer.tab.payments") },
          { to: "/offer/business", label: t("rv.offer.tab.business") },
        ]}
      />
      {loadFailure ? <FailureNote failure={loadFailure} onRetry={() => void reload()} /> : null}
      {value ? (
        <Ctx.Provider value={value}>
          <Outlet />
        </Ctx.Provider>
      ) : loadFailure ? null : (
        <Loading />
      )}
    </div>
  );
}
