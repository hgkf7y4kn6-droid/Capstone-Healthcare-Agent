export function Mark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className={className}>
      <rect width="64" height="64" rx="16" fill="var(--hero)" />
      <path d="M32 14v31c0 4 2.5 6 6.5 6" fill="none" stroke="var(--on-hero)" strokeWidth="6.5" strokeLinecap="round" />
      <path d="M19 27h9l3-5 5 10 3-5h6" fill="none" stroke="#b9c6ec" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Brand({ inverted = false }: { inverted?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <Mark size={30} className={inverted ? "rounded-[9px] ring-1 ring-hero-soft/40" : ""} />
      <span className={`font-display text-[22px] leading-none font-semibold tracking-tight ${inverted ? "text-on-hero" : "text-ink"}`}>
        Tendwell
      </span>
    </span>
  );
}
