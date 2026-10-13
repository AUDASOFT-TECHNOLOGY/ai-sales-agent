import { Suspense, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import { ChevronsLeft, ChevronsRight, History, LogOut, MessageSquare, Rocket, Search, SlidersHorizontal, Send, UserRound, Users, Radar } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { TENANT } from "../mock/data";
import { useStore } from "../mock/store";
import { Pulse, useAgentLabel } from "../agent/Pulse";
import { Button, IconButton } from "../ui/Button";
import { Segmented } from "../ui/Controls";
import { PopoverCard, Tip } from "../ui/Overlays";
import { Wordmark, BrandMark } from "../ui/Brand";
import { ToastViewport } from "../ui/Toasts";
import { PrototypeControls } from "../prototype/PrototypeControls";
import { CommandPalette } from "./CommandPalette";
import { NotificationsBell } from "./Notifications";
import { PublishBar } from "./PublishBar";
import { ScreenFallback } from "./ScreenFallback";
import { cx } from "../ui/cx";

const NAV: Array<{ to: string; key: MessageKey; icon: typeof Rocket; group?: "run" | "shape" }> = [
  { to: "/dashboard", key: "nav.command", icon: Radar, group: "run" },
  { to: "/prospects", key: "nav.prospects", icon: Users, group: "run" },
  { to: "/conversations", key: "nav.conversations", icon: MessageSquare, group: "run" },
  { to: "/history", key: "nav.history", icon: History, group: "run" },
  { to: "/configure", key: "nav.configure", icon: SlidersHorizontal, group: "shape" },
  { to: "/channel", key: "nav.channel", icon: Send, group: "shape" },
];

const TITLE: Record<string, MessageKey> = {
  "/setup": "nav.setup",
  "/dashboard": "nav.command",
  "/prospects": "prospects.title",
  "/conversations": "convo.title",
  "/history": "nav.history",
  "/configure": "configure.title",
  "/notifications": "notifications.title",
  "/profile": "profile.title",
  "/channel": "channel.title",
};

function Aurora() {
  const { pathname } = useLocation();
  return (
    <div className="aurora" aria-hidden data-field={pathname.startsWith("/dashboard") ? "on" : "off"}>
      <div className="aurora__wash" />
      <div className="aurora__horizon" />
      <div className="aurora__field" />
      <div className="aurora__grain" />
      <div className="aurora__vignette" />
    </div>
  );
}

function Rail({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { t } = useI18n();
  const { state, agentState } = useStore();
  const label = useAgentLabel(agentState, state.needs.length);
  const setupDone = state.live;
  const item = (to: string, key: MessageKey, Icon: typeof Rocket, badge?: number) => (
    <li key={to}>
      <Tip label={collapsed ? t(key) : ""}>
        <span className="rail__wrap">
          <NavLink to={to} className={({ isActive }) => cx("rail__item", isActive && "is-active")} aria-label={collapsed ? t(key) : undefined}>
            <Icon size={22} aria-hidden />
            <span className="rail__label">{t(key)}</span>
            {badge ? <span className="rail__badge tabular">{badge}</span> : null}
          </NavLink>
        </span>
      </Tip>
    </li>
  );
  return (
    <aside className="rail glass" data-collapsed={collapsed || undefined} aria-label={t("nav.primary")}>
      <div className="rail__brand">
        {collapsed ? <BrandMark size={34} /> : <Wordmark />}
      </div>
      <NavLink to="/dashboard" className="rail__agent" aria-label={`${TENANT.name}: ${label}`}>
        <Pulse state={agentState} size={collapsed ? 40 : 56} count={state.live ? state.needs.length : 0} />
        <span className="rail__agent-text">
          <span className="t-label">{TENANT.name}</span>
          <span className="c-3 t-body-sm">{label}</span>
        </span>
      </NavLink>
      <nav className="rail__nav" aria-label={t("nav.primary")}>
        {!setupDone ? <ul>{item("/setup", "nav.setup", Rocket)}</ul> : null}
        <ul>{NAV.filter((n) => n.group === "run").map((n) => item(n.to, n.key, n.icon, n.to === "/dashboard" && state.live ? state.needs.length : undefined))}</ul>
        <hr className="divider rail__sep" />
        <ul>{NAV.filter((n) => n.group === "shape").map((n) => item(n.to, n.key, n.icon))}</ul>
      </nav>
      <div className="rail__foot">
        {!collapsed ? (
          <span className="sample-chip" title={t("notice.sample.long")}>
            {t("notice.sample")}
          </span>
        ) : null}
        <IconButton label={collapsed ? t("nav.expand") : t("nav.collapse")} onClick={onToggle}>
          {collapsed ? <ChevronsRight size={20} className="mirror" /> : <ChevronsLeft size={20} className="mirror" />}
        </IconButton>
      </div>
    </aside>
  );
}

function ContextBar({ onPalette }: { onPalette: () => void }) {
  const { t, locale, setLocale } = useI18n();
  const { pathname } = useLocation();
  const { state, agentState } = useStore();
  const { profile, logout } = useClientAuth();
  const label = useAgentLabel(agentState, state.needs.length);
  const initials = (profile?.name ?? "").trim().slice(0, 2).toUpperCase();
  const title = TITLE[pathname] ?? TITLE["/" + pathname.split("/")[1]!] ?? "command.title";
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className="bar" data-scrolled={scrolled || undefined}>
      <div className="bar__mobile-brand">
        <BrandMark size={28} />
      </div>
      <h1 className="sr-only">{t(title)}</h1>
      <span className="sr-only" aria-live="polite">
        {label}
      </span>
      <span className="spacer" />
      <button type="button" className="bar__search" onClick={onPalette} aria-label={t("bar.search")}>
        <Search size={18} aria-hidden />
        <span className="bar__search-text">{t("bar.search")}</span>
        <kbd className="kbd">⌘K</kbd>
      </button>
      <PrototypeControls />
      <NotificationsBell />
      <PopoverCard
        label={t("bar.menu")}
        trigger={
          <button type="button" className="user-btn" aria-label={t("bar.menu")}>
            <span className="avatar-sm">{initials}</span>
          </button>
        }
      >
        <div className="stack">
          <div className="row">
            <span className="avatar-sm">{initials}</span>
            <div>
              <p className="t-label">{profile?.name}</p>
              <p className="c-3 t-body-sm">{profile?.login}</p>
            </div>
          </div>
          <hr className="divider" />
          <Link to="/profile" className="btn" data-variant="ghost" data-size="md">
            <UserRound size={16} aria-hidden />
            <span className="btn__label">{t("profile.menu")}</span>
          </Link>
          <Button variant="ghost" leading={<LogOut size={16} aria-hidden />} onClick={() => void logout()}>
            {t("auth.logout")}
          </Button>
          <hr className="divider" />
          <p className="t-label c-2">{t("bar.language")}</p>
          <Segmented
            layoutId="seg-locale"
            label={t("bar.language")}
            value={locale}
            onChange={setLocale}
            options={[
              { value: "en", label: "English" },
              { value: "ar", label: "العربية" },
            ]}
          />
        </div>
      </PopoverCard>
    </header>
  );
}

function TabBar() {
  const { t } = useI18n();
  const { state } = useStore();
  const tabs: Array<[string, MessageKey, typeof Rocket]> = [
    ["/dashboard", "nav.command", Radar],
    ["/prospects", "nav.prospects", Users],
    ["/conversations", "nav.chats", MessageSquare],
    ["/history", "nav.history", History],
    ["/configure", "nav.configure", SlidersHorizontal],
    ["/channel", "nav.channel", Send],
    ...(state.live ? [] : ([["/setup", "nav.setup", Rocket]] as Array<[string, MessageKey, typeof Rocket]>)),
  ];
  return (
    <nav className="tabbar glass" data-level="2" aria-label={t("nav.primary")} style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }}>
      {tabs.map(([to, key, Icon]) => (
        <NavLink key={to} to={to} className={({ isActive }) => cx("tabbar__item", isActive && "is-active")}>
          <span className="tabbar__icon">
            <Icon size={22} aria-hidden />
            {to === "/dashboard" && state.live && state.needs.length > 0 ? <span className="tabbar__dot tabular">{state.needs.length}</span> : null}
          </span>
          <span className="tabbar__label">{t(key)}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem("revansa.rail") === "collapsed";
    } catch {
      return false;
    }
  });
  const [palette, setPalette] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pathname]);

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        window.localStorage.setItem("revansa.rail", next ? "collapsed" : "expanded");
      } catch {
        /* ignore */
      }
      return next;
    });

  return (
    <div className="shell" data-rail={collapsed ? "collapsed" : "expanded"}>
      <a href="#main" className="skip-link">
        {t("nav.skip")}
      </a>
      <Aurora />
      <Rail collapsed={collapsed} onToggle={toggle} />
      <div className="shell__main">
        <ContextBar onPalette={() => setPalette(true)} />
        <motion.main key={pathname} id="main" tabIndex={-1} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}>
          <Suspense fallback={<ScreenFallback />}>
            <Outlet />
          </Suspense>
        </motion.main>
      </div>
      <TabBar />
      <PublishBar />
      <ToastViewport />
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </div>
  );
}
