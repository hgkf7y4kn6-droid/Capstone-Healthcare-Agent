import { Moon, Sun, MonitorSmartphone } from "lucide-react";
import { useEffect, useState } from "react";

type Mode = "system" | "light" | "dark";
const KEY = "tw.theme";

export function applyStoredTheme() {
  try {
    const mode = (localStorage.getItem(KEY) as Mode) || "system";
    if (mode === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
  } catch {
    /* storage blocked: follow the system */
  }
}

export function ThemeToggle({ inverted = false }: { inverted?: boolean }) {
  const [mode, setMode] = useState<Mode>(() => {
    try { return (localStorage.getItem(KEY) as Mode) || "system"; } catch { return "system"; }
  });
  useEffect(() => {
    try { localStorage.setItem(KEY, mode); } catch { /* ignore */ }
    applyStoredTheme();
  }, [mode]);
  const next: Mode = mode === "system" ? "dark" : mode === "dark" ? "light" : "system";
  const Icon = mode === "dark" ? Moon : mode === "light" ? Sun : MonitorSmartphone;
  const label = { system: "Theme follows your device", dark: "Dark theme", light: "Light theme" }[mode];
  return (
    <button
      type="button"
      onClick={() => setMode(next)}
      className={`grid size-10 place-items-center rounded-full ${inverted ? "text-hero-soft hover:bg-white/10 hover:text-on-hero" : "text-ink-soft hover:bg-sunken"}`}
      aria-label={`${label}. Switch theme`}
      title={label}
    >
      <Icon size={18} />
    </button>
  );
}
