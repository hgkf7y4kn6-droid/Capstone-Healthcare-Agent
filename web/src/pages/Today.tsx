import { CalendarPlus, CheckCircle2, Circle, Inbox, MessageSquareText, Pill, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Chip, EmptyState, ErrorNote, Section, Skeleton, Stat } from "../components/ui";
import { api, session } from "../lib/api";
import { greeting, relativeTime, ruleLabel } from "../lib/format";
import { laneLabel, toneToLane } from "../lib/lanes";
import { useAsync } from "../lib/useAsync";

type Overview = {
  counts: { reviews: number; appointment_requests: number; open_tickets: number; sent_to_pharmacy: number; patients: number; safety_flags: number };
  auto_approval_rate: number | null;
  attention: { kind: "review" | "appointment" | "ticket"; id: number | string; title: string; detail: string; tone: string; at: string }[];
  checklist: { calendar_connected: boolean; portal_connected: boolean; own_access_code: boolean; patients_loaded: boolean };
};
type Activity = { type: string; title: string; detail: string; tone: string; at: string }[];

const HREF = { review: "/console/refills", appointment: "/console/schedule", ticket: "/console/inbox" } as const;

export function Today() {
  const staff = session.staff();
  const overview = useAsync(() => api.get<Overview>("/api/staff/overview", "staff"));
  const activity = useAsync(() => api.get<Activity>("/api/staff/activity?limit=12", "staff"));
  const o = overview.data;
  const waiting = o ? o.counts.reviews + o.counts.appointment_requests + o.counts.open_tickets : 0;
  const first = staff?.name.split(" ")[0];

  return (
    <div className="space-y-10">
      {overview.error && <ErrorNote message={overview.error} onRetry={overview.reload} />}

      <section className="hero overflow-hidden p-6 sm:p-8" aria-label="Today">
        <p className="text-hero-soft">{greeting()}{first && first !== "Demo" ? `, ${first}` : ""}</p>
        <h1 className="mt-1 max-w-[20ch] font-display text-[36px] leading-[1.08] font-medium sm:text-[46px]">
          {!o ? "Loading today…" : waiting === 0 ? "Nothing needs a person right now." : `${waiting} ${waiting === 1 ? "thing needs" : "things need"} a person today.`}
        </h1>
        {o && (
          <>
            <p className="mt-3 max-w-[56ch] text-hero-soft">
              {o.counts.sent_to_pharmacy} refill{o.counts.sent_to_pharmacy === 1 ? "" : "s"} went straight to the pharmacy
              {o.auto_approval_rate !== null && ` — ${Math.round(o.auto_approval_rate * 100)}% cleared every rule without anyone touching them`}.
            </p>
            <div className="mt-8 grid grid-cols-2 gap-x-6 gap-y-6 sm:grid-cols-4">
              <Stat label="Refills to review" value={o.counts.reviews} lane="review" />
              <Stat label="Visit requests" value={o.counts.appointment_requests} lane="schedule" />
              <Stat label="Open tickets" value={o.counts.open_tickets} lane="urgent" />
              <Stat label="Sent to pharmacy" value={o.counts.sent_to_pharmacy} lane="done" />
            </div>
          </>
        )}
      </section>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <QuickAction to="/console/refills" icon={<Pill size={20} />} label="Review refills" />
        <QuickAction to="/console/schedule" icon={<CalendarPlus size={20} />} label="Book visit requests" />
        <QuickAction to="/console/inbox" icon={<Inbox size={20} />} label="Triage a message" />
        <QuickAction to="/console/patients" icon={<MessageSquareText size={20} />} label="Open a patient's portal" />
      </div>

      <div className="grid gap-10 lg:grid-cols-[1.35fr_1fr]">
        <Section title="Needs a person" aside={o && `${o.attention.length} shown`}>
          {overview.loading && !o ? <Skeleton rows={4} /> : o && o.attention.length === 0 ? (
            <EmptyState icon={<CheckCircle2 size={20} />} title="You're all caught up" body="Refills a rule couldn't clear, visit requests and unhappy messages will show up here." />
          ) : (
            <ul className="panel divide-y divide-line">
              {o?.attention.map((a) => {
                const lane = toneToLane(a.tone);
                return (
                  <li key={`${a.kind}-${a.id}`}>
                    <Link to={HREF[a.kind]} className={`lane lane-${lane} flex items-start justify-between gap-3 py-3.5 pr-4 hover:bg-sunken`}>
                      <div className="min-w-0 pl-1">
                        <p className="font-semibold">{a.title}</p>
                        <p className="line-clamp-1 text-sm text-ink-soft">{a.kind === "review" ? ruleLabel(a.detail) : a.detail}</p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        {lane !== "neutral" && <Chip lane={lane}>{laneLabel[lane]}</Chip>}
                        <span className="text-[13px] text-ink-soft">{relativeTime(a.at)}</span>
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Section>

        <div className="space-y-10">
          {o && <StartHere checklist={o.checklist} />}
          <Section title="What Tendwell AA did">
            {activity.loading && !activity.data ? <Skeleton rows={4} /> : activity.data?.length ? (
              <ol className="space-y-3">
                {activity.data.map((a, i) => (
                  <li key={i} className={`lane lane-${toneToLane(a.tone)} text-[15px]`}>
                    <span className="font-semibold">{a.title}</span>
                    {a.detail && <span className="text-ink-soft"> — {a.detail}</span>}
                    <span className="block text-[13px] text-ink-soft">{relativeTime(a.at)}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-ink-soft">Activity appears here as patients use the assistant.</p>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

function QuickAction({ to, icon, label }: { to: string; icon: ReactNode; label: string }) {
  return (
    <Link to={to} className="panel flex min-h-[88px] flex-col justify-between gap-3 p-4 font-semibold transition-transform hover:border-primary active:scale-[0.98]">
      <span className="text-primary">{icon}</span>
      {label}
    </Link>
  );
}

const DISMISS_KEY = "tw.start-here.dismissed";

function StartHere({ checklist }: { checklist: Overview["checklist"] }) {
  const [hidden, setHidden] = useState(() => {
    try { return localStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; }
  });
  const items = [
    { done: checklist.patients_loaded, label: "Load your patient roster", hint: "Import from your EHR export" },
    { done: checklist.own_access_code, label: "Set your own staff access code", hint: "Replace the demo code before real use" },
    { done: checklist.calendar_connected, label: "Connect Cal.com", hint: "So visit requests become bookings" },
    { done: checklist.portal_connected, label: "Connect your patient portal", hint: "Patients sign in once, no re-verifying" },
  ];
  const remaining = items.filter((i) => !i.done).length;
  if (hidden || remaining === 0) return null;
  return (
    <Section
      title="Start here"
      aside={
        <button
          className="grid size-8 place-items-center rounded-full hover:bg-sunken"
          aria-label="Hide the setup checklist"
          onClick={() => { try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ } setHidden(true); }}
        >
          <X size={16} />
        </button>
      }
    >
      <ul className="panel divide-y divide-line">
        {items.map((it) => (
          <li key={it.label} className="flex items-start gap-3 px-4 py-3">
            {it.done ? <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-done" aria-label="Done" /> : <Circle size={20} className="mt-0.5 shrink-0 text-ink-soft" aria-label="To do" />}
            <div>
              <p className={`font-semibold ${it.done ? "text-ink-soft line-through decoration-1" : ""}`}>{it.label}</p>
              {!it.done && <p className="text-sm text-ink-soft">{it.hint}</p>}
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-sm text-ink-soft">The setup guide in Settings walks through each step.</p>
    </Section>
  );
}
