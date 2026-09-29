import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Rows3, Search, Waves } from "lucide-react";
import { useI18n } from "../i18n";
import { useStore } from "../mock/store";
import { Dossier, OpportunityRiver, ProspectLedger } from "../agent/Prospects";
import { Segmented } from "../ui/Controls";

type View = "list" | "river";
type Filter = "all" | "needs";

export function Prospects() {
  const { t, n } = useI18n();
  const { state } = useStore();
  const [params, setParams] = useSearchParams();
  const view: View = params.get("view") === "river" ? "river" : "list";
  const filter: Filter = params.get("filter") === "needs" ? "needs" : "all";
  const product = params.get("product") ?? "all";
  const q = params.get("q") ?? "";
  const openId = params.get("open") ?? undefined;

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "" || (k === "view" && v === "list") || (k === "filter" && v === "all") || (k === "product" && v === "all")) next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };

  const products = useMemo(() => Array.from(new Set(state.prospects.map((p) => p.interest))).sort(), [state.prospects]);

  const prospects = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return state.prospects.filter((p) => {
      if (filter === "needs" && p.attention !== "waiting") return false;
      if (product !== "all" && p.interest !== product) return false;
      if (!needle) return true;
      return `${p.name} ${p.place} ${p.interest}`.toLowerCase().includes(needle);
    });
  }, [state.prospects, filter, product, q]);

  const selected = state.prospects.find((p) => p.id === openId);
  const select = (id: string) => update({ open: id });

  // What "N prospects" means changes with the filters — it is never tied to a date range (unlike
  // the Dashboard's period-filtered KPIs), so say so plainly when no filter narrows it.
  const total = state.prospects.length;
  const filterBits: string[] = [];
  if (filter === "needs") filterBits.push("need you");
  if (product !== "all") filterBits.push(`are interested in ${product}`);
  if (q.trim()) filterBits.push(`match "${q.trim()}"`);
  const countLabel = filterBits.length > 0 ? `${n(prospects.length)} of ${n(total)} ${filterBits.join(" and ")}` : t("prospects.count", { n: n(total) });

  return (
    <div className="page page--focus prospects">
      <div className="page-head">
        <div>
          <h2 className="t-h1">{t("prospects.title")}</h2>
          <p className="c-2 tabular">{countLabel}</p>
        </div>
        <div className="row row--wrap prospects__tools">
          <label className="search-field well">
            <Search size={18} aria-hidden className="c-3" />
            <input value={q} onChange={(e) => update({ q: e.target.value })} placeholder={t("prospects.search")} aria-label={t("prospects.search")} />
          </label>
          <Segmented
            layoutId="seg-filter"
            label="Filter"
            size="sm"
            value={filter}
            onChange={(v) => update({ filter: v })}
            options={[
              { value: "all", label: t("prospects.filter.all") },
              { value: "needs", label: t("prospects.filter.needs") },
            ]}
          />
          <label className="sr-only" htmlFor="prospects-product">
            Catalogue product
          </label>
          <select id="prospects-product" className="input well prospects__product-select" value={product} onChange={(e) => update({ product: e.target.value })}>
            <option value="all">All products</option>
            {products.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <Segmented
            layoutId="seg-view"
            label="View"
            size="sm"
            value={view}
            onChange={(v) => update({ view: v })}
            options={[
              { value: "list", label: t("prospects.ledger"), icon: <Rows3 size={16} aria-hidden /> },
              { value: "river", label: t("prospects.river"), icon: <Waves size={16} aria-hidden /> },
            ]}
          />
        </div>
      </div>
      <div className="prospects__body">
        <div className="prospects__main">
          {view === "list" ? (
            <ProspectLedger prospects={prospects} selectedId={openId} onSelect={select} />
          ) : (
            <OpportunityRiver prospects={prospects} selectedId={openId} onSelect={select} />
          )}
          {prospects.length === 0 ? (
            <div className="empty">
              <h3 className="t-h3">No prospects match these filters.</h3>
              <button type="button" className="link-btn" onClick={() => update({ q: null, filter: null, product: null })}>
                Clear filters
              </button>
            </div>
          ) : null}
        </div>
        <Dossier prospect={selected} inspector={false} onClose={() => update({ open: null })} />
      </div>
    </div>
  );
}
