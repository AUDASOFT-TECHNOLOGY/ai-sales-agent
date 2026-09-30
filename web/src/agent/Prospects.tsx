import { useState } from "react";
import { LayoutGroup, motion } from "motion/react";
import { Check, MessageSquareText, Sparkles, X } from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { useI18n, type MessageKey } from "../i18n";
import { PATH_STEPS, STAGES } from "../mock/data";
import { useStore } from "../mock/store";
import type { LostReason, Prospect, Stage } from "../mock/types";
import { Bead } from "../ui/Bead";
import { Button, IconButton, LinkButton } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Disclosure } from "../ui/Disclosure";
import { Glass } from "../ui/Glass";
import { Tip } from "../ui/Overlays";
import { CertaintyPanel } from "./Certainty";
import { MilestoneChip, ProspectStatusChip } from "./StatusChips";

/** One bank of the river: a stage's header and its beads. Shared by the pipeline stages and by the
 *  "Stopped" bank below, which groups by `status` instead of `stage` since a prospect can stop at
 *  any point in the pipeline, not just at the end. */
function Bank({ stage, here, selectedId, onSelect }: { stage: Stage; here: Prospect[]; selectedId?: string; onSelect: (id: string) => void }) {
  const { t } = useI18n();
  const stageLabel = t(stage.label);
  return (
    <section className="bank" data-milestone={stage.milestone} aria-label={t("prospects.bank.count", { stage: stageLabel, n: here.length })}>
      <header className="bank__head">
        <h3 className="t-label c-2">{stageLabel}</h3>
        <span className="bank__count tabular c-3 t-body-sm">{here.length}</span>
      </header>
      <ul className="bank__beads">
        {here.map((p) => (
          <motion.li key={p.id} layout layoutId={`bead-${p.id}`} transition={{ type: "spring", stiffness: 240, damping: 28 }}>
            <Tip label={`${p.name} · ${t(p.read)}`}>
              <button type="button" className="bead-btn" data-selected={selectedId === p.id || undefined} onClick={() => onSelect(p.id)} aria-label={`${p.name}, ${t(p.read)}`} aria-pressed={selectedId === p.id}>
                <Bead initials={p.initials} attention={p.attention} size={40} />
              </button>
            </Tip>
          </motion.li>
        ))}
      </ul>
    </section>
  );
}

/** The Opportunity River (doc 13.4): prospects are beads in the banks of a river of stages, plus a
 *  final "Stopped" bank for prospects lost along the way (declined, went silent, or the agent read
 *  discomfort) — they keep their `stage` of origin for reporting, so they show up here by `status`. */
export function OpportunityRiver({ prospects, selectedId, onSelect }: { prospects: Prospect[]; selectedId?: string; onSelect: (id: string) => void }) {
  const { t } = useI18n();
  const stopped = prospects.filter((p) => p.status === "lost");
  return (
    <LayoutGroup>
      <Glass className="river-panel"><div className="river" role="group" aria-label={t("prospects.river")}>
        <div className="river__current" aria-hidden />
        {STAGES.map((stage) => (
          <Bank key={stage.id} stage={stage} here={prospects.filter((p) => p.stage === stage.id && p.status !== "lost")} selectedId={selectedId} onSelect={onSelect} />
        ))}
        <Bank stage={{ id: "stopped", label: "status.lost", milestone: "lost" }} here={stopped} selectedId={selectedId} onSelect={onSelect} />
      </div></Glass>
    </LayoutGroup>
  );
}

/** The list: who, what state, what happens next, and why the agent acts now. One line of meaning per prospect (doc 16). */
export function ProspectLedger({ prospects, selectedId, onSelect }: { prospects: Prospect[]; selectedId?: string; onSelect: (id: string) => void }) {
  const { t } = useI18n();
  return (
    <Glass className="plist">
      <table>
        <thead className="sr-only">
          <tr>
            <th scope="col">{t("prospects.col.name")}</th>
            <th scope="col">{t("prospects.col.status")}</th>
            <th scope="col">{t("prospects.col.product")}</th>
            <th scope="col">{t("prospects.col.next")}</th>
          </tr>
        </thead>
        <tbody>
          {prospects.map((p) => (
            <tr key={p.id} data-selected={selectedId === p.id || undefined}>
              <th scope="row">
                <button type="button" className="plist__who" onClick={() => onSelect(p.id)}>
                  <Bead initials={p.initials} attention={p.attention} size={48} />
                  <span className="plist__text">
                    <span className="t-h3">{p.name}</span>
                    <span className="voice voice-s plist__why">{t(p.read)}</span>
                  </span>
                </button>
              </th>
              <td className="plist__status">
                <ProspectStatusChip status={p.status} stage={p.stage} lostReason={p.lostReason} />
                {p.attention === "waiting" && p.recommendation ? (
                  <p className="plist__recommend">
                    <Sparkles size={14} aria-hidden /> {t(p.recommendation)}
                  </p>
                ) : null}
              </td>
              <td className="plist__product">
                <span className="c-3 t-body-sm">{t("prospects.col.product")}</span>
                <span>{p.interest}</span>
              </td>
              <td className="plist__next">
                <span className="c-3 t-body-sm">{t("prospects.col.next")}</span>
                <span>{t(p.next)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Glass>
  );
}

/** Where the prospect stands on the sales path: done solid, current lit, next ghosted. */
export function PathTrack({ step }: { step: number }) {
  const { t } = useI18n();
  return (
    <ol className="path-track" aria-label={t("prospects.path")}>
      {PATH_STEPS.map((label, i) => (
        <li key={label} data-state={i < step ? "done" : i === step ? "current" : "next"} aria-current={i === step ? "step" : undefined}>
          <span className="path-track__dot">{i < step ? <Check size={11} strokeWidth={3} aria-hidden /> : null}</span>
          <span className="t-body-sm">{t(label)}</span>
        </li>
      ))}
    </ol>
  );
}

function DossierBody({ prospect, onClose }: { prospect: Prospect; onClose?: () => void }) {
  const { t } = useI18n();
  const { dispatch, toast } = useStore();
  const stage = STAGES.find((s) => s.id === prospect.stage);
  const [pickedReason, setPickedReason] = useState<LostReason>("declined");
  return (
    <div className="dossier__inner">
      <header className="dossier__head">
        <Bead initials={prospect.initials} attention={prospect.attention} size={64} />
        <div className="dossier__title">
          <h2 className="t-h2">{prospect.name}</h2>
          <p className="c-2">
            {prospect.place} · {t(prospect.last)}
          </p>
        </div>
        {onClose ? (
          <IconButton label={t("common.close")} onClick={onClose}>
            <X size={22} />
          </IconButton>
        ) : null}
      </header>
      <div className="row row--wrap">
        <ProspectStatusChip status={prospect.status} stage={prospect.stage} lostReason={prospect.lostReason} />
        <MilestoneChip won={stage?.milestone === "won"} label={stage ? t(stage.label) : ""} />
        <Chip tint="action" outline>
          {prospect.interest}
        </Chip>
      </div>
      <div className="stack">
        <p className="voice voice-m voice-block">{t(prospect.read)}</p>
        <p className="c-2">
          <span className="c-3">{t("prospects.col.next")}: </span>
          {t(prospect.next)}
        </p>
      </div>
      {prospect.recommendation ? (
        <p className="recommend-box">
          <Sparkles size={20} aria-hidden />
          <span>
            <strong>{t("prospects.recommends", { brand: t("brand.name") })}</strong>
            {t(prospect.recommendation)}
          </span>
        </p>
      ) : null}
      <LinkButton variant="primary" size="lg" leading={<MessageSquareText size={18} aria-hidden />} to={`/conversations/${prospect.id}`}>
        {t("prospects.openConversation")}
      </LinkButton>
      <div className="disclosure-group">
        <Disclosure title={t("prospects.knows")} defaultOpen>
          <CertaintyPanel facts={prospect.facts} hideTitle />
        </Disclosure>
        <Disclosure title={t("prospects.path")}>
          <PathTrack step={prospect.step} />
          {prospect.status === "lost" ? (
            <div className="row row--wrap">
              {prospect.lostReason ? <Chip tint="danger" outline>{t(`status.lost.${prospect.lostReason}` as MessageKey)}</Chip> : null}
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  dispatch({ type: "reopenProspect", id: prospect.id });
                  toast({ tone: "info", title: t("prospects.toast.reopened", { name: prospect.name.split(" ")[0] }), body: t("prospects.toast.manualChange") });
                }}
              >
                {t("prospects.reopen")}
              </Button>
            </div>
          ) : (
            <div className="stack">
              <label className="row row--wrap">
                <span className="t-label c-2">{t("prospects.moveStage")}</span>
                <select
                  className="input well"
                  value={prospect.stage}
                  onChange={(e) => {
                    dispatch({ type: "setProspectStage", id: prospect.id, stage: e.target.value });
                    toast({ tone: "info", title: t("prospects.toast.moved", { name: prospect.name.split(" ")[0] }), body: t("prospects.toast.manualChange") });
                  }}
                >
                  {STAGES.map((s) => (
                    <option key={s.id} value={s.id}>
                      {t(s.label)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="row row--wrap">
                <select className="input well" value={pickedReason} onChange={(e) => setPickedReason(e.target.value as LostReason)}>
                  <option value="declined">{t("status.lost.declined")}</option>
                  <option value="no_response">{t("status.lost.no_response")}</option>
                  <option value="inferred">{t("status.lost.inferred")}</option>
                </select>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    dispatch({ type: "markProspectLost", id: prospect.id, reason: pickedReason });
                    toast({ tone: "attention", title: t("prospects.toast.markedLost", { name: prospect.name.split(" ")[0] }), body: t("prospects.toast.manualChange") });
                  }}
                >
                  {t("prospects.markLost")}
                </Button>
              </div>
            </div>
          )}
        </Disclosure>
      </div>
    </div>
  );
}

/** Inspector on wide screens (page stays interactive), modal drawer below 1280 px (doc 21.2). */
export function Dossier({ prospect, inspector, onClose }: { prospect: Prospect | undefined; inspector: boolean; onClose: () => void }) {
  if (!prospect) return null;
  if (inspector) {
    return (
      <motion.aside initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }} className="dossier glass" data-level="2" aria-label={prospect.name}>
        <DossierBody prospect={prospect} onClose={onClose} />
      </motion.aside>
    );
  }
  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" />
        <Dialog.Content className="drawer glass" data-level="4" aria-describedby={undefined}>
          <Dialog.Title className="sr-only">{prospect.name}</Dialog.Title>
          <DossierBody prospect={prospect} onClose={onClose} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
