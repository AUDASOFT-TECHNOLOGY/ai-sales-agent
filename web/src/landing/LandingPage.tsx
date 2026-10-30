/** Public landing page. Everything it says about the product is true of the product today; the only
 *  invented thing is the demo conversations, which are labelled as fictional and never leave this page.
 *  Prices, credits and quotas come from the plans the administrator configured (no figure is typed here).
 *  Visuals are CSS and inline SVG: no image to load, nothing heavy. */

import { useCallback, useEffect, type ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  Boxes,
  Check,
  CreditCard,
  EyeOff,
  HandHelping,
  History,
  Mail,
  MessageCircle,
  MessagesSquare,
  Plug,
  RefreshCw,
  Send,
  Sparkles,
  Users,
  Bot,
  Clock,
  UserX,
  Layers,
  type LucideIcon,
} from "lucide-react";
import { useClientAuth } from "../client/ClientAuthContext";
import { publicPlans, type PublicPlan } from "../client/accessApi";
import { PlanFacts, planPrice } from "../client/PlansPage";
import { useFetched } from "../client/useFetched";
import { useI18n, type Locale, type MessageKey } from "../i18n";
import { BrandMark } from "../ui/Brand";
import { LinkButton } from "../ui/Button";
import { cx } from "../ui/cx";
import "./landing.css";

/** Signed-in visitors go to their space; everyone else sees the landing. */
export function RootEntry() {
  const { status } = useClientAuth();
  if (status === "authenticated") return <Navigate to="/home" replace />;
  return <LandingPage />;
}

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

function goTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

function useDocumentMeta(title: string, description: string) {
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    const set = (selector: string, attr: string, key: string, value: string) => {
      let el = document.head.querySelector<HTMLMetaElement>(selector);
      if (!el) {
        el = document.createElement("meta");
        el.setAttribute(attr, key);
        document.head.appendChild(el);
      }
      el.setAttribute("content", value);
    };
    set('meta[name="description"]', "name", "description", description);
    set('meta[property="og:title"]', "property", "og:title", title);
    set('meta[property="og:description"]', "property", "og:description", description);
    return () => {
      document.title = previous;
    };
  }, [title, description]);
}

const LOCALES: Array<{ id: Locale; label: string; name: string }> = [
  { id: "en", label: "EN", name: "English" },
  { id: "fr", label: "FR", name: "Français" },
  { id: "ar", label: "ع", name: "العربية" },
];

function LanguageSwitch() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div className="lp-lang" role="group" aria-label={t("bar.language")}>
      {LOCALES.map((l) => (
        <button key={l.id} type="button" lang={l.id} aria-label={l.name} aria-pressed={locale === l.id} className={cx("lp-lang__btn", locale === l.id && "is-on")} onClick={() => setLocale(l.id)}>
          {l.label}
        </button>
      ))}
    </div>
  );
}

function Header() {
  const { t } = useI18n();
  const items: Array<[string, MessageKey]> = [
    ["features", "lp.nav.features"],
    ["how", "lp.nav.how"],
    ["pricing", "lp.nav.pricing"],
    ["faq", "lp.nav.faq"],
  ];
  return (
    <header className="lp-header">
      <div className="lp-wrap lp-header__row">
        <Link to="/" className="lp-brand" aria-label={t("brand.name")}>
          <BrandMark size={28} />
          <span dir="ltr">{t("brand.name")}</span>
        </Link>
        <nav aria-label={t("lp.nav.label")} className="lp-nav">
          {items.map(([id, key]) => (
            <button key={id} type="button" className="lp-nav__link" onClick={() => goTo(id)}>
              {t(key)}
            </button>
          ))}
        </nav>
        <div className="lp-header__end">
          <LanguageSwitch />
          <Link to="/login" className="lp-nav__link lp-login">
            {t("lp.nav.login")}
          </Link>
          <LinkButton to="/signup" variant="primary" size="sm">
            {t("lp.cta.start")}
          </LinkButton>
        </div>
      </div>
    </header>
  );
}

/* ── Hero ─────────────────────────────────────────────────────── */

function HeroVisual() {
  const { t } = useI18n();
  const rows: Array<{ Icon: LucideIcon; channel: string; who: MessageKey; what: MessageKey }> = [
    { Icon: Mail, channel: "email", who: "lp.demo.p1", what: "lp.demo.row.email" },
    { Icon: Send, channel: "telegram", who: "lp.demo.p2", what: "lp.demo.row.telegram" },
    { Icon: MessageCircle, channel: "whatsapp", who: "lp.demo.p3", what: "lp.demo.row.whatsapp" },
  ];
  const stages: MessageKey[] = ["lp.demo.stage.contacted", "lp.demo.stage.replied", "lp.demo.stage.quote", "lp.demo.stage.followup", "lp.demo.stage.won"];
  return (
    <div className="lp-console" role="img" aria-label={`${t("lp.demo.agent")}: ${t("lp.demo.badge")}`}>
      <div className="lp-console__glow" aria-hidden />
      <div className="lp-glass lp-console__panel" aria-hidden>
        <div className="lp-console__top">
          <span className="lp-orb" />
          <strong>{t("lp.demo.agent")}</strong>
          <span className="lp-chip lp-chip--ok">{t("lp.demo.active")}</span>
        </div>
        <p className="lp-console__label">{t("lp.demo.channels")}</p>
        <ul className="lp-console__rows">
          {rows.map(({ Icon, channel, who, what }, i) => (
            <li key={channel} className="lp-convo" style={{ ["--i" as string]: i }}>
              <span className={`lp-ch lp-ch--${channel}`}>
                <Icon size={16} />
              </span>
              <span className="lp-convo__text">
                <b>{t(who)}</b>
                <span>{t(what)}</span>
              </span>
              <span className="lp-typing">
                <i />
                <i />
                <i />
              </span>
            </li>
          ))}
        </ul>
        <p className="lp-console__label">{t("lp.demo.pipeline")}</p>
        <ol className="lp-stages">
          {stages.map((s, i) => (
            <li key={s} className={i < 4 ? "is-done" : "is-next"}>
              <span className="lp-stages__dot" />
              <span>{t(s)}</span>
            </li>
          ))}
        </ol>
      </div>
      <span className="lp-float lp-float--a" aria-hidden>
        <RefreshCw size={14} /> {t("lp.omni.whatsapp.followup")}
      </span>
      <span className="lp-float lp-float--b" aria-hidden>
        <HandHelping size={14} /> {t("lp.omni.whatsapp.human")}
      </span>
      <p className="lp-demo-badge">{t("lp.demo.badge")}</p>
    </div>
  );
}

function Hero() {
  const { t } = useI18n();
  return (
    <section className="lp-hero lp-wrap" aria-labelledby="lp-hero-title">
      <div className="lp-hero__copy">
        <p className="lp-eyebrow">{t("lp.hero.eyebrow")}</p>
        <h1 id="lp-hero-title" className="lp-h1">
          {t("lp.hero.title")}
        </h1>
        <p className="lp-lead">{t("lp.hero.sub")}</p>
        <div className="lp-actions">
          <LinkButton to="/signup" variant="primary" size="lg" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
            {t("lp.cta.start")}
          </LinkButton>
          <button type="button" className="btn" data-variant="secondary" data-size="lg" onClick={() => goTo("how")}>
            <span className="btn__label">{t("lp.cta.see")}</span>
          </button>
        </div>
        <p className="lp-note">{t("lp.hero.note")}</p>
      </div>
      <HeroVisual />
    </section>
  );
}

/* ── Section scaffolding ─────────────────────────────────────── */

function Section({ id, eyebrow, title, sub, children, className }: { id?: string; eyebrow?: string; title: string; sub?: string; children: ReactNode; className?: string }) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section id={id} className={cx("lp-section lp-wrap", className)} aria-labelledby={headingId}>
      <header className="lp-section__head">
        {eyebrow ? <p className="lp-eyebrow">{eyebrow}</p> : null}
        <h2 id={headingId} className="lp-h2">
          {title}
        </h2>
        {sub ? <p className="lp-lead">{sub}</p> : null}
      </header>
      {children}
    </section>
  );
}

function Problem() {
  const { t } = useI18n();
  const pains: Array<{ Icon: LucideIcon; key: string }> = [
    { Icon: UserX, key: "forgotten" },
    { Icon: Clock, key: "manual" },
    { Icon: Layers, key: "scattered" },
    { Icon: MessagesSquare, key: "lost" },
    { Icon: EyeOff, key: "blind" },
  ];
  const wins: MessageKey[] = ["lp.solution.a", "lp.solution.b", "lp.solution.c", "lp.solution.d"];
  return (
    <Section eyebrow={t("lp.problem.eyebrow")} title={t("lp.problem.title")} sub={t("lp.problem.sub")}>
      <ul className="lp-grid lp-grid--5">
        {pains.map(({ Icon, key }) => (
          <li key={key} className="lp-glass lp-pain">
            <span className="lp-icon lp-icon--warn" aria-hidden>
              <Icon size={18} />
            </span>
            <h3>{t(`lp.problem.${key}.title` as MessageKey)}</h3>
            <p>{t(`lp.problem.${key}.text` as MessageKey)}</p>
          </li>
        ))}
      </ul>
      <div className="lp-glass lp-solution">
        <span className="lp-icon" aria-hidden>
          <Sparkles size={20} />
        </span>
        <div>
          <p className="lp-eyebrow">{t("lp.solution.eyebrow")}</p>
          <h3 className="lp-h3">{t("lp.solution.title")}</h3>
          <p className="lp-muted">{t("lp.solution.text")}</p>
        </div>
        <ul className="lp-checks">
          {wins.map((k) => (
            <li key={k}>
              <Check size={16} aria-hidden /> {t(k)}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

function How() {
  const { t } = useI18n();
  return (
    <Section id="how" eyebrow={t("lp.how.eyebrow")} title={t("lp.how.title")}>
      <ol className="lp-steps">
        {[1, 2, 3, 4].map((n) => (
          <li key={n} className="lp-glass lp-step">
            <span className="lp-step__n" aria-hidden>
              {n}
            </span>
            <h3>{t(`lp.how.${n}.title` as MessageKey)}</h3>
            <p>{t(`lp.how.${n}.text` as MessageKey)}</p>
          </li>
        ))}
      </ol>
    </Section>
  );
}

function Features() {
  const { t } = useI18n();
  // Each of these exists in the product today. The one thing still to come says so.
  const items: Array<{ Icon: LucideIcon; key: string }> = [
    { Icon: Bot, key: "agent" },
    { Icon: MessagesSquare, key: "channels" },
    { Icon: Boxes, key: "products" },
    { Icon: Users, key: "prospects" },
    { Icon: RefreshCw, key: "followups" },
    { Icon: HandHelping, key: "human" },
    { Icon: CreditCard, key: "payment" },
    { Icon: History, key: "history" },
    { Icon: BarChart3, key: "reporting" },
    { Icon: Plug, key: "crm" },
  ];
  return (
    <Section id="features" eyebrow={t("lp.features.eyebrow")} title={t("lp.features.title")}>
      <ul className="lp-grid lp-grid--features">
        {items.map(({ Icon, key }) => (
          <li key={key} className="lp-glass lp-feature">
            <span className="lp-icon" aria-hidden>
              <Icon size={18} />
            </span>
            <h3>{t(`lp.features.${key}.title` as MessageKey)}</h3>
            <p>{t(`lp.features.${key}.text` as MessageKey)}</p>
            <span className="lp-chip lp-chip--ok">{t("lp.features.available")}</span>
            {key === "crm" ? <span className="lp-chip lp-chip--soon">{t("lp.features.crm.soon")}</span> : null}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Bubble({ from, children }: { from: "agent" | "prospect"; children: ReactNode }) {
  return <p className={cx("lp-bubble", from === "agent" ? "lp-bubble--agent" : "lp-bubble--them")}>{children}</p>;
}

function Omni() {
  const { t } = useI18n();
  return (
    <Section id="demo" eyebrow={t("lp.omni.eyebrow")} title={t("lp.omni.title")} sub={t("lp.omni.sub")}>
      <p className="lp-demo-note">{t("lp.omni.demo")}</p>
      <div className="lp-grid lp-grid--3">
        <article className="lp-glass lp-thread">
          <header>
            <span className="lp-ch lp-ch--email"><Mail size={16} aria-hidden /></span>
            <b>{t("lp.omni.email.name")}</b>
            <span className="lp-muted">{t("lp.omni.email.tag")}</span>
          </header>
          <Bubble from="agent">{t("lp.omni.email.agent")}</Bubble>
          <Bubble from="prospect">{t("lp.omni.email.prospect")}</Bubble>
          <span className="lp-chip lp-chip--info">{t("lp.omni.email.quote")}</span>
        </article>
        <article className="lp-glass lp-thread">
          <header>
            <span className="lp-ch lp-ch--telegram"><Send size={16} aria-hidden /></span>
            <b>{t("lp.omni.telegram.name")}</b>
            <span className="lp-muted">{t("lp.omni.telegram.tag")}</span>
          </header>
          <Bubble from="prospect">{t("lp.omni.telegram.prospect")}</Bubble>
          <Bubble from="agent">{t("lp.omni.telegram.agent")}</Bubble>
          <span className="lp-chip lp-chip--warn">{t("lp.omni.telegram.objection")}</span>
        </article>
        <article className="lp-glass lp-thread">
          <header>
            <span className="lp-ch lp-ch--whatsapp"><MessageCircle size={16} aria-hidden /></span>
            <b>{t("lp.omni.whatsapp.name")}</b>
            <span className="lp-muted">{t("lp.omni.whatsapp.tag")}</span>
          </header>
          <Bubble from="agent">{t("lp.omni.whatsapp.agent")}</Bubble>
          <Bubble from="prospect">{t("lp.omni.whatsapp.prospect")}</Bubble>
          <div className="lp-chips">
            <span className="lp-chip lp-chip--ok">{t("lp.omni.whatsapp.followup")}</span>
            <span className="lp-chip lp-chip--human">{t("lp.omni.whatsapp.human")}</span>
          </div>
          <p className="lp-handoff">
            <HandHelping size={14} aria-hidden /> {t("lp.omni.handoff")}
          </p>
        </article>
      </div>
    </Section>
  );
}

function PlanCard({ plan, featured }: { plan: PublicPlan; featured: boolean }) {
  const { t, n } = useI18n();
  return (
    <li className={cx("lp-glass lp-plan", featured && "is-featured")} data-testid={`landing-plan-${plan.plan_id}`}>
      {featured ? <span className="lp-plan__flag">{t("lp.pricing.featured")}</span> : null}
      <h3>{plan.name}</h3>
      <p className="lp-plan__price">
        <span>{planPrice(plan, n)}</span> <small>{plan.billing_period === "annual" ? t("lp.pricing.year") : t("lp.pricing.month")}</small>
      </p>
      <PlanFacts plan={plan} />
      <LinkButton to={`/signup?plan=${encodeURIComponent(plan.plan_id)}`} variant={featured ? "primary" : "secondary"}>
        {t("lp.pricing.cta", { name: plan.name })}
      </LinkButton>
    </li>
  );
}

function Pricing() {
  const { t } = useI18n();
  const load = useCallback(() => publicPlans(), []);
  const plans = useFetched(load);
  const list = plans.value ?? [];
  // No plan is singled out by name: with three plans the middle one is highlighted, otherwise none.
  const featuredId = list.length === 3 ? list[1]!.plan_id : null;
  return (
    <Section id="pricing" eyebrow={t("lp.pricing.eyebrow")} title={t("lp.pricing.title")} sub={t("lp.pricing.sub")}>
      {plans.loading ? (
        <p className="lp-muted" role="status">
          {t("lp.pricing.loading")}
        </p>
      ) : plans.failed || list.length === 0 ? (
        <div className="lp-glass lp-empty" role="status">
          <p>{t("lp.pricing.unavailable")}</p>
          <LinkButton to="/signup" variant="primary">
            {t("lp.cta.start")}
          </LinkButton>
        </div>
      ) : (
        <ul className="lp-grid lp-grid--plans">
          {list.map((p) => (
            <PlanCard key={p.plan_id} plan={p} featured={p.plan_id === featuredId} />
          ))}
        </ul>
      )}
      <p className="lp-fineprint">{t("lp.pricing.meta")}</p>
    </Section>
  );
}

function Faq() {
  const { t } = useI18n();
  return (
    <Section id="faq" eyebrow={t("lp.faq.eyebrow")} title={t("lp.faq.title")} className="lp-faq">
      <div className="lp-faq__list">
        {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
          <details key={n} className="lp-glass lp-qa">
            <summary>{t(`lp.faq.${n}.q` as MessageKey)}</summary>
            <p>{t(`lp.faq.${n}.a` as MessageKey)}</p>
          </details>
        ))}
      </div>
    </Section>
  );
}

function FinalCta() {
  const { t } = useI18n();
  return (
    <section className="lp-wrap lp-final" aria-labelledby="lp-final-title">
      <div className="lp-glass lp-final__card">
        <h2 id="lp-final-title" className="lp-h2">
          {t("lp.final.title")}
        </h2>
        <p className="lp-lead">{t("lp.final.text")}</p>
        <LinkButton to="/signup" variant="primary" size="lg" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
          {t("lp.cta.start")}
        </LinkButton>
      </div>
    </section>
  );
}

/** The addresses of the legal pages and of the contact are the company's to give: they come from the
 *  build settings. Until one is set, the entry says "coming soon" instead of pointing nowhere. */
function externalLink(value: unknown): string | null {
  return typeof value === "string" && /^(https?:|mailto:)/.test(value) ? value : null;
}

function Footer() {
  const { t } = useI18n();
  const env = import.meta.env as Record<string, unknown>;
  const external: Array<[MessageKey, string | null]> = [
    ["lp.footer.contact", externalLink(env.VITE_CONTACT_URL)],
    ["lp.footer.privacy", externalLink(env.VITE_PRIVACY_URL)],
    ["lp.footer.terms", externalLink(env.VITE_TERMS_URL)],
  ];
  return (
    <footer className="lp-footer">
      <div className="lp-wrap lp-footer__row">
        <div>
          <span className="lp-brand">
            <BrandMark size={24} />
            <span dir="ltr">{t("brand.name")}</span>
          </span>
          <p className="lp-muted">{t("lp.footer.tagline")}</p>
        </div>
        <nav aria-label={t("lp.footer.nav")}>
          <ul className="lp-footer__links">
            <li>
              <button type="button" className="lp-nav__link" onClick={() => goTo("pricing")}>
                {t("lp.footer.pricing")}
              </button>
            </li>
            {external.map(([key, href]) => (
              <li key={key}>
                {href ? (
                  <a className="lp-nav__link" href={href} rel="noopener noreferrer">
                    {t(key)}
                  </a>
                ) : (
                  <span className="lp-footer__soon">
                    {t(key)} <small>· {t("lp.footer.soon")}</small>
                  </span>
                )}
              </li>
            ))}
            <li>
              <Link className="lp-nav__link" to="/login">
                {t("lp.footer.login")}
              </Link>
            </li>
          </ul>
        </nav>
      </div>
    </footer>
  );
}

export function LandingPage() {
  const { t } = useI18n();
  useDocumentMeta(t("lp.meta.title"), t("lp.meta.description"));
  return (
    <div className="lp">
      <button type="button" className="lp-skip" onClick={() => goTo("lp-main")}>
        {t("lp.skip")}
      </button>
      <Header />
      <main id="lp-main" tabIndex={-1}>
        <Hero />
        <Problem />
        <How />
        <Features />
        <Omni />
        <Pricing />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}
