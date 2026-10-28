import { Check } from "lucide-react";
import { Link } from "react-router";
import { Brand } from "../components/Brand";
import { ThemeToggle } from "../components/ThemeToggle";
import { Chip } from "../components/ui";
import { laneLabel, type Lane } from "../lib/lanes";

const NIGHT: { time: string; who: string; said: string; lane: Lane; outcome: string }[] = [
  { time: "9:42 pm", who: "Robert L.", said: "Can I get my atorvastatin refilled?", lane: "done", outcome: "Identity confirmed, 3 refills left — sent to CVS Horsham" },
  { time: "11:15 pm", who: "Emily C.", said: "I need more of my lorazepam", lane: "review", outcome: "Controlled substance — on Dr. Whitfield's list with her chart attached" },
  { time: "6:08 am", who: "Daniel K.", said: "The migraines are back, can I see someone?", lane: "schedule", outcome: "Neurology visit requested — front desk picks a time" },
  { time: "6:51 am", who: "A caller", said: "My dad has chest pain and his arm feels numb", lane: "urgent", outcome: "Told to call 911 now. Nothing else happened." },
];

const STEPS = [
  {
    title: "Emergencies come first",
    body: "Every message is checked against fixed safety rules before anything else reads it. Chest pain, stroke signs or thoughts of self-harm get 911 or 988 right away — no chat, no booking.",
  },
  {
    title: "Rules make the clinical calls",
    body: "Refills pass identity checks, controlled-substance rules, refill counts and lab ranges you can read and change. If any rule says no, a provider decides — with the chart already pulled.",
  },
  {
    title: "The assistant only explains",
    body: "Language comes last. Tendwell AA tells the patient what happened in plain words, cites MedlinePlus for health information, and never invents a diagnosis.",
  },
];

const PLANS = [
  { name: "Solo", price: "$149", per: "per month", for: "One provider getting their evenings back", items: ["Patient assistant on your site", "Refill rules and provider review", "Message triage and tickets", "Email support"] },
  { name: "Practice", price: "$449", per: "per month", for: "Up to six providers and a front desk", items: ["Everything in Solo", "Cal.com scheduling", "Patient-portal sign-in handoff", "Insights and quality checks", "Priority support"], featured: true },
  { name: "Network", price: "Let's talk", per: "", for: "Multi-site groups with their own EHR", items: ["Everything in Practice", "FHIR EHR connection", "Single sign-on", "Dedicated onboarding"] },
];

export function Landing() {
  return (
    <div className="min-h-svh">
      <header className="mx-auto flex max-w-[1180px] items-center justify-between gap-4 px-5 py-5 sm:px-8">
        <Brand />
        <nav className="flex items-center gap-1 sm:gap-2" aria-label="Site">
          <a href="#how" className="hidden rounded-full px-3 py-2 font-semibold text-ink-soft hover:text-ink sm:block">How it decides</a>
          <a href="#pricing" className="hidden rounded-full px-3 py-2 font-semibold text-ink-soft hover:text-ink sm:block">Pricing</a>
          <ThemeToggle />
          <Link to="/sign-in" className="btn btn-quiet">Sign in</Link>
        </nav>
      </header>

      <main>
        <section className="mx-auto grid max-w-[1180px] items-center gap-10 px-5 pt-6 pb-16 sm:px-8 lg:grid-cols-[1fr_1.05fr] lg:gap-14 lg:pt-12 lg:pb-24">
          <div>
            <h1 className="font-display text-[48px] leading-[1.02] font-medium tracking-[-0.02em] sm:text-[64px] lg:text-[76px]">
              The front desk that never closes.
            </h1>
            <p className="mt-6 max-w-[46ch] text-[19px] leading-relaxed text-ink-soft">
              Tendwell AA answers patients, routes refills through your safety rules and takes booking requests all night.
              Clear-cut requests finish on their own. Everything else is waiting for the right person in the morning,
              chart already open.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/sign-in?demo=1" className="btn btn-primary px-6 text-base">Open the live demo</Link>
              <a href="#pricing" className="btn btn-quiet px-6 text-base">See pricing</a>
            </div>
            <p className="mt-4 text-sm text-ink-soft">The demo runs on synthetic patients. No sign-up.</p>
          </div>

          {/* The one orchestrated moment: last night's messages route into their lanes. */}
          <figure className="hero p-5 sm:p-7" aria-label="Example: four overnight messages and where Tendwell AA routed them">
            <div className="flex items-baseline justify-between gap-3">
              <p className="font-display text-[22px]">While the clinic was closed</p>
              <p className="text-sm text-hero-soft">9:40 pm to 7:00 am</p>
            </div>
            <ol className="mt-5 space-y-3">
              {NIGHT.map((m, i) => (
                <li key={m.time} className={`route-in lane lane-${m.lane} rounded-row bg-white/[0.06] py-3 pr-4`} style={{ animationDelay: `${250 + i * 260}ms` }}>
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <span className="font-semibold">{m.who} <span className="font-normal text-hero-soft">at {m.time}</span></span>
                    <Chip lane={m.lane}>{laneLabel[m.lane]}</Chip>
                  </div>
                  <p className="mt-1 font-display text-[17px] italic">“{m.said}”</p>
                  <p className="mt-1 text-sm text-hero-soft">{m.outcome}</p>
                </li>
              ))}
            </ol>
          </figure>
        </section>

        <section id="how" className="border-y border-line bg-surface">
          <div className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8 lg:py-20">
            <h2 className="max-w-[22ch] font-display text-[36px] leading-tight font-medium sm:text-[44px]">How Tendwell AA decides what to do</h2>
            <p className="mt-3 max-w-[60ch] text-ink-soft">
              The order matters. Safety and clinical rules run before any language model sees a message, so the answer
              to “should this refill go out?” is always the same, and always one your providers can read.
            </p>
            <ol className="mt-10 grid gap-8 md:grid-cols-3 md:gap-10">
              {STEPS.map((s, i) => (
                <li key={s.title} className="border-t-2 border-primary pt-5">
                  <span className="font-display text-[42px] leading-none text-primary">{i + 1}</span>
                  <h3 className="mt-3 text-[19px] font-bold">{s.title}</h3>
                  <p className="mt-2 text-ink-soft">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8 lg:py-20">
          <h2 className="font-display text-[36px] leading-tight font-medium sm:text-[44px]">What's waiting in the morning</h2>
          <div className="mt-8 grid gap-x-10 gap-y-6 sm:grid-cols-2">
            {([
              ["done", "Refills that cleared every rule were sent to the pharmacy, and thank-you notes were answered. You'll see them in the log, but there's nothing to do."],
              ["review", "Controlled substances, empty refills, out-of-range labs and unhappy patients. Each one has the context gathered and a one-tap decision."],
              ["schedule", "Visit requests with the specialty already worked out. Pick a time and the patient sees it in their portal."],
              ["urgent", "Anything that matched the safety screen, so you can follow up. The patient was already told to call 911 or 988."],
            ] as [Lane, string][]).map(([lane, body]) => (
              <div key={lane} className={`lane lane-${lane} py-1`}>
                <h3 className="text-[19px] font-bold">{laneLabel[lane]}</h3>
                <p className="mt-1 max-w-[52ch] text-ink-soft">{body}</p>
              </div>
            ))}
          </div>
        </section>

        <section id="pricing" className="bg-hero text-on-hero">
          <div className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8 lg:py-20">
            <h2 className="font-display text-[36px] leading-tight font-medium sm:text-[44px]">Priced per practice, not per message</h2>
            <p className="mt-3 max-w-[56ch] text-hero-soft">Every plan includes the safety screen, the refill rule engine and unlimited patients. Start with a 30-day pilot.</p>
            <div className="mt-10 grid gap-5 lg:grid-cols-3">
              {PLANS.map((p) => (
                <div key={p.name} className={`flex flex-col rounded-panel p-6 ${p.featured ? "bg-bg text-ink" : "border border-white/15"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-[20px] font-bold">{p.name}</h3>
                    {p.featured && <Chip lane="schedule">Most practices</Chip>}
                  </div>
                  <p className={`mt-1 ${p.featured ? "text-ink-soft" : "text-hero-soft"}`}>{p.for}</p>
                  <p className="mt-5 font-display text-[44px] leading-none">
                    {p.price} <span className={`font-sans text-base ${p.featured ? "text-ink-soft" : "text-hero-soft"}`}>{p.per}</span>
                  </p>
                  <ul className="mt-6 space-y-2.5">
                    {p.items.map((it) => (
                      <li key={it} className="flex gap-2.5">
                        <Check size={18} className={`mt-0.5 shrink-0 ${p.featured ? "text-primary" : "text-ceil"}`} aria-hidden />
                        {it}
                      </li>
                    ))}
                  </ul>
                  <div className="mt-auto pt-7">
                    {p.name === "Network" ? (
                      <a href="mailto:hello@richesdigitalventures.com?subject=Tendwell AA%20AA%20Network" className={`btn w-full ${p.featured ? "btn-primary" : "border border-white/25 hover:bg-white/10"}`}>Talk to us</a>
                    ) : (
                      <Link to="/sign-in?demo=1" className={`btn w-full ${p.featured ? "btn-primary" : "border border-white/25 hover:bg-white/10"}`}>Try it with demo patients</Link>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8">
          <h2 className="font-display text-[30px] font-medium">Good to know</h2>
          <dl className="mt-6 grid gap-6 md:grid-cols-3">
            <div><dt className="font-bold">It doesn't diagnose</dt><dd className="mt-1 text-ink-soft">Health information comes from MedlinePlus, and the assistant says so. Clinical judgment stays with your team.</dd></div>
            <div><dt className="font-bold">Providers make the hard calls</dt><dd className="mt-1 text-ink-soft">Anything a rule can't clear goes to a person. Tendwell AA gathers the context; it never overrides a no.</dd></div>
            <div><dt className="font-bold">It runs without the cloud AI bill</dt><dd className="mt-1 text-ink-soft">The rules-first concierge needs no language model at all, and the full agent runs on a model you host.</dd></div>
          </dl>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1180px] flex-wrap items-center justify-between gap-4 px-5 py-8 text-sm text-ink-soft sm:px-8">
          <Brand />
          <p>A Riches Digital Ventures product. In an emergency, call 911.</p>
        </div>
      </footer>
    </div>
  );
}
