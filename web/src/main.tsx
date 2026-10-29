import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import "./index.css";
import { MoreMenu, Shell } from "./components/Shell";
import type { PublicConfig } from "./lib/api";
import { applyStoredTheme } from "./components/ThemeToggle";
import { Inbox } from "./pages/Inbox";
import { InsightsPage } from "./pages/Insights";
import { Landing } from "./pages/Landing";
import { PatientDetail } from "./pages/PatientDetail";
import { Patients } from "./pages/Patients";
import { Portal } from "./pages/Portal";
import { Refills } from "./pages/Refills";
import { Schedule } from "./pages/Schedule";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { Today } from "./pages/Today";

applyStoredTheme();

function NotFound() {
  return (
    <div className="grid min-h-svh place-items-center px-6 text-center">
      <div>
        <p className="font-display text-[44px]">This page isn't here.</p>
        <p className="mt-2 text-ink-soft">The link may be old. Head back to the start.</p>
        <Link to="/" className="btn btn-primary mt-6">Go to AegisTriage</Link>
      </div>
    </div>
  );
}

async function boot() {
  // When the server has Clerk configured, staff sign in through it; otherwise the local/demo form is used.
  let key: string | null = null;
  try {
    const cfg = (await (await fetch("/api/public/config")).json()) as PublicConfig;
    key = cfg.clerk?.publishable_key ?? null;
  } catch {
    /* offline or server down: render the app and let each screen report it */
  }
  const app = (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/sign-in" element={<SignIn />} />
        <Route path="/portal" element={<Portal />} />
        <Route path="/console" element={<Shell />}>
          <Route index element={<Today />} />
          <Route path="patients" element={<Patients />} />
          <Route path="patients/:id" element={<PatientDetail />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="refills" element={<Refills />} />
          <Route path="inbox" element={<Inbox />} />
          <Route path="insights" element={<InsightsPage />} />
          <Route path="settings" element={<Settings />} />
          <Route path="more" element={<MoreMenu />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </BrowserRouter>
  );
  const root = createRoot(document.getElementById("root")!);
  if (key) {
    const { ClerkBridge } = await import("./lib/ClerkBridge");
    root.render(<StrictMode><ClerkBridge publishableKey={key}>{app}</ClerkBridge></StrictMode>);
  } else {
    root.render(<StrictMode>{app}</StrictMode>);
  }
}

void boot();
