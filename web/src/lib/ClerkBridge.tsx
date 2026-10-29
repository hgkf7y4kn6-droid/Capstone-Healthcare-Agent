// Staff sign-in through Clerk. Loaded only when the server reports a Clerk key, so the
// demo and local-account builds don't ship it. Clerk owns the credentials, MFA and idle
// timeout; the server still decides the role (GET /api/auth/staff/me) and everything
// after sign-in goes through the same audited API.
import { ClerkProvider, SignIn as ClerkSignInForm, useAuth, useClerk } from "@clerk/clerk-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, session, attachClerk, type StaffSession } from "./api";

function Sync({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const clerk = useClerk();
  const tokenRef = useRef(getToken);
  tokenRef.current = getToken;
  const [state, setState] = useState<"loading" | "ready" | "no-role">("loading");
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setSlow(true), 10000);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    attachClerk(() => tokenRef.current(), () => clerk.signOut());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      session.setStaff(null);
      setState("ready");
      return;
    }
    let live = true;
    api.get<StaffSession>("/api/auth/staff/me", "staff")
      .then((me) => { if (live) { session.setStaff(me); setState("ready"); } })
      .catch((err: { status?: number }) => {
        if (!live) return;
        session.setStaff(null);
        setState(err.status === 403 ? "no-role" : "ready");
      });
    return () => { live = false; };
  }, [isLoaded, isSignedIn]);

  if (state === "loading") {
    return (
      <div className="grid min-h-svh place-items-center px-6 text-center text-ink-soft" role="status">
        {slow ? (
          <div>
            <p>Sign-in is taking too long to load. Check your connection.</p>
            <button className="btn btn-primary mt-4" onClick={() => window.location.reload()}>Try again</button>
          </div>
        ) : "Loading…"}
      </div>
    );
  }
  if (state === "no-role") {
    return (
      <div className="grid min-h-svh place-items-center px-6 text-center">
        <div className="max-w-[44ch]">
          <h1 className="font-display text-[34px] font-medium">You're signed in, but not set up yet</h1>
          <p className="mt-2 text-ink-soft">An admin needs to give your account a role before you can open the console. Ask them, then sign in again.</p>
          <button className="btn btn-primary mt-6" onClick={() => void clerk.signOut()}>Sign out</button>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

export function ClerkBridge({ publishableKey, children }: { publishableKey: string; children: ReactNode }) {
  return (
    <ClerkProvider publishableKey={publishableKey} afterSignOutUrl="/sign-in">
      <Sync>{children}</Sync>
    </ClerkProvider>
  );
}

export default function ClerkSignIn() {
  return <ClerkSignInForm routing="hash" fallbackRedirectUrl="/console" signUpFallbackRedirectUrl="/console" />;
}
