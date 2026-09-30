import { HandHelping, Sparkles, UserCheck } from "lucide-react";
import type { Tone } from "../mock/types";
import { useI18n, type MessageKey } from "../i18n";
import { Disclosure } from "../ui/Disclosure";
import { Segmented, ToggleChips } from "../ui/Controls";

export type { Tone };

/* ── The sales path: five plain steps, the last one is yours ── */
const PATH_KEYS: Array<{ titleKey: MessageKey; textKey: MessageKey; you?: boolean }> = [
  { titleKey: "strategy.path.understand.title", textKey: "strategy.path.understand.text" },
  { titleKey: "strategy.path.recommend.title", textKey: "strategy.path.recommend.text" },
  { titleKey: "strategy.path.register.title", textKey: "strategy.path.register.text" },
  { titleKey: "strategy.path.payment.title", textKey: "strategy.path.payment.text" },
  { titleKey: "strategy.path.confirm.title", textKey: "strategy.path.confirm.text", you: true },
];

export function SalesPath() {
  const { t } = useI18n();
  return (
    <div className="spath-wrap">
      <ol className="spath">
        {PATH_KEYS.map((step, i) => (
          <li key={step.titleKey} className="spath__step" data-you={step.you || undefined}>
            <span className="spath__node" aria-hidden>
              {step.you ? <UserCheck size={20} /> : <span className="tabular">{i + 1}</span>}
            </span>
            <div className="spath__text">
              <p className="t-h3">{t(step.titleKey)}</p>
              <p className="c-2">{t(step.textKey)}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="spath__note">
        <HandHelping size={22} aria-hidden />
        <span>{t("strategy.path.note")}</span>
      </p>
    </div>
  );
}

/* ── Language and style: what you allow, and how the agent adapts ── */
interface LanguageStyleProps {
  langs: string[];
  onLangs: (v: string[]) => void;
  defaultLang: string;
  onDefaultLang: (v: string) => void;
  tone: Tone;
  onTone: (v: Tone) => void;
  idSuffix?: string;
}

export function LanguageStyle({ langs, onLangs, defaultLang, onDefaultLang, tone, onTone, idSuffix = "" }: LanguageStyleProps) {
  const { t } = useI18n();
  const LANGUAGES = [
    { value: "fr", label: t("lang.fr") },
    { value: "ar", label: t("lang.ar") },
    { value: "en", label: t("lang.en") },
  ];
  const SIGNALS = [
    t("strategy.lang.signal.asks"),
    t("strategy.lang.signal.convo"),
    t("strategy.lang.signal.profile"),
    t("strategy.lang.signal.market"),
    t("strategy.lang.signal.default"),
  ];
  const allowed = LANGUAGES.filter((l) => langs.includes(l.value));
  const effectiveDefault = allowed.some((l) => l.value === defaultLang) ? defaultLang : (allowed[0]?.value ?? "");
  return (
    <div className="lstyle">
      <p className="callout">
        <Sparkles size={22} aria-hidden />
        <span>{t("strategy.lang.callout")}</span>
      </p>
      <div className="field">
        <span className="t-label c-2">{t("strategy.lang.languages")}</span>
        <ToggleChips label={t("strategy.lang.languages")} value={langs} onChange={onLangs} options={LANGUAGES} />
      </div>
      {allowed.length > 0 ? (
        <div className="field">
          <span className="t-label c-2">{t("strategy.lang.default")}</span>
          <Segmented layoutId={`seg-deflang${idSuffix}`} label={t("strategy.lang.default")} value={effectiveDefault} onChange={onDefaultLang} options={allowed} />
          <p className="c-3 t-body-sm">{t("strategy.lang.default.help")}</p>
        </div>
      ) : (
        <p className="c-attention">{t("strategy.lang.chooseOne")}</p>
      )}
      <div className="field">
        <span className="t-label c-2">{t("strategy.lang.style")}</span>
        <Segmented
          layoutId={`seg-tone${idSuffix}`}
          label={t("strategy.lang.style")}
          value={tone}
          onChange={onTone}
          options={[
            { value: "formal", label: t("strategy.lang.style.formal") },
            { value: "neutral", label: t("strategy.lang.style.neutral") },
            { value: "friendly", label: t("strategy.lang.style.friendly") },
          ]}
        />
        <p className="c-3 t-body-sm">{t("strategy.lang.style.help")}</p>
      </div>
      <Disclosure title={t("strategy.lang.disclosure.title")}>
        <ol className="signals">
          {SIGNALS.map((s, i) => (
            <li key={s}>
              <span className="signals__n tabular">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
        <p className="c-2">{t("strategy.lang.disclosure.p1")}</p>
        <p className="c-2">{t("strategy.lang.disclosure.p2")}</p>
      </Disclosure>
    </div>
  );
}
