import { CheckCircle2, Pill } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { refreshCounts } from "../components/Shell";
import { Chip, EmptyState, ErrorNote, PageHeader, Section, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { dateOnly, relativeTime, ruleLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

type Review = {
  review_id: number; patient_id: number; full_name: string; medication_name: string; reason: string; status: string; created_at: string;
  context_payload: {
    patient_profile?: { age?: number; sex?: string; primary_doctor?: string };
    failed_rule_detail?: string;
    medication_reconciliation?: { medication: string; remaining_refills: number; status: string }[];
    last_office_visit?: string | null;
  };
};
type Erx = { queue_id: number; patient_id: number; full_name: string; medication_name: string; pharmacy_id: string; confirmation_id: string; created_at: string };

export function Refills() {
  const { data, error, loading, reload } = useAsync(() => api.get<{ reviews: Review[]; erx: Erx[] }>("/api/staff/refills", "staff"));
  const pending = data?.reviews.filter((r) => r.status === "pending") ?? [];
  const decided = data?.reviews.filter((r) => r.status !== "pending") ?? [];

  return (
    <div className="space-y-10">
      <PageHeader
        title="Refills"
        lead="Refills that cleared identity, controlled-substance, refill-count and lab checks went straight to the pharmacy. These are the ones a rule stopped."
      />
      {error && <ErrorNote message={error} onRetry={reload} />}

      <Section title="Waiting for a provider" aside={data && `${pending.length} waiting`}>
        {loading && !data ? <Skeleton rows={3} /> : pending.length === 0 ? (
          <EmptyState icon={<CheckCircle2 size={20} />} title="No refills waiting" body="When a rule stops a refill — a controlled substance, no refills left, or a missing or out-of-range lab — it lands here with the chart context gathered." />
        ) : (
          <ul className="space-y-4">
            {pending.map((r) => <ReviewCard key={r.review_id} review={r} onDecided={() => { void reload(); refreshCounts(); }} />)}
          </ul>
        )}
      </Section>

      <div className="grid gap-10 lg:grid-cols-2">
        <Section title="Sent to the pharmacy" aside={data && `${data.erx.length} total`}>
          {data && data.erx.length === 0 ? <p className="text-ink-soft">Nothing sent yet.</p> : (
            <ul className="panel divide-y divide-line">
              {data?.erx.slice(0, 12).map((e) => (
                <li key={e.queue_id} className="lane lane-done py-3 pr-4">
                  <div className="pl-1">
                    <p className="font-semibold">{e.medication_name} for {e.full_name}</p>
                    <p className="text-sm text-ink-soft">{e.pharmacy_id === "PROVIDER-APPROVED" ? "Approved by a provider" : e.pharmacy_id}, {relativeTime(e.created_at)}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Decided">
          {decided.length === 0 ? <p className="text-ink-soft">Provider decisions will be listed here.</p> : (
            <ul className="panel divide-y divide-line">
              {decided.slice(0, 12).map((r) => (
                <li key={r.review_id} className={`lane ${r.status === "approved" ? "lane-done" : "lane-urgent"} py-3 pr-4`}>
                  <div className="flex items-center justify-between gap-3 pl-1">
                    <div>
                      <p className="font-semibold">{r.medication_name} for {r.full_name}</p>
                      <p className="text-sm text-ink-soft">{ruleLabel(r.reason)}</p>
                    </div>
                    <Chip lane={r.status === "approved" ? "done" : "urgent"}>{r.status === "approved" ? "Approved" : "Denied"}</Chip>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </div>
  );
}

function ReviewCard({ review: r, onDecided }: { review: Review; onDecided: () => void }) {
  const [busy, setBusy] = useState<"approved" | "denied" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ctx = r.context_payload ?? {};
  const decide = async (decision: "approved" | "denied") => {
    setBusy(decision);
    setError(null);
    try {
      await api.post(`/api/staff/reviews/${r.review_id}/decision`, { decision }, "staff");
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The decision wasn't saved.");
      setBusy(null);
    }
  };
  return (
    <li className="panel lane lane-review p-5 pl-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[19px] font-bold">{r.medication_name}</p>
          <p className="text-ink-soft">
            <Link to={`/console/patients/${r.patient_id}`} className="font-semibold text-primary hover:underline">{r.full_name}</Link>
            {ctx.patient_profile?.age != null && `, ${ctx.patient_profile.age}`}
            {ctx.patient_profile?.primary_doctor && ` — ${ctx.patient_profile.primary_doctor}`}
          </p>
        </div>
        <div className="text-right">
          <Chip lane="review">{ruleLabel(r.reason)}</Chip>
          <p className="mt-1 text-[13px] text-ink-soft">{relativeTime(r.created_at)}</p>
        </div>
      </div>
      {ctx.failed_rule_detail && <p className="mt-3 max-w-[70ch]">{ctx.failed_rule_detail}</p>}
      <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
        <div>
          <dt className="font-semibold text-ink-soft">Current medications</dt>
          <dd className="mt-1">{ctx.medication_reconciliation?.length ? ctx.medication_reconciliation.map((m) => `${m.medication} (${m.remaining_refills} left)`).join(", ") : "None on file"}</dd>
        </div>
        <div>
          <dt className="font-semibold text-ink-soft">Last office visit</dt>
          <dd className="mt-1">{dateOnly(ctx.last_office_visit)}</dd>
        </div>
      </dl>
      {error && <div className="mt-3"><ErrorNote message={error} /></div>}
      <div className="mt-5 flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={!!busy} onClick={() => decide("approved")}>
          <Pill size={17} /> {busy === "approved" ? "Approving…" : "Approve and send"}
        </button>
        <button className="btn btn-quiet" disabled={!!busy} onClick={() => decide("denied")}>{busy === "denied" ? "Denying…" : "Deny"}</button>
      </div>
    </li>
  );
}
