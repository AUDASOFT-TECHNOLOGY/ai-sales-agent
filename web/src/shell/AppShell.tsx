/** The Client space frame: a plain menu built around what a business owner does (prepare the offer,
 *  connect prospects and channels, review, follow results), not around the platform's internals.
 *  Nothing here is a prototype control: the agent's state is read from the server. */

import { Suspense, useEffect, useState } from "react";
import { useOverview, OverviewProvider, guideSteps } from "../client/overview";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Bell, ChartNoAxesColumn, CheckSquare, Ellipsis, House, LogOut, MessageSquare, Package, Receipt, Rocket, Route, UserRound } from "lucide-react";
import { useI18n, type MessageKey } from "../i18n";
import { useClientAuth } from "../client/ClientAuthContext";
import { googleReturnOf } from "../client/ClientGmailCard";
import { useClientAgentStatus } from "../client/useClientAgentStatus";
import { useNotificationCount } from "../client/useNotificationCount";
import { ClientNotificationsPanel } from "../client/notifications/ClientNotificationsPanel";
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

/** Four groups, in the order an owner works: set up, prepare, follow, look after the account.
 *  On a phone the first four entries stay in the tab bar and the rest sit under "More". */
const HOME: NavEntry = { to: "/home", key: "rv.nav.home", icon: House };
const SETUP: NavEntry = { to: "/setup", key: "rv.nav.setup", icon: Rocket };
const PREPARE: NavEntry[] = [
  { to: "/offer", key: "rv.nav.offer", icon: Package },
  { to: "/circuits", key: "rv.nav.circuits", icon: Route },
];
const FOLLOW: NavEntry[] = [
  { to: "/conversations", key: "rv.nav.conversations", icon: MessageSquare, also: ["/prospects"] },
  { to: "/review", key: "rv.nav.review", icon: CheckSquare },
  { to: "/results", key: "rv.nav.results", icon: ChartNoAxesColumn, also: ["/reports", "/dashboard"] },
];
const ACCOUNT: NavEntry[] = [{ to: "/account", key: "rv.nav.billing", icon: Receipt, also: ["/billing", "/profile"] }];
const TABS = [HOME, FOLLOW[1]!, FOLLOW[0]!, PREPARE[0]!];
const MORE = [PREPARE[1]!, FOLLOW[2]!, ...ACCOUNT];

function useActive(entry: NavEntry): boolean {
  const { pathname } = useLocation();
  return [entry.to, ...(entry.also ?? [])].some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

function SideItem({ entry, badge, note }: { entry: NavEntry; badge: number; note?: string }) {
  const { t } = useI18n();
  const active = useActive(entry);
  const Icon = entry.icon;
  return (
    <li>
      <NavLink to={entry.to} className={cx("rv-nav__item", active && "is-active")} aria-current={active ? "page" : undefined}>
        <Icon size={20} aria-hidden />
        <span>{t(entry.key)}</span>
        {note ? <span className="rv-nav__note">{note}</span> : null}
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

/** The bell: how many notifications are unread, and the latest ones in a small menu (the full list is
 *  "See all"). It replaces a menu entry: notifications are not a place to work in, only to be told. */
function NotificationsBell({ side }: { side: "bottom" | "right" }) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState(false);
  const count = useNotificationCount(`${pathname}:${tick}`);
  const label = count > 0 ? t("rv.nav.notificationsUnread", { n: count }) : t("rv.nav.notifications");
  return (
    <PopoverCard
      label={t("rv.nav.notifications")}
      side={side}
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button type="button" className="rv-bell" aria-label={label} title={label}>
          <Bell size={20} aria-hidden />
          {count > 0 ? <span className="rv-bell__count">{count}</span> : null}
        </button>
      }
    >
      <div className="rv-bellmenu">
        <div className="rv-bellmenu__head">
          <strong>{t("rv.nav.notifications")}</strong>
          <LinkButton to="/notifications" size="sm" variant="ghost" onClick={() => setOpen(false)}>
            {t("rv.nav.notificationsAll")}
          </LinkButton>
        </div>
        <ClientNotificationsPanel compact limit={5} onChanged={() => setTick((v) => v + 1)} />
      </div>
    </PopoverCard>
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
      <LinkButton to="/account/profile" variant="ghost" leading={<UserRound size={16} aria-hidden />}>
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
  return (
    <OverviewProvider>
      <Shell />
    </OverviewProvider>
  );
}

function Shell() {
  const { t } = useI18n();
  const { data } = useOverview();
  const { pathname } = useLocation();
  const { profile } = useClientAuth();
  const agent = useClientAgentStatus();
  const review = useReviewCount();
  const navigate = useNavigate();
  const compact = useMedia("(max-width: 899px)");

  // Google sends the browser back with `?google=...`: land on the channels, where the result is shown.
  useEffect(() => {
    if (googleReturnOf(window.location.search)) navigate("/circuits", { replace: true });
  }, [navigate]);

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pathname]);

  const badgeOf = (entry: NavEntry) => (entry.to === "/review" ? review : 0);
  // The guide stays in the menu until every step is done, with how far the client got.
  const steps = data ? guideSteps(data) : [];
  const doneSteps = steps.filter((s) => s.state === "done").length;
  const guideOpen = data !== null && steps.length > 0 && doneSteps < steps.length;
  const groups: Array<{ label: MessageKey | null; entries: NavEntry[] }> = [
    { label: null, entries: guideOpen ? [HOME, SETUP] : [HOME] },
    { label: "rv.nav.group.prepare", entries: PREPARE },
    { label: "rv.nav.group.follow", entries: FOLLOW },
    { label: "rv.nav.group.account", entries: ACCOUNT },
  ];
  const progressOf = (entry: NavEntry) => (entry.to === "/setup" ? `${doneSteps}/${steps.length}` : "");

  return (
    <div className="rv-shell">
      <a href="#main" className="skip-link">
        {t("rv.nav.skip")}
      </a>

      <aside className="rv-side glass" data-level="2" aria-label={t("rv.nav.main")}>
        <div className="rv-side__brand">
          <Wordmark />
          <NotificationsBell side="right" />
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
            <div key={group.label ?? "top"} className="rv-nav__group">
              {group.label ? <p className="rv-nav__label">{t(group.label)}</p> : null}
              <ul>
                {group.entries.map((entry) => (
                  <SideItem key={entry.to} entry={entry} badge={badgeOf(entry)} note={progressOf(entry)} />
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
          {compact ? <NotificationsBell side="bottom" /> : null}
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
