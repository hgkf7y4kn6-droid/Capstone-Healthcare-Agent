"""
Central configuration for AegisTriage (the agentic healthcare assistant).

All secrets are read from environment variables. NEVER hardcode API keys
in this file or anywhere else in the codebase.

Set these before running the app, e.g. in a `.env` file (see .env.example)
or your shell:

    export CALCOM_API_KEY="cal_live_..."

The LLM itself needs NO API key or account — it runs locally via
transformers (see agents/llm.py). If `python-dotenv` is installed (it is in
requirements.txt) a `.env` file is loaded automatically.
"""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is a convenience, never a hard requirement
    pass

# ---- Product ----
APP_NAME = os.getenv("APP_NAME", "AegisTriage")
APP_ENV = os.getenv("APP_ENV", "development")  # development | staging | production
CLINIC_NAME = os.getenv("CLINIC_NAME", "Horsham Family Health")
CLINIC_PHONE = os.getenv("CLINIC_PHONE", "(215) 555-0142")
# Demo mode loads the synthetic patient roster on first start and shows the
# staff access code on the sign-in screen. Never enable it with real patient data.
DEMO_MODE = os.getenv("DEMO_MODE", "false" if APP_ENV == "production" else "true").lower() in ("1", "true", "yes")

# ---- Local LLM (no key needed) ----
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
LOCAL_LLM_DEVICE = os.getenv("LOCAL_LLM_DEVICE", "cpu")
LOCAL_LLM_TEMPERATURE = float(os.getenv("LOCAL_LLM_TEMPERATURE", "0.2"))
LOCAL_LLM_MAX_NEW_TOKENS = int(os.getenv("LOCAL_LLM_MAX_NEW_TOKENS", "512"))

# Which engine answers the patient assistant:
#   auto      -> CrewAI + local model when installed, otherwise the deterministic concierge
#   crew      -> always CrewAI (errors surface if it's unavailable)
#   concierge -> always the deterministic concierge (fast, free, predictable — good for hosting)
ASSISTANT_ENGINE = os.getenv("ASSISTANT_ENGINE", "auto" if APP_ENV == "development" else "concierge").lower()

# ---- Integrations ----
CALCOM_API_KEY = os.getenv("CALCOM_API_KEY", "")
CALCOM_BASE_URL = os.getenv("CALCOM_BASE_URL", "https://api.cal.com/v2")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ---- Patient-portal JWTs (verified, never issued, in production) ----
# Development falls back to a fixed, clearly-insecure secret so the patient
# demo works out of the box; production must set its own.
_DEV_ONLY = "dev-only-insecure-secret-do-not-use-in-production"
PORTAL_JWT_SECRET = os.getenv("PORTAL_JWT_SECRET", "" if APP_ENV == "production" else _DEV_ONLY)
PORTAL_JWT_ALGORITHM = os.getenv("PORTAL_JWT_ALGORITHM", "HS256")
PORTAL_JWT_PUBLIC_KEY = os.getenv("PORTAL_JWT_PUBLIC_KEY", "")
PORTAL_WEBHOOK_URL = os.getenv("PORTAL_WEBHOOK_URL", "")
PORTAL_WEBHOOK_SECRET = os.getenv("PORTAL_WEBHOOK_SECRET", "")

# ---- Staff console sessions ----
# SESSION_SECRET gates staff sign-in (production refuses it unset). Staff use
# individual accounts with MFA (see api/staff_auth.py and scripts/create_staff_user.py);
# STAFF_ACCESS_CODE is the shared code for the synthetic-data demo and works only
# when DEMO_MODE is on.
SESSION_SECRET = os.getenv("SESSION_SECRET", "" if APP_ENV == "production" else _DEV_ONLY)
STAFF_ACCESS_CODE = os.getenv("STAFF_ACCESS_CODE", "" if APP_ENV == "production" else "aegistriage-demo")
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "8"))
STAFF_IDLE_MINUTES = int(os.getenv("STAFF_IDLE_MINUTES", "15"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

# ---- Async / queues ----
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", ""))
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
AWS_SQS_QUEUE_URL = os.getenv("AWS_SQS_QUEUE_URL", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# ---- Paths ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOGS_DIR = os.getenv("LOGS_DIR", os.path.join(BASE_DIR, "logs"))
PATIENT_DB_PATH = os.getenv("PATIENT_DB_PATH", os.path.join(DATA_DIR, "patients.db"))
VECTOR_STORE_DIR = os.getenv("VECTOR_STORE_DIR", os.path.join(DATA_DIR, "vector_store"))
AGENT_LOG_PATH = os.path.join(LOGS_DIR, "agent_traces.jsonl")
EVAL_LOG_PATH = os.path.join(LOGS_DIR, "eval_results.jsonl")
MONITORING_LOG_PATH = os.path.join(LOGS_DIR, "monitoring.jsonl")
WEB_DIST_DIR = os.getenv("WEB_DIST_DIR", os.path.join(BASE_DIR, "web", "dist"))

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


def is_production() -> bool:
    return APP_ENV == "production"


def require_keys(*names: str) -> list[str]:
    """Return a list of missing required env var names (does not raise).
    The LLM itself is never in this list — it's local and needs no key."""
    lookup = globals()
    missing = []
    for name in names:
        if not (lookup.get(name) or os.getenv(name)):
            missing.append(name)
    return missing
