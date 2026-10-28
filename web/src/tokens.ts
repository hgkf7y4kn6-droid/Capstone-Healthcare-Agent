/**
 * Tendwell design tokens — "Scrubs & Ceil". The single source for color,
 * radius, type and motion. index.css mirrors the colors as CSS variables for
 * Tailwind; tests/tokens.test.mjs fails if they drift or contrast drops.
 * See docs/design-system.md.
 */
export const palette = {
  light: {
    bg: "#f3f6f4",
    surface: "#ffffff",
    sunken: "#e8eeeb",
    ink: "#0f2a22",
    inkSoft: "#4a5f57",
    line: "#d3ddd8",
    primary: "#1f5c48",
    onPrimary: "#ffffff",
    ceil: "#b9c6ec",
    ceilInk: "#33447a",
    hero: "#12392d",
    onHero: "#f3f6f4",
    heroSoft: "#a9c4b9",
    heroDone: "#79d8a6",
    heroReview: "#f2b766",
    heroSchedule: "#b9c6ec",
    heroUrgent: "#ff8e84",
    done: "#17613f",
    doneSoft: "#dcefe3",
    review: "#8a4b00",
    reviewSoft: "#f8ead6",
    schedule: "#33447a",
    scheduleSoft: "#e3e8f8",
    urgent: "#a8231c",
    urgentSoft: "#f9e1df",
  },
  dark: {
    bg: "#0b1613",
    surface: "#12201c",
    sunken: "#0f1b18",
    ink: "#e4eee9",
    inkSoft: "#9db3aa",
    line: "#23362f",
    primary: "#7fd1ae",
    onPrimary: "#06231a",
    ceil: "#2a3766",
    ceilInk: "#b9c6ec",
    hero: "#102e25",
    onHero: "#e4eee9",
    heroSoft: "#9fbdb1",
    heroDone: "#79d8a6",
    heroReview: "#f2b766",
    heroSchedule: "#b9c6ec",
    heroUrgent: "#ff8e84",
    done: "#79d8a6",
    doneSoft: "#12301f",
    review: "#f2b766",
    reviewSoft: "#33260f",
    schedule: "#b9c6ec",
    scheduleSoft: "#1f2a4d",
    urgent: "#ff8e84",
    urgentSoft: "#3a1714",
  },
} as const;

export const radius = { row: 10, panel: 14, hero: 22, pill: 999 } as const;
export const motion = { fast: 160, base: 280, slow: 560, stagger: 60, ease: "cubic-bezier(0.16, 1, 0.3, 1)" } as const;
export const fonts = { display: "Newsreader Variable", ui: "Atkinson Hyperlegible Next" } as const;
