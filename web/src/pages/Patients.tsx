import { Search, UserPlus, Users } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { Chip, EmptyState, ErrorNote, PageHeader, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { initials } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export type PatientRow = {
  patient_id: number; full_name: string; age: number | null; sex: string | null; primary_doctor: string | null;
  specialty: string | null; conditions: string[]; prescriptions: number; pending_reviews: number; external_id: string | null;
};

export function Patients() {
  const navigate = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.get<PatientRow[]>("/api/staff/patients", "staff"));
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return data ?? [];
    return (data ?? []).filter((p) =>
      [p.full_name, p.primary_doctor, p.external_id, ...p.conditions].filter(Boolean).join(" ").toLowerCase().includes(needle));
  }, [data, q]);

  return (
    <div>
      <PageHeader
        title="Patients"
        lead={data ? `${data.length} on the roster. Search by name, condition, doctor or MRN.` : undefined}
        actions={<button className="btn btn-primary" onClick={() => setAdding((a) => !a)}><UserPlus size={18} /> Add a patient</button>}
      />
      {adding && <AddPatient onDone={(id) => { setAdding(false); void reload(); if (id) navigate(`/console/patients/${id}`); }} />}

      <label className="relative mb-4 block">
        <span className="sr-only">Search patients</span>
        <Search size={18} className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-ink-soft" />
        <input className="field pl-10" placeholder="Search patients" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>

      {error && <ErrorNote message={error} onRetry={reload} />}
      {loading && !data ? <Skeleton rows={6} /> : rows.length === 0 ? (
        <EmptyState icon={<Users size={20} />} title={q ? "No one matches that search" : "No patients yet"}
          body={q ? "Try a last name, a condition like “asthma”, or an MRN." : "Add a patient, or import your roster from an EHR export."} />
      ) : (
        <ul className="panel divide-y divide-line">
          {rows.map((p) => (
            <li key={p.patient_id}>
              <Link to={`/console/patients/${p.patient_id}`} className="flex items-center gap-4 px-4 py-3.5 hover:bg-sunken">
                <span className="grid size-11 shrink-0 place-items-center rounded-full bg-ceil font-bold text-ceil-ink" aria-hidden>{initials(p.full_name)}</span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">
                    {p.full_name}
                    <span className="font-normal text-ink-soft">{p.age != null && `, ${p.age}`}</span>
                  </p>
                  <p className="truncate text-sm text-ink-soft">{p.conditions.length ? p.conditions.join(", ") : "No diagnoses on file"}</p>
                </div>
                <div className="hidden shrink-0 text-right text-sm text-ink-soft md:block">
                  <p>{p.primary_doctor ?? "No primary doctor"}</p>
                  <p>{p.prescriptions} prescription{p.prescriptions === 1 ? "" : "s"}</p>
                </div>
                {p.pending_reviews > 0 && <Chip lane="review">{p.pending_reviews} to review</Chip>}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AddPatient({ onDone }: { onDone: (id?: number) => void }) {
  const [form, setForm] = useState({ full_name: "", age: "", sex: "F", relation_to_user: "self" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const p = await api.post<{ patient_id: number }>("/api/staff/patients", { ...form, age: Number(form.age) }, "staff");
      onDone(p.patient_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add the patient.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="panel settle mb-6 grid gap-4 p-5 sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-end">
      <label><span className="mb-1.5 block font-semibold">Full name</span>
        <input className="field" required minLength={2} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
      <label><span className="mb-1.5 block font-semibold">Age</span>
        <input className="field" required type="number" min={0} max={120} value={form.age} onChange={(e) => setForm({ ...form, age: e.target.value })} /></label>
      <label><span className="mb-1.5 block font-semibold">Sex</span>
        <select className="field" value={form.sex} onChange={(e) => setForm({ ...form, sex: e.target.value })}>
          <option value="F">Female</option><option value="M">Male</option><option value="Other">Other</option>
        </select></label>
      <div className="flex gap-2">
        <button type="button" className="btn btn-ghost" onClick={() => onDone()}>Cancel</button>
        <button className="btn btn-primary" disabled={busy}>Add patient</button>
      </div>
      {error && <div className="sm:col-span-4"><ErrorNote message={error} /></div>}
    </form>
  );
}
