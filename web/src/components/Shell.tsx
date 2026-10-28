import { CalendarClock, ChartLine, House, Inbox, LogOut, Menu, Pill, Settings, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { api, session } from "../lib/api";
import { Brand } from "./Brand";
import { ThemeToggle } from "./ThemeToggle";

type Counts = { reviews: number; appointment_requests: number; open_tickets: number };

const NAV: { to: string; label: string; icon: ReactNode; count?: keyof Counts; mobile?: boolean }[] = [
  { to: "/console", label: "Today", icon: <House size={19} />, mobile: true },
  { to: "/console/patients", label: "Patients", icon: <Users size={19} />, mobile: true },
  { to: "/console/schedule", label: "Schedule", icon: <CalendarClock size={19} />, count: "appointment_requests" },
  { to: "/console/refills", label: "Refills", icon: <Pill size={19} />, count: "reviews", mobile: true },
  { to: "/console/inbox", label: "Inbox", icon: <Inbox size={19} />, count: "open_tickets", mobile: true },
  { to: "/console/insights", label: "Insights", icon: <ChartLine size={19} /> },
  { to: "/console/settings", label: "Settings", icon: <Settings size={19} /> },
];

/** Tell the shell to refresh its counts after a mutation anywhere. */
export const refreshCounts = () => window.dispatchEvent(new Event("tw:refresh"));

export function Shell() {
  const navigate = useNavigate();
  const staff = session.staff();
  const [counts, setCounts] = useState<Counts | null>(null);

  useEffect(() => {
    if (!staff) {
      navigate("/sign-in", { replace: true });
      return;
    }
    const load = () => api.get<{ counts: Counts }>("/api/staff/overview", "staff").then((o) => setCounts(o.counts)).catch(() => {});
    const out = () => navigate("/sign-in", { replace: true });
    void load();
    window.addEventListener("tw:refresh", load);
    window.addEventListener("tw:signed-out", out);
    return () => {
      window.removeEventListener("tw:refresh", load);
      window.removeEventListener("tw:signed-out", out);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!staff) return null;

  const signOut = () => {
    session.setStaff(null);
    navigate("/sign-in");
  };

  const badge = (key?: keyof Counts) => {
    const n = key && counts ? counts[key] : 0;
    return n ? (
      <span className="ml-auto min-w-6 rounded-full bg-ceil px-1.5 text-center text-[12px] leading-6 font-bold text-ceil-ink">{n}</span>
    ) : null;
  };

  return (
    <div className="min-h-svh lg:grid lg:grid-cols-[248px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-full focus:bg-surface focus:px-4 focus:py-2">
        Skip to content
      </a>

      {/* Desktop rail */}
      <aside className="sticky top-0 hidden h-svh flex-col bg-hero px-4 pt-6 pb-4 text-on-hero lg:flex">
        <div className="px-2"><Brand inverted /></div>
        <nav className="mt-8 flex flex-col gap-0.5" aria-label="Console">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/console"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-row px-3 py-2.5 font-semibold transition-colors ${
                  isActive ? "bg-white/12 text-on-hero" : "text-hero-soft hover:bg-white/6 hover:text-on-hero"
                }`
              }
            >
              {item.icon}
              {item.label}
              {badge(item.count)}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex items-center gap-2 border-t border-white/10 px-2 pt-4">
          <div className="min-w-0 flex-1">
            <div className="truncate font-semibold">{staff.name}</div>
            <div className="text-sm text-hero-soft">Signed in</div>
          </div>
          <ThemeToggle inverted />
          <button onClick={signOut} className="grid size-10 place-items-center rounded-full text-hero-soft hover:bg-white/10 hover:text-on-hero" aria-label="Sign out" title="Sign out">
            <LogOut size={18} />
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-bg/90 px-4 py-3 backdrop-blur lg:hidden">
        <Brand />
        <ThemeToggle />
      </header>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1120px] px-4 pt-6 pb-28 sm:px-8 lg:pt-10 lg:pb-16">
        <Outlet />
      </main>

      {/* Mobile tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-white/10 bg-hero pb-[env(safe-area-inset-bottom)] lg:hidden" aria-label="Console">
        {NAV.filter((n) => n.mobile).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/console"}
            className={({ isActive }) => `relative flex flex-col items-center gap-0.5 py-2.5 text-[12px] font-semibold ${isActive ? "text-on-hero" : "text-hero-soft"}`}
          >
            {({ isActive }) => (
              <>
                <span className={`grid h-7 w-12 place-items-center rounded-full ${isActive ? "bg-white/14" : ""}`}>{item.icon}</span>
                {item.label}
                {item.count && counts?.[item.count] ? (
                  <span className="absolute top-1.5 right-[calc(50%-22px)] size-2.5 rounded-full bg-ceil ring-2 ring-hero" aria-label={`${counts[item.count]} waiting`} />
                ) : null}
              </>
            )}
          </NavLink>
        ))}
        <NavLink to="/console/more" className={({ isActive }) => `flex flex-col items-center gap-0.5 py-2.5 text-[12px] font-semibold ${isActive ? "text-on-hero" : "text-hero-soft"}`}>
          <span className="grid h-7 w-12 place-items-center rounded-full"><Menu size={19} /></span>
          More
        </NavLink>
      </nav>
    </div>
  );
}

export function MoreMenu() {
  const navigate = useNavigate();
  return (
    <div>
      <h1 className="mb-4 font-display text-[34px] font-medium">More</h1>
      <div className="panel divide-y divide-line">
        {NAV.filter((n) => !n.mobile).map((item) => (
          <NavLink key={item.to} to={item.to} className="flex items-center gap-3 px-4 py-4 font-semibold hover:bg-sunken">
            <span className="text-primary">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
        <button
          className="flex w-full items-center gap-3 px-4 py-4 text-left font-semibold text-urgent hover:bg-sunken"
          onClick={() => { session.setStaff(null); navigate("/sign-in"); }}
        >
          <LogOut size={19} /> Sign out
        </button>
      </div>
    </div>
  );
}
