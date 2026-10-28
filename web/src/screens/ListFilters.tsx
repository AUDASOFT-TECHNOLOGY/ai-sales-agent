/** The search and filters shared by the prospects and conversations lists. The state lives in the URL
 *  (so a filtered list can be reloaded and shared); the server does the narrowing, nothing is
 *  filtered in the browser. */

import { useEffect, useState, type FormEvent } from "react";
import { useI18n, type MessageKey } from "../i18n";
import { clientLanes, type ProspectFilters, type ProspectRow } from "../client/api";
import { Search } from "lucide-react";

export const STATUSES: ProspectRow["status"][] = ["new", "active", "paused", "opted_out", "closed"];
export const CHANNELS = ["whatsapp", "sms", "email", "telegram"];

export interface FilterState {
  q: string;
  status: string;
  channel: string;
  lane: string;
  human: boolean;
  errors: boolean;
  since: string; // yyyy-mm-dd
  until: string; // yyyy-mm-dd, included
}

export const NO_FILTERS: FilterState = { q: "", status: "", channel: "", lane: "", human: false, errors: false, since: "", until: "" };

export function filtersFromParams(p: URLSearchParams): FilterState {
  return {
    q: p.get("q") ?? "",
    status: STATUSES.includes(p.get("status") as ProspectRow["status"]) ? (p.get("status") as string) : "",
    channel: p.get("channel") ?? "",
    lane: p.get("lane") ?? "",
    human: p.get("human") === "1",
    errors: p.get("errors") === "1",
    since: p.get("since") ?? "",
    until: p.get("until") ?? "",
  };
}

/** The URL parameters of a filter state (only what is set) and, when above 1, the page. */
export function paramsFromFilters(f: FilterState, page = 1): Record<string, string> {
  const out: Record<string, string> = {};
  if (f.q.trim()) out.q = f.q.trim();
  if (f.status) out.status = f.status;
  if (f.channel) out.channel = f.channel;
  if (f.lane) out.lane = f.lane;
  if (f.human) out.human = "1";
  if (f.errors) out.errors = "1";
  if (f.since) out.since = f.since;
  if (f.until) out.until = f.until;
  if (page > 1) out.page = String(page);
  return out;
}

export function isFiltered(f: FilterState): boolean {
  return Object.keys(paramsFromFilters(f)).length > 0;
}

function dayAfter(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString();
}

/** What the server is asked: days are UTC, the last one included. */
export function toApiFilters(f: FilterState, sort?: ProspectFilters["sort"]): ProspectFilters {
  const api: ProspectFilters = {};
  if (f.q.trim()) api.q = f.q.trim();
  if (f.status) api.status = [f.status as ProspectRow["status"]];
  if (f.channel) api.channel = f.channel;
  if (f.lane) api.lane = f.lane;
  if (f.human) api.needs_human = true;
  if (f.errors) api.has_error = true;
  if (f.since) api.active_since = `${f.since}T00:00:00Z`;
  if (f.until) api.active_until = dayAfter(f.until);
  if (sort) api.sort = sort;
  return api;
}

/** The client's circuits, for the filter. A failure only hides the circuit filter. */
function useLanes(): { id: string; label: string }[] {
  const [lanes, setLanes] = useState<{ id: string; label: string }[]>([]);
  useEffect(() => {
    let cancelled = false;
    clientLanes().then(
      (state) => {
        if (!cancelled) setLanes(state.lanes.map((l) => ({ id: l.id, label: l.source.label })));
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return lanes;
}

/** One line of controls: a search box, three drop-downs, two switches and the dates behind "Dates".
 *  Every control keeps a text label for assistive technology. */
export function ListFilters({ value, onChange }: { value: FilterState; onChange: (next: FilterState) => void }) {
  const { t } = useI18n();
  const lanes = useLanes();
  const [text, setText] = useState(value.q);
  const [seen, setSeen] = useState(value.q);
  if (seen !== value.q) {
    // The address changed (reset, back button): the box follows it.
    setSeen(value.q);
    setText(value.q);
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onChange({ ...value, q: text });
  };
  const set = (patch: Partial<FilterState>) => onChange({ ...value, ...patch });
  const hasDates = value.since !== "" || value.until !== "";

  return (
    <form className="rv-filters" role="search" aria-label={t("prospects.f.label")} onSubmit={submit}>
      <label className="rv-search">
        <Search size={16} aria-hidden />
        <span className="sr-only">{t("prospects.f.search")}</span>
        <input type="search" value={text} placeholder={t("prospects.f.searchPh")} onChange={(e) => setText(e.target.value)} onBlur={() => text !== value.q && onChange({ ...value, q: text })} />
      </label>
      <button type="submit" className="sr-only">
        {t("prospects.f.searchGo")}
      </button>
      <label className="rv-pillselect">
        <span className="sr-only">{t("prospects.f.status")}</span>
        <select value={value.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">{t("prospects.f.statusAll")}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {t(`crm.status.${s}` as MessageKey)}
            </option>
          ))}
        </select>
      </label>
      <label className="rv-pillselect">
        <span className="sr-only">{t("prospects.f.channel")}</span>
        <select value={value.channel} onChange={(e) => set({ channel: e.target.value })}>
          <option value="">{t("prospects.f.channelAll")}</option>
          {CHANNELS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      {lanes.length > 1 ? (
        <label className="rv-pillselect">
          <span className="sr-only">{t("prospects.f.lane")}</span>
          <select value={value.lane} onChange={(e) => set({ lane: e.target.value })}>
            <option value="">{t("prospects.f.laneAll")}</option>
            {lanes.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label className="rv-switchchip" data-on={value.human || undefined}>
        <input type="checkbox" checked={value.human} onChange={(e) => set({ human: e.target.checked })} />
        <span>{t("prospects.f.human")}</span>
      </label>
      <label className="rv-switchchip" data-on={value.errors || undefined}>
        <input type="checkbox" checked={value.errors} onChange={(e) => set({ errors: e.target.checked })} />
        <span>{t("prospects.f.errors")}</span>
      </label>
      <details className="rv-dates" open={hasDates || undefined}>
        <summary>{t("rv.conv.dates")}</summary>
        <div className="rv-dates__body">
          <label className="rv-select-label">
            <span className="t-label c-2">{t("prospects.f.since")}</span>
            <input className="rv-select" type="date" value={value.since} onChange={(e) => set({ since: e.target.value })} />
          </label>
          <label className="rv-select-label">
            <span className="t-label c-2">{t("prospects.f.until")}</span>
            <input className="rv-select" type="date" value={value.until} onChange={(e) => set({ until: e.target.value })} />
          </label>
        </div>
      </details>
      {isFiltered(value) ? (
        <button type="button" className="link-btn" onClick={() => onChange(NO_FILTERS)}>
          {t("prospects.f.reset")}
        </button>
      ) : null}
    </form>
  );
}
