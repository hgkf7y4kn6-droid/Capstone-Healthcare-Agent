import type { ReactNode } from "react";
import { chipClass, type Lane } from "../lib/lanes";

export function Chip({ lane = "neutral", children }: { lane?: Lane | "neutral"; children: ReactNode }) {
  return <span className={`chip ${chipClass[lane]}`}>{children}</span>;
}

export function PageHeader({ title, lead, actions }: { title: string; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="font-display text-[34px] leading-[1.1] font-medium tracking-tight sm:text-[40px]">{title}</h1>
        {lead && <p className="mt-1.5 max-w-[62ch] text-ink-soft">{lead}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className} aria-label={title}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-[17px] font-bold">{title}</h2>
        {aside && <div className="text-sm text-ink-soft">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function EmptyState({ icon, title, body, action }: { icon: ReactNode; title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-panel border border-dashed border-line px-5 py-7">
      <span className="grid size-11 place-items-center rounded-row bg-ceil text-ceil-ink" aria-hidden>{icon}</span>
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mt-0.5 max-w-[56ch] text-ink-soft">{body}</p>
      </div>
      {action}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-row bg-urgent-soft px-4 py-3 text-urgent">
      <span className="font-semibold">{message}</span>
      {onRetry && <button className="btn btn-quiet min-h-9 px-4" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-14 animate-pulse rounded-row bg-sunken" />
      ))}
    </div>
  );
}

export function Stat({ label, value, lane }: { label: string; value: ReactNode; lane?: Lane }) {
  return (
    <div className={`lane ${lane ? `lane-${lane}` : ""}`}>
      <div className="font-display text-[38px] leading-none font-medium">{value}</div>
      <div className="mt-1.5 text-sm text-hero-soft">{label}</div>
    </div>
  );
}
