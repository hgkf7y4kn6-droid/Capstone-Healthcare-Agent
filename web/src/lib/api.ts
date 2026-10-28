// Thin fetch wrapper. Staff and patient sessions live in sessionStorage so
// closing the tab signs out — a shared front-desk computer shouldn't stay signed in.

const STAFF_KEY = "tw.staff";
const PATIENT_KEY = "tw.patient";

export type StaffSession = { token: string; name: string; role: string; expires_at: number };
export type PatientSession = { token: string; patient: { patient_id: number; full_name: string; first_name: string } };

function read<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}
function write(key: string, value: unknown) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: the session just won't survive a reload */
  }
}

export const session = {
  staff: () => {
    const s = read<StaffSession>(STAFF_KEY);
    return s && s.expires_at * 1000 > Date.now() ? s : null;
  },
  setStaff: (s: StaffSession | null) => write(STAFF_KEY, s),
  patient: () => read<PatientSession>(PATIENT_KEY),
  setPatient: (s: PatientSession | null) => write(PATIENT_KEY, s),
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit & { as?: "staff" | "patient" } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const token = init.as === "staff" ? session.staff()?.token : init.as === "patient" ? session.patient()?.token : null;
  if (token) headers.set("authorization", `Bearer ${token}`);
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "Can't reach AegisTriage. Check your connection and try again.");
  }
  if (res.status === 401 && init.as === "staff") {
    session.setStaff(null);
    window.dispatchEvent(new Event("tw:signed-out"));
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail) ? "Some fields need another look." : `Request failed (${res.status}).`;
    throw new ApiError(res.status, message);
  }
  return data as T;
}

export const api = {
  get: <T,>(path: string, as?: "staff" | "patient") => request<T>(path, { as }),
  post: <T,>(path: string, body: unknown, as?: "staff" | "patient") =>
    request<T>(path, { method: "POST", body: JSON.stringify(body), as }),
  patch: <T,>(path: string, body: unknown, as?: "staff" | "patient") =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body), as }),
};
