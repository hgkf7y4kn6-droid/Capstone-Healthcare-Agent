import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Brand } from "../components/Brand";
import { ErrorNote } from "../components/ui";
import { api, session, type StaffSession } from "../lib/api";
import { greeting } from "../lib/format";

type PublicConfig = { clinic: { name: string }; demo: { enabled: boolean; access_code: string | null } };

export function SignIn() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (session.staff()) navigate("/console", { replace: true });
    api.get<PublicConfig>("/api/public/config").then((c) => {
      setCfg(c);
      if (params.get("demo") && c.demo.enabled && c.demo.access_code) {
        setCode(c.demo.access_code);
        setName((n) => n || "Demo front desk");
      }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      session.setStaff(await api.post<StaffSession>("/api/auth/staff", { code, name }));
      navigate("/console");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-svh lg:grid-cols-[1fr_1.1fr]">
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-hero p-10 text-on-hero lg:flex">
        <Link to="/" aria-label="AegisTriage home"><Brand inverted /></Link>
        <div>
          <p className="max-w-[18ch] font-display text-[46px] leading-[1.05]">{greeting()}. Here's what came in overnight.</p>
          <p className="mt-4 max-w-[42ch] text-hero-soft">
            Refills that cleared your rules are already at the pharmacy. The rest are sorted by who needs to look at them.
          </p>
        </div>
        <p className="text-sm text-hero-soft">{cfg?.clinic.name ?? "Your clinic"}</p>
      </aside>

      <main className="flex flex-col px-5 py-8 sm:px-10">
        <div className="lg:hidden"><Link to="/"><Brand /></Link></div>
        <div className="m-auto w-full max-w-[400px] py-10">
          <h1 className="font-display text-[36px] font-medium">Sign in to the console</h1>
          <p className="mt-1 text-ink-soft">Use the access code your practice manager gave you.</p>

          <form onSubmit={submit} className="mt-8 space-y-4">
            <label className="block">
              <span className="mb-1.5 block font-semibold">Your name</span>
              <input className="field" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Shown in the activity log" />
            </label>
            <label className="block">
              <span className="mb-1.5 block font-semibold">Access code</span>
              <input className="field" type="password" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="current-password" required />
            </label>
            {error && <ErrorNote message={error} />}
            <button className="btn btn-primary w-full" disabled={busy || !code}>{busy ? "Signing in…" : "Sign in"}</button>
          </form>

          {cfg?.demo.enabled && cfg.demo.access_code && (
            <div className="mt-8 rounded-row bg-ceil px-4 py-3 text-ceil-ink">
              <p className="font-semibold">Exploring the demo?</p>
              <p className="mt-0.5 text-sm">
                The access code is <code className="font-bold">{cfg.demo.access_code}</code>. Every patient here is synthetic.
              </p>
              <button type="button" className="mt-2 text-sm font-bold underline underline-offset-2" onClick={() => { setCode(cfg.demo.access_code!); setName((n) => n || "Demo front desk"); }}>
                Fill it in for me
              </button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
