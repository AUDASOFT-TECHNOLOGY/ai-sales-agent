/** Where the client wants Revansa's results to appear in its own source (write-back).
 *
 *  For a spreadsheet: the title of an existing column, per result. For HubSpot: the name of an
 *  existing contact property, per result, and what the lead statuses mean. Nothing is copied until
 *  the client fills this in, and nothing is created in the client's file or account. */

import { useEffect, useState } from "react";
import { useI18n, type MessageKey } from "../../i18n";
import { Button } from "../../ui/Button";
import { Chip } from "../../ui/Chip";
import { TextInput } from "../../ui/Controls";
import { clientLaneSync, clientSaveLaneSync, type LaneSync, type SyncResultKey } from "../api";
import { useClientAuth } from "../ClientAuthContext";
import { Card, FailureNote, Loading, Note, failureOf, type Failure } from "../kit";

const SHEETS = "google_sheets";
const HUBSPOT = "hubspot";

type Names = Partial<Record<SyncResultKey, string>>;

function clean(names: Names): Names {
  return Object.fromEntries(Object.entries(names).filter(([, v]) => (v ?? "").trim() !== "").map(([k, v]) => [k, (v ?? "").trim()]));
}

/** The next action is two cells/properties and is named whole or not at all. */
function nextActionBroken(names: Names): boolean {
  return Boolean(names.next_action_type) !== Boolean(names.next_action_due_at);
}

export function SyncCard({ laneId, laneName, onClose, onChanged }: { laneId: string; laneName: string; onClose: () => void; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const { logout } = useClientAuth();
  const [sync, setSync] = useState<LaneSync | null>(null);
  const [names, setNames] = useState<Names>({});
  const [copyStatus, setCopyStatus] = useState(false);
  const [optOut, setOptOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const [done, setDone] = useState(false);

  function adopt(next: LaneSync) {
    setSync(next);
    if (next.source === SHEETS) {
      setNames(next.google_sheets?.columns ?? {});
      setOptOut(Boolean(next.google_sheets?.propagate_opt_out));
    } else if (next.source === HUBSPOT) {
      setNames(next.hubspot?.properties ?? {});
      setCopyStatus(Boolean(next.hubspot?.status));
      setOptOut(Boolean(next.hubspot?.propagate_opt_out));
    }
  }

  useEffect(() => {
    let cancelled = false;
    clientLaneSync(laneId)
      .then((next) => !cancelled && adopt(next))
      .catch((err) => {
        if (cancelled) return;
        const f = failureOf(err);
        setFailure(f);
        if (f.signedOut) void logout();
      });
    return () => {
      cancelled = true;
    };
  }, [laneId, logout]);

  if (!sync) return failure ? <FailureNote failure={failure} /> : <Loading />;

  const sheets = sync.source === SHEETS;
  // A spreadsheet has a column for the status; HubSpot has its status property, asked above.
  const columns = sync.results.filter((r) => sheets || r.key !== "status");

  async function run(body: Parameters<typeof clientSaveLaneSync>[1]) {
    setBusy(true);
    setFailure(null);
    setDone(false);
    try {
      adopt(await clientSaveLaneSync(laneId, body));
      setDone(true);
      await onChanged();
    } catch (err) {
      const f = failureOf(err);
      setFailure(f);
      if (f.signedOut) void logout();
    }
    setBusy(false);
  }

  async function save() {
    const chosen = clean(names);
    if (nextActionBroken(chosen)) return setProblem("rv.sync.err.nextAction");
    if (sheets) {
      if (Object.keys(chosen).length === 0) return setProblem("rv.sync.err.none");
      setProblem(null);
      return run({ expected_version: sync!.version, google_sheets: { columns: chosen, propagate_opt_out: optOut } });
    }
    if (!copyStatus && Object.keys(chosen).length === 0) return setProblem("rv.sync.err.none");
    setProblem(null);
    const { status: _status, ...properties } = chosen;
    void _status;
    return run({
      expected_version: sync!.version,
      hubspot: {
        status: copyStatus,
        properties,
        propagate_opt_out: copyStatus && optOut,
      },
    });
  }

  const stateChip = sync.state === "configured" ? "success" : undefined;

  return (
    <Card aria-label={t("rv.sync.title")}>
      <div className="rv-card__head">
        <h3 dir="auto">{t("rv.sync.titleFor", { name: laneName })}</h3>
        <Chip tint={stateChip}>{t(`rv.sync.state.${sync.state}` as MessageKey)}</Chip>
      </div>
      {!sync.supported ? (
        <Note>{t("rv.sync.unsupported")}</Note>
      ) : (
        <>
          <p className="rv-card__sub">{t(sheets ? "rv.sync.help.sheets" : "rv.sync.help.hubspot")}</p>
          {sync.state !== "off" ? <Note tone={sync.state === "configured" ? "success" : "warn"}>{t(`rv.sync.note.${sync.state}` as MessageKey)}</Note> : null}
          {failure ? <FailureNote failure={failure} /> : null}
          {problem ? <Note tone="danger">{t(problem)}</Note> : null}
          {done ? <Note tone="success">{t("rv.sync.saved")}</Note> : null}

          {!sheets ? (
            <>
              <label className="rv-checkbox">
                <input type="checkbox" checked={copyStatus} onChange={(e) => setCopyStatus(e.target.checked)} />
                <span>{t("rv.sync.copyStatus")}</span>
              </label>
            </>
          ) : null}

          <div className="stack">
            {columns.map((r) => (
                <TextInput
                  key={r.key}
                  label={t(`rv.sync.result.${r.key}` as MessageKey)}
                  helper={t(sheets ? "rv.sync.columnHelp" : "rv.sync.propertyHelp")}
                  value={names[r.key] ?? ""}
                  onChange={(e) => {
                    setNames({ ...names, [r.key]: e.target.value });
                    setDone(false);
                  }}
                  dir="ltr"
                  maxLength={120}
                />
              ))}
          </div>
          {sheets || copyStatus ? (
            <label className="rv-checkbox">
              <input type="checkbox" checked={optOut} onChange={(e) => setOptOut(e.target.checked)} disabled={sheets && !names.status?.trim()} />
              <span>{t("rv.sync.optOut")}</span>
            </label>
          ) : null}
        </>
      )}
      <div className="rv-actions">
        {sync.supported ? (
          <Button variant="primary" onClick={() => void save()} loading={busy}>
            {t("rv.common.save")}
          </Button>
        ) : null}
        {sync.supported && sync.state !== "off" ? (
          <Button onClick={() => void run({ expected_version: sync.version, enabled: false })} disabled={busy}>
            {t("rv.sync.stop")}
          </Button>
        ) : null}
        <Button variant="ghost" onClick={onClose} disabled={busy}>
          {t("rv.common.cancel")}
        </Button>
      </div>
    </Card>
  );
}
