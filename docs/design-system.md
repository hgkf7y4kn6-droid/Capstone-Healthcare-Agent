# Design system: "Scrubs & Ceil"

Tendwell AA's look is its own. It's drawn from the clinic floor — hunter-green and ceil-blue scrubs, triage tags, the paper day sheet at the front desk — and borrows proven ideas from the sibling projects:

- **Mindful Money Management:** a palette tied to the logo, a branded sign-in, a time-of-day greeting, one summary hero per screen and inviting empty states.
- **DFS Riches:** the inverted hero panel with stat tiles, a "Start here" checklist for first-run setup, and tinted status chips.
- **Pool PM:** deterministic rules decide and agents only write language over them; tokens are the single source of color; lead each screen with a hero, then quick actions, then detail; clients (here, patients) never receive internal fields.
- **The Riches Digital Ventures site:** a serif display face for headlines and big figures, a single orchestrated reveal, and a visible keyboard focus ring everywhere.

Sources of truth:

| What | Where |
|---|---|
| Color, radius, type and motion tokens | `web/src/tokens.ts` |
| Tailwind theme (CSS variables, light + dark) | `web/src/index.css` — `web/tests/tokens.test.mjs` fails if the two drift or contrast drops |
| Brand mark | `web/public/mark.svg`, drawn in-app by `components/Brand.tsx` |
| Lanes (routing outcomes) | `web/src/lib/lanes.ts` |

## The signature: lanes

Everything Tendwell AA does ends in a **lane** — where a request was routed. Lanes are shown as a 4px colored rule on the leading edge of a row, like the colored band on a triage tag. Lanes are the one bold device; everything around them stays quiet.

| Lane | Meaning | Token |
|---|---|---|
| Handled | Cleared every rule and finished on its own (refill sent, thank-you answered) | `lane-done` |
| Needs a person | A rule stopped it; staff or a provider decides | `lane-review` |
| Scheduled | An appointment request or booking | `lane-schedule` |
| Urgent | The safety screen matched; emergency guidance was shown | `lane-urgent` |

## Color

Cool clinic white (not cream), hunter-green ink for text and the hero, ceil blue as the single accent. Dark mode is "night shift": a dim green-black with mint and ceil.

| Token | Light | Dark | Use |
|---|---|---|---|
| `bg` | #f3f6f4 | #0b1613 | Page |
| `surface` / `sunken` | #ffffff / #e8eeeb | #12201c / #0f1b18 | Panels, insets, tracks |
| `ink` / `ink-soft` | #0f2a22 / #4a5f57 | #e4eee9 / #9db3aa | Text |
| `line` | #d3ddd8 | #23362f | Hairlines |
| `primary` / `on-primary` | #1f5c48 / #ffffff | #7fd1ae / #06231a | Brand, primary buttons, links |
| `ceil` / `ceil-ink` | #b9c6ec / #33447a | #2a3766 / #b9c6ec | The accent: selection, focus, the hero's highlights |
| `hero` / `on-hero` / `hero-soft` | #12392d / #f3f6f4 / #a9c4b9 | #102e25 / … | The day sheet panel, the rail |
| `done`, `review`, `schedule`, `urgent` (+ `-soft`) | | | Lanes and status chips; always a tinted fill with full-strength text |

Contrast minimums, enforced by the token test in both themes: body text 7:1, secondary text 4.5:1, chips 4.5:1, hero text 7:1.

## Type

| Role | Face |
|---|---|
| Headlines, hero figures, the patient's name | **Newsreader** (variable, optical sizes) — feels like a clinical letter, not a dashboard |
| Everything else | **Atkinson Hyperlegible Next** — designed by the Braille Institute for low-vision legibility, which is the point in a clinic |

Body is 16px minimum; figures use tabular numerals. Labels are sentence case — no all-caps eyebrows.

## Shape and depth

Radius encodes hierarchy instead of being one value everywhere: the hero is 22px, panels 14px, rows and inputs 10px, chips and buttons are pills. Panels sit on a hairline with no drop shadow; only floating things (menus, the patient composer) get a shadow.

## Motion

One orchestrated moment per surface: the landing page routes four sample messages into their lanes once on load; the console's day sheet counts up once. Everything else moves only in answer to the person — a refill result slides into place, a decided review collapses out of the list. `prefers-reduced-motion` turns all of it off.

## Voice

Plain verbs, sentence case, the person's own words for things ("Request a refill", not "Submit refill"). An action keeps its name through the flow ("Approve" → "Approved"). Errors say what happened and what to do next. Empty states say what will appear and offer the action that creates it. Tendwell AA never claims to diagnose and always keeps 911 one tap away for patients.
