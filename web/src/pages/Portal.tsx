import { ArrowLeft, CalendarCheck2, ExternalLink, Phone, Pill, Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { Brand } from "../components/Brand";
import { Markdown } from "../components/Markdown";
import { ThemeToggle } from "../components/ThemeToggle";
import { Chip, ErrorNote } from "../components/ui";
import { api, session } from "../lib/api";
import { dateTime, greeting } from "../lib/format";
import { useAsync } from "../lib/useAsync";

type Rx = { prescription_id: number; name: string; refills: number; controlled: boolean; lab: string | null };
type Me = {
  patient: { full_name: string; first_name: string; primary_doctor: string | null };
  prescriptions: Rx[];
  appointments: { appointment_id: number; specialty: string; doctor_name: string; start_time: string; status: string }[];
  disclaimer: string;
  clinic: { name: string; phone: string };
};
type Card =
  | { type: "appointment_request"; specialty: string; status: string }
  | { type: "prescriptions"; items: { prescription_id: number; name: string; refills: number; controlled: boolean; lab: string | null }[] }
  | { type: "sources"; items: { title: string; url: string; source: string }[] }
  | { type: "record_summary"; patient: string; groups: Record<string, string[]> };
type Safety = { level: "emergency" | "urgent"; title: string; guidance: string; contacts: { label: string; href: string }[] };
type Reply = { reply: string; cards: Card[]; actions: { label: string; intent: string }[]; safety: Safety | null; engine: string };
type Msg =
  | { role: "patient"; text: string }
  | { role: "assistant"; reply: Reply }
  | { role: "result"; lane: "done" | "review" | "urgent"; text: string };

const INTENT_TEXT: Record<string, string> = {
  book: "I'd like to request an appointment.",
  records: "Can you go over my medical record?",
  meds: "What medications am I on?",
};

export function Portal() {
  const navigate = useNavigate();
  const patientSession = session.patient();
  const staff = session.staff();
  const me = useAsync(() => api.get<Me>("/api/me", "patient"));
  const [thread, setThread] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refilling, setRefilling] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (!patientSession) navigate("/", { replace: true }); }, [patientSession, navigate]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [thread, busy]);

  const ask = async (message: string, e?: FormEvent) => {
    e?.preventDefault();
    if (!message.trim() || busy) return;
    setThread((t) => [...t, { role: "patient", text: message }]);
    setText("");
    setBusy(true);
    setError(null);
    try {
      const reply = await api.post<Reply>("/api/me/assistant", { message }, "patient");
      setThread((t) => [...t, { role: "assistant", reply }]);
      if (reply.cards.some((c) => c.type === "appointment_request")) void me.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Your message didn't send.");
    } finally {
      setBusy(false);
    }
  };

  const refill = async (rx: { prescription_id: number; name: string }) => {
    setRefilling(rx.prescription_id);
    try {
      const r = await api.post<{ status: string; message: string }>("/api/me/refills", { prescription_id: rx.prescription_id }, "patient");
      const lane = r.status === "processed" ? "done" : r.status === "routed_to_review" ? "review" : "urgent";
      setThread((t) => [...t, { role: "result", lane, text: `${rx.name}: ${r.message}` }]);
      void me.reload();
    } catch (err) {
      setThread((t) => [...t, { role: "result", lane: "urgent", text: err instanceof Error ? err.message : "The refill request didn't go through." }]);
    } finally {
      setRefilling(null);
    }
  };

  const doAction = (intent: string) => {
    if (intent === "refill") {
      const items = me.data?.prescriptions ?? [];
      setThread((t) => [...t, { role: "assistant", reply: { reply: "Which one would you like refilled?", cards: [{ type: "prescriptions", items }], actions: [], safety: null, engine: "concierge" } }]);
    } else if (INTENT_TEXT[intent]) {
      void ask(INTENT_TEXT[intent]);
    }
  };

  if (!patientSession) return null;
  const m = me.data;
  const next = m?.appointments.filter((a) => a.status === "confirmed" && new Date(a.start_time).getTime() > Date.now())
    .sort((a, b) => a.start_time.localeCompare(b.start_time))[0];
  const pending = m?.appointments.filter((a) => a.status === "requested").length ?? 0;

  return (
    <div className="flex min-h-svh flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-[720px] items-center justify-between gap-2 px-4 py-3">
          {staff ? (
            <Link to="/console/patients" className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink-soft hover:text-ink" onClick={() => session.setPatient(null)}>
              <ArrowLeft size={16} /> Console
            </Link>
          ) : <Brand />}
          <div className="flex items-center gap-1">
            <a href="tel:911" className="chip bg-urgent-soft py-1.5 text-urgent"><Phone size={14} /> Emergency? Call 911</a>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[720px] flex-1 px-4 pt-5 pb-40">
        {me.error && <ErrorNote message={me.error} onRetry={me.reload} />}
        <section className="hero p-5 sm:p-6">
          <p className="text-hero-soft">{m?.clinic.name ?? "Your clinic"}</p>
          <h1 className="mt-1 font-display text-[34px] leading-tight font-medium">{greeting()}{m ? `, ${m.patient.first_name}` : ""}.</h1>
          <div className="mt-4 grid grid-cols-2 gap-4 text-[15px]">
            <div className="lane lane-schedule">
              <p className="font-semibold">{next ? dateTime(next.start_time) : pending ? `${pending} visit request${pending === 1 ? "" : "s"} sent` : "No visit booked"}</p>
              <p className="text-sm text-hero-soft">{next ? `${next.specialty} with ${next.doctor_name}` : pending ? "The front desk will confirm a time" : "Ask below to request one"}</p>
            </div>
            <div className="lane lane-done">
              <p className="font-semibold">{m ? `${m.prescriptions.length} medication${m.prescriptions.length === 1 ? "" : "s"}` : "…"}</p>
              <p className="text-sm text-hero-soft">{m?.patient.primary_doctor ?? ""}</p>
            </div>
          </div>
        </section>

        <div className="mt-6 space-y-4" aria-live="polite">
          {thread.length === 0 && (
            <div className="space-y-3">
              <p className="text-ink-soft">Ask about a visit, your record, refills or a health condition. You'll get an answer right away, any time of day.</p>
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-quiet" onClick={() => doAction("book")}><CalendarCheck2 size={17} /> Request a visit</button>
                <button className="btn btn-quiet" onClick={() => doAction("refill")}><Pill size={17} /> Request a refill</button>
                <button className="btn btn-quiet" onClick={() => doAction("records")}>Go over my record</button>
              </div>
            </div>
          )}

          {thread.map((msg, i) => {
            if (msg.role === "patient") {
              return <div key={i} className="settle ml-auto max-w-[85%] rounded-[18px] rounded-br-md bg-primary px-4 py-2.5 text-on-primary">{msg.text}</div>;
            }
            if (msg.role === "result") {
              return <div key={i} className={`settle lane lane-${msg.lane} font-semibold`}>{msg.text}</div>;
            }
            const r = msg.reply;
            if (r.safety?.level === "emergency") {
              return (
                <div key={i} role="alert" className="settle rounded-panel bg-urgent-soft p-5 text-urgent">
                  <p className="font-display text-[24px] leading-tight">{r.safety.title}.</p>
                  <p className="mt-2 text-ink">{r.safety.guidance}</p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {r.safety.contacts.map((c) => (
                      <a key={c.href} href={c.href} className="btn bg-urgent text-white"><Phone size={17} /> {c.label}</a>
                    ))}
                  </div>
                </div>
              );
            }
            return (
              <div key={i} className="settle max-w-[92%] space-y-3">
                <div className="rounded-[18px] rounded-bl-md bg-surface px-4 py-3 ring-1 ring-line">
                  <Markdown text={r.reply} />
                </div>
                {r.cards.map((c, j) => <ReplyCard key={j} card={c} onRefill={refill} refilling={refilling} />)}
                {r.actions.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {r.actions.filter((a) => a.intent !== "select_patient" && !(a.intent === "refill" && r.cards.some((c) => c.type === "prescriptions"))).map((a) => (
                      <button key={a.intent} className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-sm font-semibold hover:bg-sunken" onClick={() => doAction(a.intent)}>{a.label}</button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {busy && <div className="flex gap-1.5 px-2 py-3" aria-label="Tendwell is replying">{[0, 1, 2].map((d) => <span key={d} className="size-2 animate-bounce rounded-full bg-ink-soft" style={{ animationDelay: `${d * 120}ms` }} />)}</div>}
          {error && <ErrorNote message={error} />}
          <div ref={endRef} className="scroll-mb-44" />
        </div>
      </main>

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-bg/95 pb-[env(safe-area-inset-bottom)] backdrop-blur">
        <form onSubmit={(e) => ask(text, e)} className="mx-auto flex max-w-[720px] gap-2 px-4 pt-3">
          <label className="sr-only" htmlFor="ask">Message Tendwell</label>
          <input id="ask" className="field rounded-full px-5 shadow-[0_6px_24px_-12px_rgb(15_42_34/0.35)]" placeholder="Ask about a visit, refill or condition" value={text} onChange={(e) => setText(e.target.value)} autoComplete="off" maxLength={2000} />
          <button className="btn btn-primary shrink-0 px-4" disabled={busy || !text.trim()} aria-label="Send"><Send size={18} /></button>
        </form>
        <p className="mx-auto max-w-[720px] px-5 py-2 text-center text-[13px] text-ink-soft">{m?.disclaimer}</p>
      </div>
    </div>
  );
}

function ReplyCard({ card, onRefill, refilling }: { card: Card; onRefill: (rx: { prescription_id: number; name: string }) => void; refilling: number | null }) {
  if (card.type === "appointment_request") {
    return (
      <div className="lane lane-schedule rounded-row bg-schedule-soft py-3 pr-4 text-schedule">
        <p className="pl-1 font-semibold">{card.specialty} visit requested</p>
        <p className="pl-1 text-sm">The front desk will confirm a time. It'll show at the top of this page.</p>
      </div>
    );
  }
  if (card.type === "prescriptions") {
    return (
      <ul className="panel divide-y divide-line">
        {card.items.map((rx) => (
          <li key={rx.prescription_id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="font-semibold">{rx.name}</p>
              <p className="text-sm text-ink-soft">
                {rx.refills > 0 ? `${rx.refills} refill${rx.refills === 1 ? "" : "s"} left` : "No refills left, your provider will review"}
                {rx.controlled && ", reviewed by your provider"}
              </p>
            </div>
            <button className="btn btn-quiet min-h-9 shrink-0 px-4 text-sm" disabled={refilling !== null} onClick={() => onRefill({ prescription_id: rx.prescription_id, name: rx.name })}>
              {refilling === rx.prescription_id ? "Requesting…" : "Request a refill"}
            </button>
          </li>
        ))}
      </ul>
    );
  }
  if (card.type === "sources") {
    return (
      <div className="flex flex-wrap gap-2">
        {card.items.map((s) => (
          <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="chip bg-ceil py-1.5 text-ceil-ink hover:brightness-95">
            <ExternalLink size={14} /> {s.title}
          </a>
        ))}
      </div>
    );
  }
  return <Chip lane="neutral">From your record</Chip>;
}
