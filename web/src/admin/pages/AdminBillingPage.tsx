/** Billing: two tabs, the commercial plans and the monthly invoices (`AdminInvoicesPanel`). Plans: (GET/POST/PUT /admin/v1/plans -- real data, no fixtures). An owner
 *  creates and changes plans and switches them off; every other role only reads. A plan is never
 *  deleted: clients and past assignments refer to it. Invoices are not part of this screen yet. */

import { useEffect, useId, useState, type FormEvent } from "react";
import { Glass } from "../../ui/Glass";
import { Chip } from "../../ui/Chip";
import { Button } from "../../ui/Button";
import { Segmented, Switch, TextInput } from "../../ui/Controls";
import { AdminInvoicesPanel } from "./AdminInvoicesPanel";
import { Modal } from "../../ui/Overlays";
import { useAdminAuth } from "../AdminAuthContext";
import { AdminApiError, adminCreatePlan, adminListPlans, adminUpdatePlan, type Plan, type PlanInput } from "../api";

// Mirrors require_owner on POST/PUT /plans in src/sales_app/api/admin_plans.py.
const CAN_EDIT_PLANS_ROLES = new Set(["owner"]);

type LoadState = "loading" | "ready" | "error";

type Tab = "plans" | "invoices";

export function AdminBillingPage() {
  const [tab, setTab] = useState<Tab>("plans");
  return (
    <div className="admin-clients">
      <Segmented<Tab>
        label="Billing section"
        layoutId="admin-billing-tab"
        value={tab}
        onChange={setTab}
        options={[
          { value: "plans", label: "Plans" },
          { value: "invoices", label: "Invoices" },
        ]}
      />
      {tab === "plans" ? <PlansPanel /> : <AdminInvoicesPanel />}
    </div>
  );
}

function PlansPanel() {
  const { admin } = useAdminAuth();
  const canEdit = admin !== null && CAN_EDIT_PLANS_ROLES.has(admin.role);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Plan | "new" | null>(null);

  useEffect(() => {
    let cancelled = false;
    adminListPlans()
      .then((data) => {
        if (cancelled) return;
        setPlans(data);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof AdminApiError ? err.message : "Could not load the plans.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function onSaved(saved: Plan) {
    setPlans((current) =>
      [...current.filter((p) => p.plan_id !== saved.plan_id), saved].sort((a, b) => a.name.localeCompare(b.name)),
    );
    setEditing(null);
  }

  return (
    <div className="admin-clients">
      <div className="admin-clients__header">
        <div className="admin-clients__title">
          <h2 className="t-h2">Plans</h2>
          {state === "ready" ? <span className="admin-count tabular">{plans.length}</span> : null}
        </div>
        {canEdit ? (
          <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
            + New plan
          </Button>
        ) : null}
      </div>

      {state === "loading" ? (
        <p className="admin-alert admin-alert--info" role="status" aria-live="polite">
          Loading plans…
        </p>
      ) : null}
      {state === "error" ? (
        <p className="admin-alert admin-alert--danger" role="alert">
          {error}
        </p>
      ) : null}

      {state === "ready" && plans.length === 0 ? (
        <Glass level={1} className="admin-placeholder admin-placeholder--center">
          <p className="c-2">No plans yet.{canEdit ? " Create the first one to put clients on it." : ""}</p>
        </Glass>
      ) : null}

      {state === "ready" && plans.length > 0 ? (
        <Glass level={1} className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Plan</th>
                <th scope="col" className="admin-table__num">
                  Monthly price
                </th>
                <th scope="col" className="admin-table__num">
                  Included credits
                </th>
                <th scope="col" className="admin-table__num">
                  Daily quota
                </th>
                <th scope="col" className="admin-table__num">
                  Clients
                </th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {plans.map((p) => (
                <tr key={p.plan_id}>
                  <th scope="row" className="admin-table__name">
                    {p.name}
                  </th>
                  <td className="admin-table__num tabular">
                    {p.monthly_price} {p.currency}
                  </td>
                  <td className="admin-table__num tabular">{p.included_credits}</td>
                  <td className="admin-table__num tabular">{p.daily_quota}</td>
                  <td className="admin-table__num tabular">{p.client_count}</td>
                  <td>
                    <Chip tint={p.active ? "success" : "neutral"}>{p.active ? "Active" : "Inactive"}</Chip>
                  </td>
                  <td className="admin-table__action">
                    {canEdit ? (
                      <Button variant="ghost" size="sm" onClick={() => setEditing(p)} aria-label={`Edit ${p.name}`}>
                        Edit
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Glass>
      ) : null}

      {editing ? <PlanModal plan={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={onSaved} /> : null}
    </div>
  );
}

/** Accepts "100,000" / "100 000" style input; returns null when not a whole number. */
function toInteger(raw: string): number | null {
  const cleaned = raw.replace(/[\s,_ ]/g, "");
  if (!/^\d+$/.test(cleaned)) return null;
  return Number(cleaned);
}

function PlanModal({ plan, onClose, onSaved }: { plan: Plan | null; onClose: () => void; onSaved: (plan: Plan) => void }) {
  const formId = useId();
  const [name, setName] = useState(plan?.name ?? "");
  const [price, setPrice] = useState(plan?.monthly_price ?? "");
  const [currency, setCurrency] = useState(plan?.currency ?? "USD");
  const [credits, setCredits] = useState(plan ? String(plan.included_credits) : "");
  const [quota, setQuota] = useState(plan ? String(plan.daily_quota) : "");
  const [active, setActive] = useState(plan?.active ?? true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const body: PlanInput = {
      name: name.trim(),
      monthly_price: price.trim(),
      currency: currency.trim().toUpperCase(),
      included_credits: creditsValue ?? 0,
      daily_quota: quotaValue ?? 0,
      active,
    };
    try {
      onSaved(plan ? await adminUpdatePlan(plan.plan_id, body) : await adminCreatePlan(body));
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "Could not save the plan.");
      setSaving(false);
    }
  }

  const creditsValue = toInteger(credits);
  const quotaValue = toInteger(quota);
  const ready = Boolean(name.trim() && price.trim() && currency.trim().length === 3 && creditsValue !== null && quotaValue !== null);

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
      title={plan ? `Edit ${plan.name}` : "New plan"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form={formId} loading={saving} disabled={!ready}>
            {plan ? "Save" : "Create plan"}
          </Button>
        </>
      }
    >
      <form id={formId} className="admin-form" onSubmit={(e) => void onSubmit(e)}>
        <TextInput label="Plan name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoComplete="off" />
        <TextInput
          label="Monthly price"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          inputMode="decimal"
          placeholder="49.90"
          required
          autoComplete="off"
        />
        <TextInput
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value)}
          maxLength={3}
          placeholder="USD"
          required
          autoComplete="off"
          helper="3-letter code."
        />
        <TextInput
          label="Included credits"
          value={credits}
          onChange={(e) => setCredits(e.target.value)}
          inputMode="numeric"
          required
          autoComplete="off"
        />
        <TextInput
          label="Maximum daily quota"
          value={quota}
          onChange={(e) => setQuota(e.target.value)}
          inputMode="numeric"
          required
          autoComplete="off"
          helper="Actions per day."
        />
        <Switch checked={active} onChange={setActive} label="Active" />
        {!active ? (
          <p className="c-3 t-body-sm">
            An inactive plan cannot be given to a client. Clients already on it stay on it until you move them.
          </p>
        ) : null}
        {error ? (
          <p className="admin-alert admin-alert--danger" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
