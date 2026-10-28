# Tendwell AA — notes for coding agents

Read `README.md` first; the design system is `docs/design-system.md`.

- **Commands**: `python -m unittest discover -s tests` (no network, no model); in `web/`: `npm test`, `npm run lint`, `npm run build`. Run all of them before calling a change done.
- **Where logic goes**:
  - Clinical and safety rules are deterministic and live in `workflows/` (refill gates, safety screen, support triage). Agents in `agents/` only write language over rule outcomes; they never decide whether a refill goes out or whether something is an emergency.
  - `agents/assistant.py` is the single entry point for the patient assistant: safety screen first, then the engine. Don't call `run_assistant` or `run_concierge` from routes directly.
  - `api/app.py` is transport only. New rules go in `workflows/`, shared by the API, the CrewAI tools and the ops console.
  - Every LLM-dependent path degrades to deterministic behavior when CrewAI/transformers are missing.
- **Privacy boundaries**:
  - Staff responses never include SSN digits (`_staff_patient`). Patient responses (`/api/me/*`) never include identity factors or staff/clinical notes (`PATIENT_VISIBLE_TYPES` in the concierge).
  - A patient token can only touch its own records; return 404 for anything else.
  - Don't log message text for safety-screen matches; don't commit `data/patients.db` or `logs/`.
- **Versioning**: every push that changes the app or server first runs `python -m scripts.bump_version` and commits `version.py`, `web/package.json` and `web/package-lock.json`. Docs-only changes don't bump.
- **UI**:
  - Colors come from `web/src/tokens.ts` mirrored in `web/src/index.css`; never hard-code hex in screens. Add a token to both and the token test keeps them honest.
  - Lead each console screen with one hero (or a `PageHeader`), then quick actions, then detail. Lanes (`lane lane-done|review|schedule|urgent`) mark where a request was routed — they're the one bold device.
  - Sentence-case copy, verb-first buttons, no all-caps labels. Give interactive elements accessible names.
