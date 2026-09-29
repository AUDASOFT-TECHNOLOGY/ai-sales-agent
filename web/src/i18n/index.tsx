import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { en, type MessageKey } from "./en";
import { ar } from "./ar";

export type Locale = "en" | "ar";
export type Direction = "ltr" | "rtl";
export type { MessageKey };

const catalogs: Record<Locale, Partial<Record<MessageKey, string>>> = { en, ar };
const DIRECTION: Record<Locale, Direction> = { en: "ltr", ar: "rtl" };

type Vars = Record<string, string | number>;

interface I18n {
  locale: Locale;
  dir: Direction;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, vars?: Vars) => string;
  /** Formats a number in the interface locale (Intl), never by hand. */
  n: (value: number, options?: Intl.NumberFormatOptions) => string;
}

const Ctx = createContext<I18n | null>(null);

function initialLocale(): Locale {
  try {
    const saved = window.localStorage.getItem("revensa.locale");
    if (saved === "en" || saved === "ar") return saved;
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
      window.localStorage.setItem("revensa.locale", next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<I18n>(() => {
    const t = (key: MessageKey, vars?: Vars) => {
      const raw = catalogs[locale][key] ?? en[key] ?? key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name: string) => String(vars[name] ?? `{${name}}`)) : raw;
    };
    const n = (v: number, options?: Intl.NumberFormatOptions) =>
      new Intl.NumberFormat(locale === "ar" ? "ar-TN-u-nu-latn" : "en", options).format(v);
    return { locale, dir, setLocale, t, n };
  }, [locale, dir, setLocale]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}
