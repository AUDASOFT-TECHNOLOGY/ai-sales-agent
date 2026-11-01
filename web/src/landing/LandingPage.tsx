/** Public landing page. Everything it says about the product is true of the product today; the only
 *  invented thing is the demo conversations, which are labelled as fictional and never leave this page.
 *  Prices, credits and quotas come from the plans the administrator configured (no figure is typed here).
 *  Visuals are CSS and inline SVG: no image to load, nothing heavy. */

import { useCallback, useEffect, useRef, type ComponentType, type ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  BellRing,
  Check,
  CreditCard,
  FileText,
  Flame,
  Globe,
  History,
  Mail,
  MessageCircle,
  MessagesSquare,
  Mic,
  MoonStar,
  Package,
  PenLine,
  Radar,
  Send,
  ShieldCheck,
  Sparkles,
  ShoppingBag,
  RefreshCw,
  UserPlus,
} from "lucide-react";
import { useClientAuth } from "../client/ClientAuthContext";
import { publicPageKeys, publicPlans, type PublicPlan } from "../client/accessApi";
import { PlanFacts, planPrice } from "../client/PlansPage";
import { useFetched } from "../client/useFetched";
import { useI18n, type Locale, type MessageKey } from "../i18n";
import { BrandMark } from "../ui/Brand";
import { LinkButton } from "../ui/Button";
import { cx } from "../ui/cx";
import { Avatar, Board,CardsArt, ClockArt, CloudArt, ControlArt, DayRing, Orbit, ReportArt, SlidersArt, TalkArt } from "./LandingArt";
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

/** Fades sections in as they scroll into view. Without IntersectionObserver (or with reduced motion) everything is shown at once. */
function useReveal(root: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const host = root.current;
    if (!host) return;
    const items = Array.from(host.querySelectorAll<HTMLElement>(".lp-reveal"));
    if (reducedMotion() || typeof IntersectionObserver === "undefined") {
      items.forEach((el) => el.classList.add("is-in"));
      return;
    }
    host.classList.add("lp--ready");
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("is-in");
            io.unobserve(e.target);
          }
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -6% 0px" },
    );
    items.forEach((el) => io.observe(el));
    return () => {
      io.disconnect();
      host.classList.remove("lp--ready");
    };
  }, [root]);
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

/** One looping scene: a prospect writes at night and the agent closes the sale, while the day's actions tick
 *  off and the CEO report builds up around the conversation. All of it is a labelled, fictional example. */
function ChatDemo() {
  const { t } = useI18n();
  const queue: Array<{ Icon: ComponentType<{ size?: number }>; n: number; key: MessageKey }> = [
    { Icon: UserPlus, n: 3, key: "lp.hero.q.new" },
    { Icon: RefreshCw, n: 2, key: "lp.hero.q.follow" },
    { Icon: BellRing, n: 1, key: "lp.hero.q.remind" },
  ];
  const context: Array<{ Icon: ComponentType<{ size?: number }>; key: MessageKey }> = [
    { Icon: History, key: "lp.hero.ctx.history" },
    { Icon: Globe, key: "lp.hero.ctx.country" },
    { Icon: Flame, key: "lp.hero.ctx.interest" },
  ];
  return (
    <div className="lp-console" role="img" aria-label={`${t("lp.demo.agent")}: ${t("lp.demo.badge")}`}>
      <div className="lp-console__glow" aria-hidden />
      <div className="lp-glass lp-queue lp-node" style={{ ["--s" as string]: 0 }} aria-hidden>
        <em className="lp-node__n">1</em>
        <b className="lp-queue__title">{t("lp.hero.q.title")}</b>
        <ul>
          {queue.map(({ Icon, n, key }, i) => (
            <li key={key} style={{ ["--i" as string]: i }}>
              <Icon size={15} />
              <span>
                <b dir="ltr">{n}</b> {t(key)}
              </span>
              <Check size={14} className="lp-queue__ok" />
            </li>
          ))}
        </ul>
      </div>
      <div className="lp-glass lp-chat lp-node" style={{ ["--s" as string]: 1 }} aria-hidden>
        <em className="lp-node__n">2</em>
        <div className="lp-chat__top">
          <Avatar name={t("lp.board.p1")} hue={265} />
          <span className="lp-chat__who">
            <strong>{t("lp.board.p1")}</strong>
            <small>
              <span className="lp-orb" /> {t("lp.demo.active")}
            </small>
          </span>
          <span className="lp-ch lp-ch--whatsapp lp-chat__ch">
            <MessageCircle size={14} />
          </span>
          <span className="lp-chat__time">
            <MoonStar size={14} /> <span dir="ltr">{t("lp.demo.time")}</span>
          </span>
        </div>
        <ul className="lp-ctx">
          {context.map(({ Icon, key }) => (
            <li key={key}>
              <Icon size={12} /> {t(key)}
            </li>
          ))}
        </ul>
        <p className="lp-msg lp-msg--them" style={{ ["--n" as string]: 1 }}>
          {t("lp.demo.q")}
        </p>
        <p className="lp-msg lp-msg--agent" style={{ ["--n" as string]: 2 }}>
          {t("lp.demo.a")}
        </p>
        <p className="lp-msg lp-msg--them" style={{ ["--n" as string]: 3 }}>
          {t("lp.demo.ok")}
        </p>
        <p className="lp-msg lp-msg--agent" style={{ ["--n" as string]: 4 }}>
          {t("lp.demo.sent")}
        </p>
        <p className="lp-msg lp-won" style={{ ["--n" as string]: 5 }}>
          <Check size={16} /> {t("lp.demo.won")}
        </p>
      </div>
      <div className="lp-glass lp-kpi lp-node" style={{ ["--s" as string]: 2 }} aria-hidden>
        <em className="lp-node__n">3</em>
        <b className="lp-kpi__title">
          <BarChart3 size={14} /> {t("lp.hero.kpi.title")}
        </b>
        <span className="lp-kpi__bars">
          {[0, 1, 2, 3, 4].map((k) => (
            <i key={k} style={{ ["--k" as string]: k }} />
          ))}
        </span>
        <span className="lp-kpi__row">
          <b dir="ltr">+2</b> {t("lp.hero.kpi.sales")}
        </span>
        <span className="lp-kpi__sent">
          <Mail size={12} /> {t("lp.hero.kpi.sent")}
        </span>
      </div>
      <p className="lp-demo-badge">{t("lp.demo.badge")}</p>
    </div>
  );
}

function Hero() {
  const { t } = useI18n();
  return (
    <section className="lp-hero lp-wrap" aria-labelledby="lp-hero-title">
      <div className="lp-hero__copy">
        <p className="lp-pill">
          <Sparkles size={14} aria-hidden /> {t("lp.hero.eyebrow")}
        </p>
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
        <ul className="lp-trust">
          {(["lp.trust.card", "lp.trust.credits", "lp.trust.control"] as MessageKey[]).map((k) => (
            <li key={k}>
              <Check size={14} aria-hidden /> {t(k)}
            </li>
          ))}
        </ul>
      </div>
      <ChatDemo />
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

/** Two big pictures side by side: the channels around one agent, and the 24 hours it covers. */
function Showcase() {
  const { t } = useI18n();
  return (
    <section className="lp-wrap lp-showcase lp-reveal" aria-label={t("lp.orbit.title")}>
      <figure className="lp-glass lp-showcase__panel">
        <Orbit />
        <figcaption className="lp-h3">{t("lp.orbit.title")}</figcaption>
      </figure>
      <figure className="lp-glass lp-showcase__panel">
        <DayRing />
        <figcaption className="lp-h3">{t("lp.clock.title")}</figcaption>
      </figure>
    </section>
  );
}

/** Four benefits, each with its own small animated scene, a three-word title and one short line. */
function Values() {
  const { t } = useI18n();
  const items: Array<{ Art: ComponentType; n: number }> = [
    { Art: SlidersArt, n: 1 },
    { Art: CardsArt, n: 2 },
    { Art: ClockArt, n: 3 },
    { Art: CloudArt, n: 4 },
  ];
  return (
    <Section id="features" eyebrow={t("lp.values.eyebrow")} title={t("lp.values.title")} className="lp-reveal">
      <ul className="lp-grid lp-grid--values">
        {items.map(({ Art, n }) => (
          <li key={n} className="lp-glass lp-value">
            <Art />
            <h3>{t(`lp.values.${n}.title` as MessageKey)}</h3>
            <p>{t(`lp.values.${n}.text` as MessageKey)}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Pipeline() {
  const { t } = useI18n();
  return (
    <Section title={t("lp.board.title")} className="lp-reveal">
      <Board />
      <p className="lp-demo-badge lp-demo-badge--left">{t("lp.demo.badge")}</p>
    </Section>
  );
}

/** Three big nodes, one arrow between each, one highlighted at a time: train, sell, get paid. */
type HowItem = { id: string; Icon: ComponentType<{ size?: number }> };

function How() {
  const { t } = useI18n();
  const steps: Array<{ n: number; Art: ComponentType; items: HowItem[] }> = [
    {
      n: 1,
      Art: SlidersArt,
      items: [
        { id: "a", Icon: Package },
        { id: "b", Icon: Mic },
        { id: "c", Icon: ShieldCheck },
      ],
    },
    {
      n: 2,
      Art: TalkArt,
      items: [
        { id: "a", Icon: Radar },
        { id: "b", Icon: PenLine },
        { id: "c", Icon: MessagesSquare },
      ],
    },
    {
      n: 3,
      Art: ReportArt,
      items: [
        { id: "a", Icon: BarChart3 },
        { id: "b", Icon: FileText },
      ],
    },
  ];
  return (
    <Section id="how" eyebrow={t("lp.how.eyebrow")} title={t("lp.how.title")} className="lp-reveal">
      <ol className="lp-path" aria-label={t("lp.how.label")}>
        {steps.map(({ n, Art, items }) => (
          <li key={n} className="lp-path__step" style={{ ["--n" as string]: n }}>
            <span className="lp-path__badge" aria-hidden>
              <Art />
              <em>{n}</em>
            </span>
            <h3>{t(`lp.how.${n}.title` as MessageKey)}</h3>
            <ul className="lp-path__list">
              {items.map(({ id, Icon }, i) => (
                <li key={id} style={{ ["--i" as string]: i }}>
                  <span className="lp-path__ico" aria-hidden>
                    <Icon size={18} />
                  </span>
                  <span>
                    <b>{t(`lp.how.${n}.${id}.t` as MessageKey)}</b>
                    <small>{t(`lp.how.${n}.${id}.d` as MessageKey)}</small>
                  </span>
                </li>
              ))}
            </ul>
            {n === 2 ? (
              <span className="lp-path__loop" aria-hidden>
                <RefreshCw size={14} /> {t("lp.how.2.loop")}
              </span>
            ) : null}
            {n === 2 ? (
              <span className="lp-path__chs" aria-hidden>
                <span className="lp-ch lp-ch--email">
                  <Mail size={15} />
                </span>
                <span className="lp-ch lp-ch--telegram">
                  <Send size={15} />
                </span>
                <span className="lp-ch lp-ch--whatsapp">
                  <MessageCircle size={15} />
                </span>
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </Section>
  );
}

function Control() {
  const { t } = useI18n();
  return (
    <Section title={t("lp.control.title")} className="lp-reveal">
      <div className="lp-control">
        <ControlArt />
        <ul className="lp-control__list">
          {[1, 2, 3].map((n) => (
            <li key={n}>
              <Check size={18} aria-hidden /> {t(`lp.control.${n}` as MessageKey)}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

function FreeCard() {
  const { t } = useI18n();
  return (
    <li className="lp-glass lp-plan lp-plan--free" data-testid="landing-plan-free">
      <h3>{t("lp.pricing.free.name")}</h3>
      <p className="lp-plan__price">
        <span>{t("lp.pricing.free.price")}</span>
      </p>
      <ul className="plan-facts">
        {(["lp.pricing.free.f1", "lp.pricing.free.f2", "lp.pricing.free.f3"] as MessageKey[]).map((k) => (
          <li key={k}>{t(k)}</li>
        ))}
      </ul>
      <LinkButton to="/signup" variant="secondary">
        {t("lp.cta.start")}
      </LinkButton>
    </li>
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
      <ul className="plan-facts">
        {(["lp.pricing.paid.f1", "lp.pricing.paid.f2", "lp.pricing.paid.f3"] as MessageKey[]).map((k) => (
          <li key={k}>{t(k)}</li>
        ))}
      </ul>
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
          <FreeCard />
          {list.map((p) => (
            <PlanCard key={p.plan_id} plan={p} featured={p.plan_id === featuredId} />
          ))}
        </ul>
      )}
      <p className="lp-pay">
        <CreditCard size={16} aria-hidden /> {t("lp.pricing.pay")}
      </p>
      <p className="lp-fineprint">{t("lp.pricing.meta")}</p>
    </Section>
  );
}

function Faq() {
  const { t } = useI18n();
  return (
    <Section id="faq" eyebrow={t("lp.faq.eyebrow")} title={t("lp.faq.title")} className="lp-faq lp-reveal">
      <div className="lp-faq__list">
        {[1, 2, 3].map((n) => (
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
    <section className="lp-wrap lp-final lp-reveal" aria-labelledby="lp-final-title">
      <div className="lp-glass lp-final__card">
        <div className="lp-final__copy">
          <h2 id="lp-final-title" className="lp-h2">
            {t("lp.final.title")}
          </h2>
          <p className="lp-lead">{t("lp.final.text")}</p>
          <LinkButton to="/signup" variant="primary" size="lg" trailing={<ArrowRight size={16} className="mirror" aria-hidden />}>
            {t("lp.cta.start")}
          </LinkButton>
          <p className="lp-note">
            {t("lp.trust.card")} · {t("lp.trust.credits")}
          </p>
        </div>
        <div className="lp-morning" role="img" aria-label={`${t("lp.final.rep.title")}: ${t("lp.demo.badge")}`}>
          <div className="lp-sky" aria-hidden>
            <i className="lp-sky__star lp-sky__star--1" />
            <i className="lp-sky__star lp-sky__star--2" />
            <i className="lp-sky__star lp-sky__star--3" />
            <span className="lp-sky__moon">
              <MoonStar size={22} />
            </span>
            <span className="lp-sky__sun" />
          </div>
          <div className="lp-morning__body" aria-hidden>
            <b className="lp-morning__title">{t("lp.final.rep.title")}</b>
            <ul className="lp-morning__tiles">
              {(
                [
                  { Icon: MessagesSquare, n: 12, key: "lp.final.rep.conv" },
                  { Icon: ShoppingBag, n: 3, key: "lp.final.rep.sales" },
                  { Icon: RefreshCw, n: 5, key: "lp.final.rep.follow" },
                ] as Array<{ Icon: ComponentType<{ size?: number }>; n: number; key: MessageKey }>
              ).map(({ Icon, n, key }) => (
                <li key={key}>
                  <Icon size={16} />
                  <span className="lp-count" dir="ltr" style={{ ["--to" as string]: n }} />
                  <small>{t(key)}</small>
                </li>
              ))}
            </ul>
            <span className="lp-morning__sent">
              <Check size={14} /> {t("lp.final.rep.sent")}
            </span>
          </div>
          <p className="lp-demo-badge lp-demo-badge--left">{t("lp.demo.badge")}</p>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  const { t } = useI18n();
  // The pages are the administrator's: a link exists only for a page that was written.
  const loadKeys = useCallback(() => publicPageKeys(), []);
  const written = new Set(useFetched(loadKeys).value ?? []);
  const external: Array<[MessageKey, string]> = [
    ["lp.footer.contact", "contact"],
    ["lp.footer.privacy", "privacy"],
    ["lp.footer.terms", "terms"],
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
            {external.map(([key, page]) => (
              <li key={key}>
                {written.has(page) ? (
                  <Link className="lp-nav__link" to={`/page/${page}`}>
                    {t(key)}
                  </Link>
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
  const root = useRef<HTMLDivElement>(null);
  useReveal(root);
  return (
    <div className="lp" ref={root}>
      <div className="lp-aurora" aria-hidden />
      <button type="button" className="lp-skip" onClick={() => goTo("lp-main")}>
        {t("lp.skip")}
      </button>
      <Header />
      <main id="lp-main" tabIndex={-1}>
        <Hero />
        <Showcase />
        <Values />
        <Pipeline />
        <How />
        <Control />
        <Pricing />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}
