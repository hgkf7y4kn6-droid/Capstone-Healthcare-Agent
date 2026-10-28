import { CalendarCheck2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { refreshCounts } from "../components/Shell";
import { Chip, EmptyState, ErrorNote, PageHeader, Section, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, relativeTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";

type Appt = { appointment_id: number; patient_id: number; full_name: string; doctor_name: string; specialty: string; start_time: string; status: string; created_at: string };

export function Schedule() {
  const { data, error, loading, reload } = useAsync(() => api.get<Appt[]>("/api/staff/appointments", "staff"));
  const now = Date.now();
  const requested = data?.filter((a) => a.status === "requested") ?? [];
  const upcoming = data?.filter((a) => a.status === "confirmed" && new Date(a.start_time).getTime() >= now) ?? [];
  const past = (data?.filter((a) => a.status !== "requested" && !upcoming.includes(a)) ?? []).reverse();

  return (
    <div className="space-y-10">
      <PageHeader title="Schedule" lead="The assistant works out the specialty; you pick the time. Patients see the confirmed visit in their portal." />
      {error && <ErrorNote message={error} onRetry={reload} />}

      <Section title="Visit requests" aside={data && `${requested.length} to book`}>
        {loading && !data ? <Skeleton rows={3} /> : requested.length === 0 ? (
          <EmptyState icon={<CalendarCheck2 size={20} />} title="Every request is booked" body="When a patient asks the assistant for a visit, the request shows up here with the specialty already chosen." />
        ) : (
          <ul className="space-y-3">{requested.map((a) => <RequestRow key={a.appointment_id} appt={a} onSaved={() => { void reload(); refreshCounts(); }} />)}</ul>
        )}
      </Section>

      <div className="grid gap-10 lg:grid-cols-2">
        <Section title="Coming up">
          {upcoming.length === 0 ? <p className="text-ink-soft">No confirmed visits ahead.</p> : (
            <ul className="panel divide-y divide-line">
              {upcoming.map((a) => (
                <li key={a.appointment_id} className="lane lane-done py-3 pr-4">
                  <div className="pl-1">
                    <p className="font-semibold">{dateTime(a.start_time)}</p>
                    <p className="text-sm text-ink-soft">{a.full_name}, {a.specialty} with {a.doctor_name}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Earlier">
          <ul className="panel divide-y divide-line">
            {past.slice(0, 10).map((a) => (
              <li key={a.appointment_id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate font-semibold">{a.full_name}</p>
                  <p className="truncate text-sm text-ink-soft">{a.specialty || "Visit"}, {dateTime(a.start_time)}</p>
                </div>
                <Chip lane={a.status === "declined" || a.status === "cancelled" ? "neutral" : "done"}>{a.status[0]!.toUpperCase() + a.status.slice(1)}</Chip>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </div>
  );
}

function RequestRow({ appt, onSaved }: { appt: Appt; onSaved: () => void }) {
  const [when, setWhen] = useState("");
  const [doctor, setDoctor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (status: "confirmed" | "declined", e?: FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/api/staff/appointments/${appt.appointment_id}`, {
        status, start_time: status === "confirmed" ? when : undefined, doctor_name: doctor.trim() || undefined,
      }, "staff");
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save.");
      setBusy(false);
    }
  };
  return (
    <li className="panel lane lane-schedule p-4 pl-6">
      <form onSubmit={(e) => save("confirmed", e)} className="grid gap-3 md:grid-cols-[1.2fr_1fr_1fr_auto] md:items-end">
        <div>
          <p className="font-bold">{appt.specialty} visit</p>
          <p className="text-sm text-ink-soft">
            <Link to={`/console/patients/${appt.patient_id}`} className="font-semibold text-primary hover:underline">{appt.full_name}</Link>, requested {relativeTime(appt.created_at)}
          </p>
        </div>
        <label><span className="mb-1 block text-sm font-semibold">Time</span>
          <input className="field" type="datetime-local" required value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        <label><span className="mb-1 block text-sm font-semibold">With</span>
          <input className="field" placeholder="Next available" value={doctor} onChange={(e) => setDoctor(e.target.value)} /></label>
        <div className="flex gap-2">
          <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => save("declined")}>Decline</button>
          <button className="btn btn-primary" disabled={busy}>Confirm</button>
        </div>
      </form>
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}
    </li>
  );
}
