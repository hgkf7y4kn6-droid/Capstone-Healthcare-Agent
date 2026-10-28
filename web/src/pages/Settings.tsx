import { CheckCircle2, Circle } from "lucide-react";
import { Chip, PageHeader, Section, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { useAsync } from "../lib/useAsync";

type Config = {
  app_name: string; version: string; env: string; clinic: { name: string; phone: string };
  demo: { enabled: boolean }; assistant_engine: string;
  integrations: { calcom: boolean; tavily: boolean; portal_webhook: boolean; sqs: boolean };
};

const INTEGRATIONS: { key: keyof Config["integrations"]; name: string; what: string; env: string }[] = [
  { key: "calcom", name: "Cal.com", what: "Turns confirmed visit requests into real bookings.", env: "CALCOM_API_KEY" },
  { key: "portal_webhook", name: "Patient portal", what: "Tells your portal when a refill's status changes.", env: "PORTAL_WEBHOOK_URL" },
  { key: "tavily", name: "WHO search", what: "Adds WHO fact sheets alongside MedlinePlus.", env: "TAVILY_API_KEY" },
  { key: "sqs", name: "AWS SQS", what: "Streams refill events to your other systems.", env: "AWS_SQS_QUEUE_URL" },
];

const SETUP = [
  ["Set your own access code and session secret", "Add STAFF_ACCESS_CODE and SESSION_SECRET in your host's environment, then turn DEMO_MODE off."],
  ["Load your roster", "Run python -m data.ingest_patient_data --source <your export> on the server."],
  ["Review the refill rules", "Controlled substances, refill counts and lab ranges live in workflows/prescription_refill.py. Have a provider sign off on them."],
  ["Connect scheduling and your portal", "Add the keys above. Each one switches on as soon as it's set."],
  ["Put the assistant on your website", "Patients reach it through your portal's sign-in, so they never re-enter their details."],
];

export function Settings() {
  const { data } = useAsync(() => api.get<Config>("/api/public/config"));
  if (!data) return <Skeleton rows={5} />;
  return (
    <div className="space-y-10">
      <PageHeader title="Settings" lead={`${data.clinic.name}, ${data.clinic.phone}`} />

      <Section title="Patient assistant">
        <div className="panel p-5">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold">Answering with the {data.assistant_engine === "crew" ? "full agent on your local model" : "rules-first concierge"}</p>
            <Chip lane="done">On</Chip>
          </div>
          <p className="mt-1 max-w-[66ch] text-ink-soft">
            {data.assistant_engine === "crew"
              ? "A planner and an executor agent work through each request with your tools. If the model is unavailable, the concierge answers instead."
              : "Fast and predictable: it books, explains records, checks medications and looks things up in MedlinePlus without a language model. Set ASSISTANT_ENGINE=crew on a machine with the model installed to use the full agent."}
          </p>
          <p className="mt-3 text-sm text-ink-soft">The safety screen runs first either way and can't be turned off.</p>
        </div>
      </Section>

      <Section title="Connections">
        <ul className="panel divide-y divide-line">
          {INTEGRATIONS.map((i) => (
            <li key={i.key} className="flex items-start gap-3 px-4 py-3.5">
              {data.integrations[i.key] ? <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-done" aria-label="Connected" /> : <Circle size={20} className="mt-0.5 shrink-0 text-ink-soft" aria-label="Not connected" />}
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{i.name}</p>
                <p className="text-sm text-ink-soft">{i.what}</p>
              </div>
              {data.integrations[i.key] ? <Chip lane="done">Connected</Chip> : <code className="shrink-0 rounded bg-sunken px-2 py-0.5 text-[13px] text-ink-soft">{i.env}</code>}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Going live">
        <ol className="space-y-4">
          {SETUP.map(([title, body], i) => (
            <li key={title} className="flex gap-4">
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ceil font-bold text-ceil-ink">{i + 1}</span>
              <div><p className="font-semibold">{title}</p><p className="text-ink-soft">{body}</p></div>
            </li>
          ))}
        </ol>
      </Section>

      <p className="text-sm text-ink-soft">{data.app_name} {data.version}, {data.env}{data.demo.enabled && ", demo data"}</p>
    </div>
  );
}
