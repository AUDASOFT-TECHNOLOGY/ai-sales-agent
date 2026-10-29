/** The activity feed: one line per person (or per task that is about nobody), newest first. */

import { Check, CircleAlert, LoaderCircle } from "lucide-react";
import { useI18n, type MessageKey } from "../../i18n";
import { clock, groupFeed, initials, secondsOf, stepKey, type FeedGroup, type LiveStep } from "./liveModel";

const SHORT: Record<string, MessageKey> = {
  checking: "rv.live.s.checking",
  reading_history: "rv.live.s.reading_history",
  thinking: "rv.live.s.thinking",
  answering: "rv.live.s.answering",
  sending: "rv.live.s.sending",
  receiving: "rv.live.s.receiving",
};

function Row({ group, followed, serverNow, onPick }: { group: FeedGroup; followed: string | null; serverNow: number; onPick: (id: string) => void }) {
  const { t, d } = useI18n();
  const first = group.steps[0]!;
  const seconds = group.steps.reduce((sum, s) => sum + secondsOf(s, serverNow), 0);
  const Icon = group.state === "running" ? LoaderCircle : group.state === "failed" ? CircleAlert : Check;
  const title = group.prospectId ? (group.name ?? t("rv.live.someone")) : t(stepKey(first.kind), { name: "", detail: first.detail ?? "" });
  const summary = group.prospectId
    ? group.steps.map((s: LiveStep) => t(SHORT[s.kind] ?? stepKey(s.kind), { name: s.name ?? "", detail: s.detail ?? "" })).join(" · ")
    : "";
  const body = (
    <>
      <span className="lv-feed__avatar" aria-hidden>
        {group.prospectId ? initials(group.name) : <Icon size={14} />}
      </span>
      <span className="lv-feed__text">
        <span className="lv-feed__title">{title}</span>
        {summary ? <span className="lv-feed__sum c-3">{summary}</span> : null}
        {group.state === "failed" ? <span className="lv-feed__sum lv-feed__warn">{t("rv.live.failed")}</span> : null}
      </span>
      <span className="lv-feed__meta tabular c-3">
        <time dateTime={group.startedAt}>{d(group.startedAt, { timeStyle: "medium" })}</time>
        <span>{clock(seconds)}</span>
      </span>
      {group.prospectId ? (
        <span className="lv-feed__state" data-state={group.state} aria-hidden>
          <Icon size={14} />
        </span>
      ) : null}
    </>
  );
  return (
    <li data-state={group.state} data-followed={group.prospectId !== null && group.prospectId === followed ? "" : undefined}>
      {group.prospectId ? (
        <button type="button" className="lv-feed__row" onClick={() => onPick(group.prospectId!)} aria-label={t("rv.live.follow", { name: group.name ?? "" })}>
          {body}
        </button>
      ) : (
        <div className="lv-feed__row">{body}</div>
      )}
    </li>
  );
}

export function Feed({ recent, followed, serverNow, onPick }: { recent: LiveStep[]; followed: string | null; serverNow: number; onPick: (id: string) => void }) {
  const { t } = useI18n();
  const groups = groupFeed(recent);
  if (groups.length === 0) return <p className="c-3">{t("rv.live.feed.empty")}</p>;
  return (
    <ol className="lv-feed" aria-label={t("rv.live.feed")} data-testid="live-journal">
      {groups.map((g) => (
        <Row key={g.key} group={g} followed={followed} serverNow={serverNow} onPick={onPick} />
      ))}
    </ol>
  );
}
