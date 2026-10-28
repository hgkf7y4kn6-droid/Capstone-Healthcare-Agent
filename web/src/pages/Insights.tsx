import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { Chip, ErrorNote, PageHeader, Section, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { ruleLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";

type Insights = {
  latency: Record<string, { count: number; avg_ms: number; median_ms: number; p95_ms: number; success_rate: number }>;
  throughput: { time: string; requests: number }[];
  engines: Record<string, number>;
  safety: Record<string, number>;
  triage: Record<string, number>;
  refills: { auto: number; review: number; reasons: Record<string, number> };
};
type Quality = { summary: { count: number; classification_accuracy: number; routing_success_rate: number; avg_latency_ms: number }; cases: { id: string; expected_category: string; predicted_category: string; classification_correct: boolean; routing_succeeded: boolean }[] };

const pretty = (s: string) => s.replace(/[._]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/** Single-hue magnitude bars, labelled, with the value in text (never color-only). */
function Bars({ data, unit = "" }: { data: [string, number][]; unit?: string }) {
  const max = Math.max(1, ...data.map(([, v]) => v));
  if (!data.length) return <p className="text-ink-soft">Nothing recorded yet.</p>;
  return (
    <ul className="space-y-3">
      {data.map(([label, v]) => (
        <li key={label} title={`${label}: ${v}${unit}`}>
          <div className="flex justify-between gap-3 text-[15px]"><span>{label}</span><span className="font-semibold">{v}{unit}</span></div>
          <div className="mt-1 h-2.5 rounded-full bg-sunken" aria-hidden>
            <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(2, (v / max) * 100)}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Throughput({ points }: { points: Insights["throughput"] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return <p className="text-ink-soft">The hourly chart fills in as requests come through.</p>;
  const max = Math.max(...points.map((p) => p.requests));
  const label = (iso: string) => new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric" });
  const h = hover !== null ? points[hover] : null;
  return (
    <div>
      <p className="mb-2 h-6 text-sm text-ink-soft" aria-live="polite">
        {h ? <><span className="font-semibold text-ink">{h.requests} requests</span> in the hour from {label(h.time)}</> : `Peak hour: ${max} requests`}
      </p>
      <div className="flex h-36 items-end gap-[2px]" onMouseLeave={() => setHover(null)} role="img" aria-label={`Requests per hour, ${points.length} hours, peak ${max}`}>
        {points.map((p, i) => (
          <div key={p.time} className="flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}>
            <div className={`w-full rounded-t-[4px] ${hover === i ? "bg-ceil-ink" : "bg-primary"}`} style={{ height: `${Math.max(3, (p.requests / max) * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-[13px] text-ink-soft"><span>{label(points[0]!.time)}</span><span>{label(points[points.length - 1]!.time)}</span></div>
    </div>
  );
}

export function InsightsPage() {
  const { data, error, loading, reload } = useAsync(() => api.get<Insights>("/api/staff/insights", "staff"));
  const [quality, setQuality] = useState<Quality | null>(null);
  const [running, setRunning] = useState(false);
  const [qError, setQError] = useState<string | null>(null);

  const runQuality = async () => {
    setRunning(true);
    setQError(null);
    try { setQuality(await api.post<Quality>("/api/staff/insights/quality-check", {}, "staff")); }
    catch (e) { setQError(e instanceof Error ? e.message : "The check didn't run."); }
    finally { setRunning(false); }
  };

  if (error) return <ErrorNote message={error} onRetry={reload} />;
  if (loading && !data) return <Skeleton rows={6} />;
  if (!data) return null;

  const total = data.refills.auto + data.refills.review;
  const autoPct = total ? Math.round((data.refills.auto / total) * 100) : 0;
  const modules = Object.entries(data.latency).sort((a, b) => b[1].count - a[1].count).slice(0, 8);

  return (
    <div className="space-y-10">
      <PageHeader title="Insights" lead="How much AegisTriage handled on its own, where it asked for a person, and how fast each part responds." />

      <section className="hero grid gap-6 p-6 sm:grid-cols-3 sm:p-8" aria-label="Refill automation">
        <div className="sm:col-span-1">
          <p className="font-display text-[64px] leading-none">{autoPct}%</p>
          <p className="mt-2 text-hero-soft">of refills cleared every rule and went out without anyone touching them</p>
        </div>
        <div className="sm:col-span-2">
          <p className="mb-2 font-semibold">{total} refill requests</p>
          <div className="flex h-4 overflow-hidden rounded-full" role="img" aria-label={`${data.refills.auto} handled, ${data.refills.review} needed a person`}>
            <div className="bg-hero-done" style={{ width: `${autoPct}%` }} />
            <div className="ml-[2px] flex-1 bg-hero-review" />
          </div>
          <div className="mt-3 flex flex-wrap gap-4 text-sm">
            <span className="lane lane-done">Handled: {data.refills.auto}</span>
            <span className="lane lane-review">Needed a person: {data.refills.review}</span>
          </div>
        </div>
      </section>

      <div className="grid gap-10 lg:grid-cols-2">
        <Section title="Why refills stopped"><Bars data={Object.entries(data.refills.reasons).map(([k, v]) => [ruleLabel(k), v] as [string, number]).sort((a, b) => b[1] - a[1])} /></Section>
        <Section title="Messages by kind"><Bars data={Object.entries(data.triage).map(([k, v]) => [pretty(k), v] as [string, number]).sort((a, b) => b[1] - a[1])} /></Section>
        <Section title="Safety screen matches" aside="Message text is never stored">
          <Bars data={Object.entries(data.safety).map(([k, v]) => [pretty(k), v] as [string, number])} />
        </Section>
        <Section title="Requests per hour"><Throughput points={data.throughput} /></Section>
      </div>

      <Section title="Response time by part" aside="Last 2,000 operations">
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-[15px]">
            <thead className="border-b border-line text-sm text-ink-soft">
              <tr><th className="px-4 py-2.5 font-semibold">Part</th><th className="px-4 py-2.5 font-semibold">Runs</th><th className="px-4 py-2.5 font-semibold">Typical</th><th className="px-4 py-2.5 font-semibold">Slowest 5%</th><th className="px-4 py-2.5 font-semibold">Succeeded</th></tr>
            </thead>
            <tbody className="divide-y divide-line">
              {modules.map(([name, m]) => (
                <tr key={name}>
                  <td className="px-4 py-2.5">{pretty(name)}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{m.count}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{m.median_ms} ms</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{m.p95_ms} ms</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{Math.round(m.success_rate * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-sm text-ink-soft">A run counts as not succeeded when a rule stopped it, which is often the right outcome.</p>
      </Section>

      <Section title="Triage quality check">
        <div className="panel p-5">
          <p className="max-w-[64ch] text-ink-soft">Runs a labelled set of patient messages through triage on a throwaway copy, so nothing lands in your real inbox.</p>
          <button className="btn btn-primary mt-4" onClick={runQuality} disabled={running}><FlaskConical size={17} /> {running ? "Running the check…" : "Run the quality check"}</button>
          {qError && <div className="mt-3"><ErrorNote message={qError} /></div>}
          {quality && (
            <div className="settle mt-5">
              <p className="font-semibold">
                {Math.round(quality.summary.classification_accuracy * 100)}% sorted correctly and {Math.round(quality.summary.routing_success_rate * 100)}% routed correctly across {quality.summary.count} messages.
              </p>
              <ul className="mt-3 flex flex-wrap gap-1.5">
                {quality.cases.map((c) => (
                  <li key={c.id}><Chip lane={c.classification_correct && c.routing_succeeded ? "done" : "urgent"}>{c.id}: {pretty(c.predicted_category)}</Chip></li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Section>
    </div>
  );
}
