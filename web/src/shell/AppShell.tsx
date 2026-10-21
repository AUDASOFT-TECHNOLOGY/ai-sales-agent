/** The Client space frame: a plain menu built around what a business owner does (prepare the offer,
 *  connect prospects and channels, review, follow results), not around the platform's internals.
 *  Nothing here is a prototype control: the agent's state is read from the server. */

import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Bell, CheckSquare, Ellipsis, FileText, House, LayoutDashboard, Link2, LogOut, MessageSquare, Package, Receipt, UserRound, Users } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { googleReturnOf } from "../client/ClientGmailCard";
import { useClientAgentStatus } from "../client/useClientAgentStatus";
import { useNotificationCount } from "../client/useNotificationCount";
import { useReviewCount } from "../client/useReviewCount";
import { StatusOrb } from "../agent/StatusOrb";
import { Button, LinkButton } from "../ui/Button";
import { Segmented } from "../ui/Controls";
import { PopoverCard } from "../ui/Overlays";
import { BrandMark, Wordmark } from "../ui/Brand";
import { ScreenFallback } from "./ScreenFallback";
import { cx } from "../ui/cx";
import { useMedia } from "../ui/useMedia";

interface NavEntry {
  to: string;
  key: MessageKey;
  icon: typeof House;
  /** Other paths that belong to this entry. */
  also?: string[];
}

/** Two groups: what happens with prospects every day, and what the owner prepares for the agent.
 *  On a phone the first four stay in the tab bar and the rest sit under "More". */
const ACTIVITY: NavEntry[] = [
  { to: "/home", key: "rv.nav.home", icon: House },
  { to: "/review", key: "rv.nav.review", icon: CheckSquare },
  { to: "/conversations", key: "rv.nav.conversations", icon: MessageSquare },
  { to: "/prospects", key: "rv.nav.prospects", icon: Users },
  { to: "/reports", key: "rv.nav.reports", icon: FileText },
  { to: "/dashboard", key: "rv.nav.dashboard", icon: LayoutDashboard },
  { to: "/billing", key: "rv.nav.billing", icon: Receipt },
  { to: "/notifications", key: "rv.nav.notifications", icon: Bell },
];
const AGENT: NavEntry[] = [
  { to: "/offer", key: "rv.nav.offer", icon: Package },
  { to: "/connections", key: "rv.nav.connections", icon: Link2 },
];
const TABS = ACTIVITY.slice(0, 4);
const MORE = [...ACTIVITY.slice(4), ...AGENT];

function useActive(entry: NavEntry): boolean {
  const { pathname } = useLocation();
  return [entry.to, ...(entry.also ?? [])].some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

function SideItem({ entry, badge }: { entry: NavEntry; badge: number }) {
  const { t } = useI18n();
  const active = useActive(entry);
  const Icon = entry.icon;
  return (
    <li>
      <NavLink to={entry.to} className={cx("rv-nav__item", active && "is-active")} aria-current={active ? "page" : undefined}>
        <Icon size={20} aria-hidden />
        <span>{t(entry.key)}</span>
        {badge > 0 ? (
          <span className="rv-badge" aria-label={String(badge)}>
            {badge}
          </span>
        ) : null}
      </NavLink>
    </li>
  );
}

function TabItem({ entry, badge }: { entry: NavEntry; badge: number }) {
  const { t } = useI18n();
  const active = useActive(entry);
  const Icon = entry.icon;
  return (
    <NavLink to={entry.to} className={cx("rv-tabbar__item", active && "is-active")} aria-current={active ? "page" : undefined}>
      <Icon size={22} aria-hidden />
      <span className="rv-tabbar__label">{t(entry.key)}</span>
      {badge > 0 ? <span className="rv-tabbar__dot">{badge}</span> : null}
    </NavLink>
  );
}

function MoreMenu() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const active = MORE.some((e) => pathname === e.to || pathname.startsWith(`${e.to}/`));
  return (
    <PopoverCard
      label={t("rv.nav.more")}
      side="top"
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button type="button" className={cx("rv-tabbar__item", active && "is-active")}>
          <Ellipsis size={22} aria-hidden />
          <span className="rv-tabbar__label">{t("rv.nav.more")}</span>
        </button>
      }
    >
      <div className="rv-more">
        {MORE.map((e) => (
          <LinkButton key={e.to} to={e.to} variant="ghost" leading={<e.icon size={18} aria-hidden />} onClick={() => setOpen(false)}>
            {t(e.key)}
          </LinkButton>
        ))}
      </div>
    </PopoverCard>
  );
}

function AccountMenu({ compact }: { compact?: boolean }) {
  const { t, locale, setLocale } = useI18n();
  const { profile, logout } = useClientAuth();
  const initials = (profile?.name ?? "").trim().slice(0, 2).toUpperCase();
  const menu = (
    <div className="stack">
      <div className="row">
        <span className="rv-avatar">{initials}</span>
        <div className="rv-user__text">
          <span className="rv-user__name">{profile?.name}</span>
          <span className="rv-user__login">{profile?.login}</span>
        </div>
      </div>
      <hr className="divider" />
      <LinkButton to="/profile" variant="ghost" leading={<UserRound size={16} aria-hidden />}>
        {t("rv.nav.account")}
      </LinkButton>
      <Button variant="ghost" leading={<LogOut size={16} aria-hidden />} onClick={() => void logout()}>
        {t("rv.nav.logout")}
      </Button>
      <hr className="divider" />
      <p className="t-label c-2">{t("rv.nav.language")}</p>
      <Segmented
        layoutId={compact ? "seg-locale-top" : "seg-locale-side"}
        label={t("rv.nav.language")}
        value={locale}
        onChange={setLocale}
        options={[
          { value: "fr", label: "Français" },
          { value: "en", label: "English" },
          { value: "ar", label: "العربية" },
        ]}
      />
    </div>
  );
  return (
    <PopoverCard
      label={t("rv.nav.menu")}
      side={compact ? "bottom" : "top"}
      trigger={
        compact ? (
          <button type="button" className="rv-avatar" aria-label={t("rv.nav.menu")}>
            {initials}
          </button>
        ) : (
          <button type="button" className="rv-user" aria-label={t("rv.nav.menu")}>
            <span className="rv-avatar">{initials}</span>
            <span className="rv-user__text">
              <span className="rv-user__name">{profile?.name}</span>
              <span className="rv-user__login">{profile?.login}</span>
            </span>
          </button>
        )
      }
    >
      {menu}
    </PopoverCard>
  );
}

export function AppShell() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const { profile } = useClientAuth();
  const agent = useClientAgentStatus();
  const review = useReviewCount();
  const notifications = useNotificationCount(pathname);
  const navigate = useNavigate();
  const compact = useMedia("(max-width: 899px)");

  // Google sends the browser back with `?google=...`: land on the channels, where the result is shown.
  useEffect(() => {
    if (googleReturnOf(window.location.search)) navigate("/connections/channels", { replace: true });
  }, [navigate]);

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pathname]);

  const badgeOf = (entry: NavEntry) => (entry.to === "/review" ? review : entry.to === "/notifications" ? notifications : 0);
  const groups: Array<{ label: MessageKey; entries: NavEntry[] }> = [
    { label: "rv.nav.group.activity", entries: ACTIVITY },
    { label: "rv.nav.group.agent", entries: AGENT },
  ];

  return (
    <div className="rv-shell">
      <a href="#main" className="skip-link">
        {t("rv.nav.skip")}
      </a>

      <aside className="rv-side glass" data-level="2" aria-label={t("rv.nav.main")}>
        <div className="rv-side__brand">
          <Wordmark />
        </div>
        <NavLink to="/home" className="rv-agent" aria-label={`${t("rv.agent.name")}: ${t(agent.statusKey)}`}>
          <StatusOrb state={agent.pulse} size={40} />
          <span className="rv-agent__text">
            <span className="rv-agent__name">{profile?.name ?? t("rv.agent.name")}</span>
            <span className="rv-agent__state" aria-live="polite">
              {t(agent.statusKey)}
            </span>
          </span>
        </NavLink>
        <nav className="rv-nav" aria-label={t("rv.nav.main")}>
          {groups.map((group) => (
            <div key={group.label} className="rv-nav__group">
              <p className="rv-nav__label">{t(group.label)}</p>
              <ul>
                {group.entries.map((entry) => (
                  <SideItem key={entry.to} entry={entry} badge={badgeOf(entry)} />
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="rv-side__foot">{compact ? null : <AccountMenu />}</div>
      </aside>

      <div className="rv-main">
        <header className="rv-topbar glass" data-level="2">
          <BrandMark size={28} />
          <span className="row" style={{ gap: 10 }}>
            <StatusOrb state={agent.pulse} size={22} />
            <span className="rv-agent__state" aria-live="polite">
              {t(agent.statusKey)}
            </span>
          </span>
          {compact ? (
            <NavLink
              to="/notifications"
              className="rv-topbar__bell"
              aria-label={notifications > 0 ? t("rv.nav.notificationsUnread", { n: notifications }) : t("rv.nav.notifications")}
            >
              <Bell size={22} aria-hidden />
              {notifications > 0 ? <span className="rv-tabbar__dot">{notifications}</span> : null}
            </NavLink>
          ) : null}
          {compact ? <AccountMenu compact /> : null}
        </header>
        <main id="main" tabIndex={-1} className="rv-content">
          <Suspense fallback={<ScreenFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </div>

      <nav className="rv-tabbar glass" data-level="2" aria-label={t("rv.nav.main")}>
        {TABS.map((entry) => (
          <TabItem key={entry.to} entry={entry} badge={badgeOf(entry)} />
        ))}
        <MoreMenu />
      </nav>
    </div>
  );
}
