import { Suspense, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import { ChevronsLeft, ChevronsRight, ClipboardCheck, History, LogOut, MessageSquare, Rocket, Search, SlidersHorizontal, Send, UserRound, Users, Radar, Workflow } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { useClientAgentStatus, type ClientAgentStatus } from "../client/useClientAgentStatus";
import { Pulse } from "../agent/Pulse";
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
  { to: "/approvals", key: "nav.approvals", icon: ClipboardCheck, group: "run" },
  { to: "/history", key: "nav.history", icon: History, group: "run" },
  { to: "/agent", key: "nav.agent", icon: Rocket, group: "shape" },
  { to: "/settings", key: "nav.settings", icon: SlidersHorizontal, group: "shape" },
  { to: "/channel", key: "nav.channel", icon: Send, group: "shape" },
  { to: "/lanes", key: "nav.lanes", icon: Workflow, group: "shape" },
];

const TITLE: Record<string, MessageKey> = {
  "/agent": "agent.title",
  "/settings": "settings.title",
  "/dashboard": "nav.command",
  "/prospects": "prospects.title",
  "/conversations": "convo.title",
  "/history": "nav.history",
  "/notifications": "notifications.title",
  "/profile": "profile.title",
  "/approvals": "approvals.title",
  "/channel": "channel.title",
  "/lanes": "lanes.title",
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

function Rail({ collapsed, onToggle, agent }: { collapsed: boolean; onToggle: () => void; agent: ClientAgentStatus }) {
  const { t } = useI18n();
  const { profile } = useClientAuth();
  const label = t(agent.label);
  const name = profile?.name ?? "";
  const item = (to: string, key: MessageKey, Icon: typeof Rocket) => (
    <li key={to}>
      <Tip label={collapsed ? t(key) : ""}>
        <span className="rail__wrap">
          <NavLink to={to} className={({ isActive }) => cx("rail__item", isActive && "is-active")} aria-label={collapsed ? t(key) : undefined}>
            <Icon size={22} aria-hidden />
            <span className="rail__label">{t(key)}</span>
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
      <NavLink to="/dashboard" className="rail__agent" aria-label={`${name}: ${label}`}>
        <Pulse state={agent.pulse} size={collapsed ? 40 : 56} />
        <span className="rail__agent-text">
          <span className="t-label">{name}</span>
          <span className="c-3 t-body-sm">{label}</span>
        </span>
      </NavLink>
      <nav className="rail__nav" aria-label={t("nav.primary")}>
        <ul>{NAV.filter((n) => n.group === "run").map((n) => item(n.to, n.key, n.icon))}</ul>
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

function ContextBar({ onPalette, agent }: { onPalette: () => void; agent: ClientAgentStatus }) {
  const { t, locale, setLocale } = useI18n();
  const { pathname } = useLocation();
  const { profile, logout } = useClientAuth();
  const label = t(agent.label);
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
          <Link to="/lanes" className="btn" data-variant="ghost" data-size="md">
            <Workflow size={16} aria-hidden />
            <span className="btn__label">{t("nav.lanes")}</span>
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
  const tabs: Array<[string, MessageKey, typeof Rocket]> = [
    ["/dashboard", "nav.command", Radar],
    ["/prospects", "nav.prospects", Users],
    ["/conversations", "nav.chats", MessageSquare],
    ["/history", "nav.history", History],
    ["/agent", "nav.agent", Rocket],
    ["/settings", "nav.settings", SlidersHorizontal],
    ["/channel", "nav.channel", Send],
  ];
  return (
    <nav className="tabbar glass" data-level="2" aria-label={t("nav.primary")} style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }}>
      {tabs.map(([to, key, Icon]) => (
        <NavLink key={to} to={to} className={({ isActive }) => cx("tabbar__item", isActive && "is-active")}>
          <span className="tabbar__icon">
            <Icon size={22} aria-hidden />
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
  const agent = useClientAgentStatus();

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
      <Rail collapsed={collapsed} onToggle={toggle} agent={agent} />
      <div className="shell__main">
        <ContextBar onPalette={() => setPalette(true)} agent={agent} />
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
