/** "What your agent has learned": one number, two columns (what works, what it avoids), a small bar for
 *  how many conversations stand behind each point, and an icon to set it aside. Almost no text. */

import { useEffect, useState } from "react";
import { Check, Lock, RotateCcw, Sparkles, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useI18n } from "../../i18n";
import { clientLearning, clientLearningChoice } from "../api";
import { Card, Note } from "../kit";
import { emptyReason, groupNotes, sentenceKey, shown, type Choice, type LearnedNote, type LearningAnswer } from "./learningModel";
import "./learning.css";

export function LearnedCard() {
  const { t } = useI18n();
  const [answer, setAnswer] = useState<LearningAnswer | null>(null);
  const [lost, setLost] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const [round, setRound] = useState(0);
  useEffect(() => {
    let stale = false;
    clientLearning().then(
      (next) => {
        if (stale) return;
        setAnswer(next);
        setLost(false);
      },
      () => {
        if (!stale) setLost(true);
      },
    );
    return () => {
      stale = true;
    };
  }, [round]);

  async function choose(note: LearnedNote, choice: Choice) {
    setBusy(note.id);
    try {
      await clientLearningChoice(note.id, choice);
      setRound((n) => n + 1);
    } catch {
      setLost(true);
    }
    setBusy(null);
  }

  if (!answer) return lost ? <Note tone="warn">{t("rv.learn.failed")}</Note> : null;
  const notes = shown(answer.notes);
  const groups = groupNotes(notes);
  const empty = emptyReason({ ...answer, notes });
  const { studied, needed } = answer.counts;
  const strongest = Math.max(1, ...notes.map((n) => n.cases));

  const icon = (note: LearnedNote, choice: Choice, label: string, glyph: React.ReactNode, kind?: "ok") => (
    <button key={choice} type="button" className="ln-icon" data-kind={kind} aria-label={label} title={label} disabled={busy === note.id} onClick={() => choose(note, choice)}>
      {glyph}
    </button>
  );

  const column = (tone: "works" | "avoid", list: LearnedNote[]) =>
    list.length === 0 ? null : (
      <section className="ln-col" data-tone={tone}>
        <h4 className="ln-col__title">
          {tone === "works" ? <ThumbsUp size={16} aria-hidden /> : <ThumbsDown size={16} aria-hidden />}
          {t(tone === "works" ? "rv.learn.works" : "rv.learn.avoids")}
        </h4>
        <ul>
          {list.map((n) => (
            <li key={n.id} className="ln-item" data-testid={`learned-${n.id}`}>
              <span className="ln-item__text">{t(sentenceKey(n)!)}</span>
              <span className="ln-item__acts">{icon(n, "off", t("rv.learn.stop"), <X size={16} aria-hidden />)}</span>
              <span className="ln-item__bar" title={t("rv.learn.cases", { n: n.cases })}>
                <i style={{ inlineSize: `${Math.round((n.cases / strongest) * 70)}%` }} />
                <b>{n.cases}</b>
              </span>
            </li>
          ))}
        </ul>
      </section>
    );

  return (
    <Card data-testid="training-learned">
      <div className="ln">
        <div className="ln-head">
          <span className="ln-head__icon" aria-hidden>
            <Sparkles size={22} />
          </span>
          <h3>{t("rv.learn.title")}</h3>
          <div className="ln-stat">
            <strong>{studied}</strong>
            <span>{t("rv.learn.studied")}</span>
          </div>
        </div>

        {empty === "few" ? (
          <div className="ln-wait">
            <div className="ln-wait__bar" role="progressbar" aria-valuemin={0} aria-valuemax={needed} aria-valuenow={studied}>
              <i style={{ inlineSize: `${Math.min(100, Math.round((studied / needed) * 100))}%` }} />
            </div>
            <p>{t("rv.learn.empty", { left: Math.max(0, needed - studied) })}</p>
          </div>
        ) : null}
        {empty === "none" ? <p className="c-2">{t("rv.learn.nothing")}</p> : null}

        {groups.works.length + groups.avoids.length > 0 ? (
          <div className="ln-cols">
            {column("works", groups.works)}
            {column("avoid", groups.avoids)}
          </div>
        ) : null}

        {groups.watching.length > 0 ? (
          <div className="ln-chips">
            <span className="ln-chips__label">{t("rv.learn.watching")}</span>
            {groups.watching.map((n) => (
              <span key={n.id} className="ln-chip" data-testid={`learned-${n.id}`}>
                {t(sentenceKey(n)!)}
                {icon(n, "confirm", t("rv.learn.confirm"), <Check size={14} aria-hidden />, "ok")}
                {icon(n, "off", t("rv.learn.stop"), <X size={14} aria-hidden />)}
              </span>
            ))}
          </div>
        ) : null}

        {groups.off.length > 0 ? (
          <details className="ln-off">
            <summary>{t("rv.learn.off")} ({groups.off.length})</summary>
            <ul>
              {groups.off.map((n) => (
                <li key={n.id} data-testid={`learned-${n.id}`}>
                  <span>{t(sentenceKey(n)!)}</span>
                  {icon(n, "on", t("rv.learn.back"), <RotateCcw size={14} aria-hidden />)}
                </li>
              ))}
            </ul>
          </details>
        ) : null}

        {lost ? <Note tone="warn">{t("rv.learn.failed")}</Note> : null}
        <p className="ln-foot">
          <Lock size={12} aria-hidden /> {t("rv.learn.privacy")}
        </p>
      </div>
    </Card>
  );
}
