import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import "./index.css";
import { MoreMenu, Shell } from "./components/Shell";
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
        <Link to="/" className="btn btn-primary mt-6">Go to Tendwell</Link>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
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
  </StrictMode>,
);
