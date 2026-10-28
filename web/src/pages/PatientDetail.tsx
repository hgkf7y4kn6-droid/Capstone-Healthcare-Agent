import { ArrowLeft, ExternalLink, FileText, Pill, Phone } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { refreshCounts } from "../components/Shell";
import { Chip, EmptyState, ErrorNote, Section, Skeleton } from "../components/ui";
import { api, session, type PatientSession } from "../lib/api";
import { dateTime, initials, relativeTime, ruleLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export type Rx = { prescription_id: number; patient_id: number; full_name?: string; name: string; refills: number; controlled: boolean; schedule: string | null; lab: string | null; lab_range: string | null; prescriber: string | null };
type HistoryItem = { entry_id: number; entry_type: string; content: unknown; created_at: string };
type Detail = {
  patient: { patient_id: number; full_name: string; age: number | null; sex: string | null; primary_doctor: string | null; blood_type: string | null; external_id: string | null; next_appointment: string | null; identity_factors: { dob: boolean; address: boolean; ssn_last4: boolean } };
  history: HistoryItem[];
  prescriptions: Rx[];
  appointments: { appointment_id: number; doctor_name: string; specialty: string; start_time: string; status: string }[];
  reviews: { review_id: number; medication_name: string; reason: string; status: string; created_at: string }[];
  erx: { queue_id: number; medication_name?: string; confirmation_id: string; pharmacy_id: string; created_at: string }[];
};

const GROUPS: [string, string][] = [
  ["diagnosis", "Diagnoses"], ["alert", "Alerts and allergies"], ["treatment", "Treatments"], ["lab_result", "Lab results"],
  ["visit", "Visits"], ["clinical_note", "Clinical notes"], ["note", "Staff notes"], ["emergency_contact", "Emergency contact"],
];

export function rxFlags(rx: Rx): { text: string; lane: "review" | "done" | "neutral" }[] {
  const flags: { text: string; lane: "review" | "done" | "neutral" }[] = [];
  if (rx.controlled) flags.push({ text: `Controlled${rx.schedule ? ` (${rx.schedule})` : ""}`, lane: "review" });
  if (rx.refills <= 0) flags.push({ text: "No refills left", lane: "review" });
  if (rx.lab) flags.push({ text: `Needs ${rx.lab} ${rx.lab_range ?? ""}`.trim(), lane: "neutral" });
  if (!flags.length) flags.push({ text: "Clears automatically", lane: "done" });
  return flags;
}

function Entry({ content }: { content: unknown }) {
  if (content && typeof content === "object") {
    const pairs = Object.entries(content as Record<string, unknown>).filter(([, v]) => v !== null && v !== "");
    return (
      <span>
        {pairs.map(([k, v], i) => (
          <span key={k}>
            {i > 0 && <span className="text-ink-soft">; </span>}
            {i > 0 && <span className="text-ink-soft">{k.replace(/_/g, " ")} </span>}
            {typeof v === "object" ? Object.entries(v as Record<string, unknown>).map(([a, b]) => `${a} ${b}`).join(", ") : String(v)}
          </span>
        ))}
      </span>
    );
  }
  const text = String(content);
  if (text.length <= 220) return <span>{text}</span>;
  return (
    <details>
      <summary className="cursor-pointer">{text.slice(0, 140).trim()}… <span className="font-semibold text-primary">Show the full document</span></summary>
      <p className="mt-2 whitespace-pre-wrap text-sm text-ink-soft">{text}</p>
    </details>
  );
}

export function PatientDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.get<Detail>(`/api/staff/patients/${id}`, "staff"), [id]);
  const [phoneRx, setPhoneRx] = useState<Rx | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  const openPortal = async () => {
    try {
      const s = await api.post<PatientSession>(`/api/staff/patients/${id}/portal-session`, {}, "staff");
      session.setPatient(s);
      navigate("/portal");
    } catch (e) {
      setOpenError(e instanceof Error ? e.message : "Couldn't open the portal.");
    }
  };

  if (error) return <ErrorNote message={error} onRetry={reload} />;
  if (loading && !data) return <Skeleton rows={6} />;
  if (!data) return null;
  const p = data.patient;

  return (
    <div className="space-y-10">
      <div>
        <Link to="/console/patients" className="mb-4 inline-flex items-center gap-1.5 font-semibold text-ink-soft hover:text-ink"><ArrowLeft size={17} /> Patients</Link>
        <header className="flex flex-wrap items-center gap-5">
          <span className="grid size-16 place-items-center rounded-full bg-ceil font-display text-[24px] text-ceil-ink" aria-hidden>{initials(p.full_name)}</span>
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-[36px] leading-tight font-medium">{p.full_name}</h1>
            <p className="text-ink-soft">
              {[p.age != null && `${p.age} years`, p.sex, p.blood_type && `Blood type ${p.blood_type}`, p.external_id && `MRN ${p.external_id}`].filter(Boolean).join(", ")}
            </p>
            <p className="text-ink-soft">{p.primary_doctor ?? "No primary doctor on file"}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-quiet" onClick={openPortal}><ExternalLink size={17} /> Open their portal</button>
          </div>
        </header>
        {openError && <div className="mt-3"><ErrorNote message={openError} /></div>}
        <p className="mt-4 text-sm text-ink-soft">
          Identity on file for phone refills: date of birth {p.identity_factors.dob ? "yes" : "missing"}, address {p.identity_factors.address ? "yes" : "missing"}, SSN last four {p.identity_factors.ssn_last4 ? "yes" : "missing"}.
        </p>
      </div>

      <Section title="Medications">
        {data.prescriptions.length === 0 ? (
          <EmptyState icon={<Pill size={20} />} title="No prescriptions on file" body="Prescriptions from your EHR import appear here with the rules each one must pass." />
        ) : (
          <ul className="panel divide-y divide-line">
            {data.prescriptions.map((rx) => (
              <li key={rx.prescription_id} className="flex flex-wrap items-center gap-3 px-4 py-3.5">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{rx.name}</p>
                  <p className="text-sm text-ink-soft">{rx.refills} refill{rx.refills === 1 ? "" : "s"} left{rx.prescriber && `, prescribed by ${rx.prescriber}`}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">{rxFlags(rx).map((f) => <Chip key={f.text} lane={f.lane}>{f.text}</Chip>)}</div>
                <button className="btn btn-quiet min-h-9 px-4 text-sm" onClick={() => setPhoneRx(rx)}><Phone size={15} /> Refill by phone</button>
              </li>
            ))}
          </ul>
        )}
        {phoneRx && <PhoneRefill rx={phoneRx} onClose={() => { setPhoneRx(null); void reload(); refreshCounts(); }} />}
      </Section>

      <div className="grid gap-10 lg:grid-cols-[1.4fr_1fr]">
        <Section title="Record">
          <div className="space-y-6">
            {GROUPS.map(([type, label]) => {
              const items = data.history.filter((h) => h.entry_type === type);
              if (!items.length) return null;
              return (
                <div key={type}>
                  <h3 className="mb-2 font-semibold text-ink-soft">{label}</h3>
                  <ul className="space-y-2">
                    {items.map((h) => (
                      <li key={h.entry_id} className="rounded-row bg-surface px-3.5 py-2.5 ring-1 ring-line">
                        <Entry content={h.content} />
                        <span className="mt-0.5 block text-[13px] text-ink-soft">Added {relativeTime(h.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
            <AddNote patientId={p.patient_id} onAdded={reload} />
          </div>
        </Section>

        <div className="space-y-10">
          <Section title="Appointments">
            {data.appointments.length === 0 ? <p className="text-ink-soft">No appointments yet.</p> : (
              <ul className="space-y-2.5">
                {[...data.appointments].reverse().map((a) => (
                  <li key={a.appointment_id} className={`lane ${a.status === "requested" ? "lane-schedule" : a.status === "confirmed" ? "lane-done" : ""}`}>
                    <p className="font-semibold">{a.specialty || "Visit"} with {a.doctor_name}</p>
                    <p className="text-sm text-ink-soft">{a.status === "requested" ? "Requested, needs a time" : `${a.status[0]!.toUpperCase()}${a.status.slice(1)}, ${dateTime(a.start_time)}`}</p>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="Refill history">
            {data.reviews.length + data.erx.length === 0 ? <p className="text-ink-soft">No refill requests yet.</p> : (
              <ul className="space-y-2.5">
                {data.erx.map((e) => (
                  <li key={`e${e.queue_id}`} className="lane lane-done">
                    <p className="font-semibold">Sent to {e.pharmacy_id}</p>
                    <p className="text-sm text-ink-soft">{e.confirmation_id}, {relativeTime(e.created_at)}</p>
                  </li>
                ))}
                {data.reviews.map((r) => (
                  <li key={`r${r.review_id}`} className={`lane ${r.status === "pending" ? "lane-review" : "lane-done"}`}>
                    <p className="font-semibold">{r.medication_name}: {r.status === "pending" ? "waiting for a provider" : r.status}</p>
                    <p className="text-sm text-ink-soft">{ruleLabel(r.reason)}, {relativeTime(r.created_at)}</p>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

function AddNote({ patientId, onAdded }: { patientId: number; onAdded: () => void }) {
  const [text, setText] = useState("");
  const [type, setType] = useState("note");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post(`/api/staff/patients/${patientId}/history`, { entry_type: type, content: text }, "staff");
      setText("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-2.5 border-t border-line pt-5">
      <label className="block">
        <span className="mb-1.5 flex items-center gap-2 font-semibold"><FileText size={17} /> Add to the record</span>
        <textarea className="field min-h-24 py-2.5" value={text} onChange={(e) => setText(e.target.value)} placeholder="Staff notes stay in the console; patients never see them." required />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <select className="field w-auto" value={type} onChange={(e) => setType(e.target.value)} aria-label="Entry type">
          <option value="note">Staff note</option><option value="diagnosis">Diagnosis</option><option value="treatment">Treatment</option>
          <option value="alert">Alert or allergy</option><option value="visit">Visit</option>
        </select>
        <button className="btn btn-primary" disabled={busy || !text.trim()}>Save to record</button>
      </div>
      {error && <ErrorNote message={error} />}
    </form>
  );
}

type RefillResult = { status: string; message: string; reason?: string; detail?: string };

function PhoneRefill({ rx, onClose }: { rx: Rx; onClose: () => void }) {
  const [dob, setDob] = useState("");
  const [ssn, setSsn] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RefillResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<RefillResult>("/api/staff/refills", {
        patient_id: rx.patient_id, prescription_id: rx.prescription_id, date_of_birth: dob, ssn_last4: ssn || undefined,
      }, "staff"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The refill didn't go through.");
    } finally {
      setBusy(false);
    }
  };
  const lane = result?.status === "processed" ? "done" : result?.status === "routed_to_review" ? "review" : "urgent";
  return (
    <div className="panel settle mt-4 p-5">
      <h3 className="text-[18px] font-bold">Refill {rx.name} by phone</h3>
      <p className="text-ink-soft">Ask the caller to confirm who they are. Tendwell checks it against the record, then runs the same rules as an online request.</p>
      {result ? (
        <div className={`lane lane-${lane} mt-4`}>
          <p className="font-semibold">{result.message}</p>
          {result.detail && <p className="text-sm text-ink-soft">{result.detail}</p>}
          <button className="btn btn-quiet mt-3" onClick={onClose}>Done</button>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label><span className="mb-1.5 block font-semibold">Date of birth</span>
            <input className="field" type="date" required value={dob} onChange={(e) => setDob(e.target.value)} /></label>
          <label><span className="mb-1.5 block font-semibold">SSN last four <span className="font-normal text-ink-soft">(optional)</span></span>
            <input className="field" inputMode="numeric" pattern="\d{4}" maxLength={4} value={ssn} onChange={(e) => setSsn(e.target.value)} /></label>
          <div className="flex gap-2">
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={busy}>{busy ? "Checking…" : "Verify and request"}</button>
          </div>
          {error && <div className="sm:col-span-3"><ErrorNote message={error} /></div>}
        </form>
      )}
    </div>
  );
}
