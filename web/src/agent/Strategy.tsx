import { HandHelping, Sparkles, UserCheck } from "lucide-react";
import type { Tone } from "../mock/types";
import { Disclosure } from "../ui/Disclosure";
import { Segmented, ToggleChips } from "../ui/Controls";

export type { Tone };

/* ── The sales path: five plain steps, the last one is yours ── */
const PATH: Array<{ title: string; text: string; you?: boolean }> = [
  { title: "Understand", text: "I ask a few questions to learn what they need." },
  { title: "Recommend", text: "I suggest the offer that fits them." },
  { title: "Register", text: "I help them sign up." },
  { title: "Payment", text: "I share how to pay." },
  { title: "Your confirmation", text: "You check the payment and confirm it.", you: true },
];

export function SalesPath() {
  return (
    <div className="spath-wrap">
      <ol className="spath">
        {PATH.map((step, i) => (
          <li key={step.title} className="spath__step" data-you={step.you || undefined}>
            <span className="spath__node" aria-hidden>
              {step.you ? <UserCheck size={20} /> : <span className="tabular">{i + 1}</span>}
            </span>
            <div className="spath__text">
              <p className="t-h3">{step.title}</p>
              <p className="c-2">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="spath__note">
        <HandHelping size={22} aria-hidden />
        <span>You can take over a conversation at any time.</span>
      </p>
    </div>
  );
}

/* ── Language and style: what you allow, and how the agent adapts ── */
const LANGUAGES = [
  { value: "fr", label: "French" },
  { value: "ar", label: "Arabic" },
  { value: "en", label: "English" },
];

interface LanguageStyleProps {
  langs: string[];
  onLangs: (v: string[]) => void;
  defaultLang: string;
  onDefaultLang: (v: string) => void;
  tone: Tone;
  onTone: (v: Tone) => void;
  idSuffix?: string;
}

const SIGNALS = [
  "What the prospect asks for",
  "The language of the conversation",
  "What we know from their profile",
  "Their market or country, when it is known",
  "Your default language",
];

export function LanguageStyle({ langs, onLangs, defaultLang, onDefaultLang, tone, onTone, idSuffix = "" }: LanguageStyleProps) {
  const allowed = LANGUAGES.filter((l) => langs.includes(l.value));
  const effectiveDefault = allowed.some((l) => l.value === defaultLang) ? defaultLang : (allowed[0]?.value ?? "");
  return (
    <div className="lstyle">
      <p className="callout">
        <Sparkles size={22} aria-hidden />
        <span>Your agent adapts its language and communication style to each prospect and conversation.</span>
      </p>
      <div className="field">
        <span className="t-label c-2">Languages it may use</span>
        <ToggleChips label="Languages it may use" value={langs} onChange={onLangs} options={LANGUAGES} />
      </div>
      {allowed.length > 0 ? (
        <div className="field">
          <span className="t-label c-2">Default language</span>
          <Segmented layoutId={`seg-deflang${idSuffix}`} label="Default language" value={effectiveDefault} onChange={onDefaultLang} options={allowed} />
          <p className="c-3 t-body-sm">Used for the first message, until the agent learns more about the prospect.</p>
        </div>
      ) : (
        <p className="c-attention">Choose at least one language.</p>
      )}
      <div className="field">
        <span className="t-label c-2">Starting style</span>
        <Segmented
          layoutId={`seg-tone${idSuffix}`}
          label="Starting style"
          value={tone}
          onChange={onTone}
          options={[
            { value: "formal", label: "Formal" },
            { value: "neutral", label: "Neutral" },
            { value: "friendly", label: "Friendly" },
          ]}
        />
        <p className="c-3 t-body-sm">Where the agent starts. It adjusts to each prospect from there.</p>
      </div>
      <Disclosure title="How your agent chooses the language">
        <ol className="signals">
          {SIGNALS.map((s, i) => (
            <li key={s}>
              <span className="signals__n tabular">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
        <p className="c-2">The first message uses your default language and style. The agent adapts as soon as it has new signals.</p>
        <p className="c-2">The country code of a WhatsApp number can hint at context. It is not proof of nationality, and it never outweighs the steps above.</p>
      </Disclosure>
    </div>
  );
}
