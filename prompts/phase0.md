Read ./CLAUDE.md, /docs/schema.sql, and (since this is the first phase) /docs/build-playbook.md and
/docs/signals-api-spec.md. The database schema is already locked in /docs/schema.sql and will be
loaded into Supabase — do NOT design or alter tables. Do exactly this one phase:

--- PHASE 0 — Scaffold ---
Scaffold the project. Python 3.11 with uv. Two entry points: a `worker` package (scheduled
ingestion) and an `api` package (FastAPI, read-only). A shared `core` package for: config/env
loading, a Supabase client wrapper, and a SINGLE rate-limited HTTP client class that enforces a
global cap and attaches the SEC_USER_AGENT header on sec.gov requests. Add a .env.example with the
variables from playbook section 1.2, a Makefile with `run-worker` and `run-api` targets, a README,
a `/health` endpoint on the API, and a small documented step to apply /docs/schema.sql to Supabase.
Do NOT implement any signal logic yet. Then run the API and show me /health responding.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, and DEFINITION OF DONE. State what you're building and
which shared utilities everything will reuse, then go.