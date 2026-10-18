import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { en, type MessageKey } from "./en";
import { ar } from "./ar";
import { frClient } from "./client.fr";
import { frLegacy } from "./client.legacy.fr";

export type Locale = "en" | "fr" | "ar";
export type Direction = "ltr" | "rtl";
export type { MessageKey };

const catalogs: Record<Locale, Partial<Record<MessageKey, string>>> = { en, fr: { ...frLegacy, ...frClient }, ar };
const DIRECTION: Record<Locale, Direction> = { en: "ltr", fr: "ltr", ar: "rtl" };
const INTL: Record<Locale, string> = { en: "en", fr: "fr", ar: "ar-TN-u-nu-latn" };

type Vars = Record<string, string | number>;

interface I18n {
  locale: Locale;
  dir: Direction;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, vars?: Vars) => string;
  /** Formats a number in the interface locale (Intl), never by hand. */
  n: (value: number, options?: Intl.NumberFormatOptions) => string;
  /** Formats a date in the interface locale (Intl), never by hand. */
  d: (value: string | Date, options?: Intl.DateTimeFormatOptions) => string;
}

const Ctx = createContext<I18n | null>(null);

function initialLocale(): Locale {
  try {
    const saved = window.localStorage.getItem("revansa.locale");
    if (saved === "en" || saved === "fr" || saved === "ar") return saved;
    const browser = window.navigator.language.slice(0, 2).toLowerCase();
    if (browser === "fr" || browser === "ar") return browser;
  } catch {
    /* storage can be unavailable; the interface still renders */
  }
  return "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const dir = DIRECTION[locale];

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = dir;
  }, [locale, dir]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem("revansa.locale", next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<I18n>(() => {
    const t = (key: MessageKey, vars?: Vars) => {
      const raw = catalogs[locale][key] ?? en[key] ?? key;
      return vars
        ? raw.replace(/\{(\w+)\}/g, (_, name: string) => {
            const v = vars[name];
            if (v === undefined) return `{${name}}`;
            // Isolate numbers/mixed-script values so they keep their own left-to-right
            // reading order instead of being pulled to the wrong end of an RTL sentence.
            return typeof v === "number" ? `⁦${v}⁩` : String(v);
          })
        : raw;
    };
    const n = (v: number, options?: Intl.NumberFormatOptions) =>
      new Intl.NumberFormat(INTL[locale], options).format(v);
    const d = (v: string | Date, options: Intl.DateTimeFormatOptions = { day: "numeric", month: "long", year: "numeric" }) =>
      new Intl.DateTimeFormat(INTL[locale], options).format(typeof v === "string" ? new Date(v) : v);
    return { locale, dir, setLocale, t, n, d };
  }, [locale, dir, setLocale]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}
