import { Inbox as InboxIcon, Send } from "lucide-react";
import { useState, type FormEvent } from "react";
import { refreshCounts } from "../components/Shell";
import { Chip, EmptyState, ErrorNote, PageHeader, Section, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { Lane } from "../lib/lanes";
import { useAsync } from "../lib/useAsync";

type Ticket = { ticket_number: string; patient_name: string | null; complaint_text: string; status: "Unresolved" | "In Progress" | "Resolved"; created_at: string; updated_at: string };
type TriageResult = { category: string; classification_method: string; message: string; ticket_number?: string; stage?: string; chained_refill_agent?: { ticket_number: string; message: string; stage: string } };

const STATUS_LANE: Record<Ticket["status"], Lane | "neutral"> = { Unresolved: "urgent", "In Progress": "review", Resolved: "done" };
const CATEGORY: Record<string, { label: string; lane: Lane | "neutral" }> = {
  positive_feedback: { label: "Thank-you", lane: "done" },
  negative_feedback: { label: "Complaint — ticket opened", lane: "urgent" },
  query: { label: "Status question", lane: "neutral" },
  prescription_refill: { label: "Refill request", lane: "review" },
};

export function Inbox() {
  const { data, error, loading, reload } = useAsync(() => api.get<{ tickets: Ticket[] }>("/api/staff/inbox", "staff"));
  const [filter, setFilter] = useState<"open" | "all">("open");
  const tickets = (data?.tickets ?? []).filter((t) => filter === "all" || t.status !== "Resolved");

  const setStatus = async (t: Ticket, status: Ticket["status"]) => {
    await api.patch(`/api/staff/tickets/${t.ticket_number}`, { status }, "staff");
    void reload();
    refreshCounts();
  };

  return (
    <div className="space-y-8">
      <PageHeader title="Inbox" lead="Every message from text, email or the web form is sorted: thank-yous are answered, status questions are looked up, complaints become tickets and refill requests start a confirmation." />
      {error && <ErrorNote message={error} onRetry={reload} />}

      <div className="grid gap-10 lg:grid-cols-[1.3fr_1fr]">
        <Section
          title="Tickets"
          aside={
            <div className="flex gap-1 rounded-full bg-sunken p-1" role="group" aria-label="Show tickets">
              {(["open", "all"] as const).map((f) => (
                <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}
                  className={`rounded-full px-3 py-1 text-sm font-semibold ${filter === f ? "bg-surface text-ink shadow-sm" : "text-ink-soft"}`}>
                  {f === "open" ? "Open" : "All"}
                </button>
              ))}
            </div>
          }
        >
          {loading && !data ? <Skeleton rows={4} /> : tickets.length === 0 ? (
            <EmptyState icon={<InboxIcon size={20} />} title="No open tickets" body="Complaints and unanswered questions become tickets here, with the patient's own words." />
          ) : (
            <ul className="space-y-3">
              {tickets.map((t) => (
                <li key={t.ticket_number} className={`panel lane lane-${STATUS_LANE[t.status]} p-4 pl-6`}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="font-bold">#{t.ticket_number}{t.patient_name && <span className="font-normal text-ink-soft">, {t.patient_name}</span>}</p>
                    <span className="text-[13px] text-ink-soft">{relativeTime(t.created_at)}</span>
                  </div>
                  <p className="mt-1.5 font-display text-[17px] italic">“{t.complaint_text}”</p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Chip lane={STATUS_LANE[t.status]}>{t.status}</Chip>
                    <span className="flex-1" />
                    {t.status !== "In Progress" && t.status !== "Resolved" && <button className="btn btn-quiet min-h-9 px-4 text-sm" onClick={() => setStatus(t, "In Progress")}>I'm on it</button>}
                    {t.status !== "Resolved" && <button className="btn btn-primary min-h-9 px-4 text-sm" onClick={() => setStatus(t, "Resolved")}>Mark resolved</button>}
                    {t.status === "Resolved" && <button className="btn btn-ghost min-h-9 px-4 text-sm" onClick={() => setStatus(t, "Unresolved")}>Reopen</button>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Triage onChange={() => { void reload(); refreshCounts(); }} />
      </div>
    </div>
  );
}

const SAMPLES = [
  "Thanks for sorting out my medication refill issue.",
  "My medication refill still hasn't been filled.",
  "I need to refill my prescription.",
];

function Triage({ onChange }: { onChange: () => void }) {
  const [message, setMessage] = useState("");
  const [patient, setPatient] = useState("");
  const [medication, setMedication] = useState("");
  const [active, setActive] = useState<{ ticket: string; stage: string } | null>(null);
  const [log, setLog] = useState<{ who: "in" | "out"; text: string; tag?: { label: string; lane: Lane | "neutral" } }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (e?: FormEvent, text = message) => {
    e?.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<TriageResult>("/api/staff/inbox/messages", {
        message: text, patient_name: patient || undefined, medication_name: medication || undefined, active_ticket: active?.ticket,
      }, "staff");
      const tag = CATEGORY[r.category] ?? { label: r.category, lane: "neutral" as const };
      const next = [...log, { who: "in" as const, text }, { who: "out" as const, text: r.message, tag }];
      if (r.chained_refill_agent) next.push({ who: "out", text: r.chained_refill_agent.message });
      setLog(next);
      const stage = r.chained_refill_agent?.stage ?? r.stage;
      const ticket = r.chained_refill_agent?.ticket_number ?? r.ticket_number;
      setActive(stage && ticket && stage.startsWith("awaiting") ? { ticket, stage } : null);
      setMessage("");
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The message wasn't processed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Triage a message">
      <div className="panel p-4">
        <p className="text-sm text-ink-soft">Paste a message as it arrived. Tendwell sorts it and drafts the reply the patient would get.</p>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <input className="field" placeholder="Patient name" value={patient} onChange={(e) => setPatient(e.target.value)} aria-label="Patient name" />
          <input className="field" placeholder="Medication, if any" value={medication} onChange={(e) => setMedication(e.target.value)} aria-label="Medication name" />
        </div>
        {log.length > 0 && (
          <ol className="mt-4 space-y-2.5" aria-live="polite">
            {log.map((m, i) => (
              <li key={i} className={`settle max-w-[92%] rounded-row px-3.5 py-2.5 ${m.who === "in" ? "bg-sunken" : "ml-auto bg-ceil text-ceil-ink"}`}>
                {m.tag && <span className="mb-1 block"><Chip lane={m.tag.lane}>{m.tag.label}</Chip></span>}
                {m.text}
              </li>
            ))}
          </ol>
        )}
        {active && <p className="mt-3 text-sm font-semibold text-review">Ticket #{active.ticket} is waiting for the patient to answer yes or no.</p>}
        {error && <div className="mt-3"><ErrorNote message={error} /></div>}
        <form onSubmit={send} className="mt-3 flex gap-2">
          <input className="field" placeholder={active ? "Their reply, e.g. “yes”" : "Incoming message"} value={message} onChange={(e) => setMessage(e.target.value)} aria-label="Message" />
          <button className="btn btn-primary shrink-0 px-4" disabled={busy || !message.trim()} aria-label="Triage message"><Send size={17} /></button>
        </form>
        {!active && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {SAMPLES.map((s) => (
              <button key={s} className="rounded-full border border-line px-3 py-1 text-sm hover:bg-sunken" onClick={() => send(undefined, s)} disabled={busy}>{s}</button>
            ))}
          </div>
        )}
      </div>
    </Section>
  );
}
