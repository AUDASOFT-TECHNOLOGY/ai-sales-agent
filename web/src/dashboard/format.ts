import type { Indicator } from "./types";

/** An indicator as text: its value, or a dash when it is not tracked (never "0"). */
export function indicatorText(ind: Indicator | undefined, n: (v: number) => string = String, suffix = ""): string {
  if (!ind || ind.value === null) return "—";
  return `${n(ind.value)}${suffix}`;
}

/** A decimal string as a number for drawing (amounts are exact strings on the wire). */
export function amount(value: string | undefined | null): number | null {
  if (value === undefined || value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function money(value: string | undefined | null, currency: string, locale = "en"): string {
  const parsed = amount(value);
  if (parsed === null) return "—";
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(parsed);
  } catch {
    return `${parsed.toFixed(2)} ${currency}`;
  }
}

/** Per-currency amounts as one line ("30.00 USD, 40.00 EUR"); currencies are never added. */
export function moneyMap(map: Record<string, string> | undefined, locale = "en"): string {
  const entries = Object.entries(map ?? {});
  if (entries.length === 0) return "—";
  return entries.map(([currency, value]) => money(value, currency, locale)).join(", ");
}

export function monthDefaults(now = new Date()): { from: string; to: string } {
  const to = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const from = new Date(Date.UTC(to.getUTCFullYear(), to.getUTCMonth() - 5, 1));
  const label = (d: Date) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  return { from: label(from), to: label(to) };
}
