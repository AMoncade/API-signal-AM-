# Project: Corporate Intent Intelligence API

Before writing code in any session, read: `/docs/schema.sql` (the database contract),
`/docs/signals-api-spec.md` (what/why), and `/docs/build-playbook.md` (the phased how).
Then inspect the repo and read the ACTUAL existing code — it is the source of truth for
what's already built, not the docs and not your memory.

## What this is
A B2B signals API built from two FREE public sources — SEC EDGAR (Form D + 8-K) and public
ATS job boards (Greenhouse + Lever). We sell interpreted signals and the JOIN between funding
and hiring, not raw data.

## Stack (do not change without asking)
- Python 3.11, `uv` for deps.
- Two components: a scheduled ingestion `worker` and a read-only FastAPI `api`.
- Supabase Postgres. Shared `core` package: config, Supabase client, and ONE rate-limited
  HTTP client used by everything.
- Store EXTRACTED SIGNALS ONLY. Never persist raw filing text or raw job-description text.
  (The only derived free-text allowed is the <=240-char 8-K summary.)

## CONTRACTS — these keep the phases working together
- `/docs/schema.sql` is the SINGLE SOURCE OF TRUTH for the database. Do not invent or rename
  tables/columns. If a phase truly needs a change, edit schema.sql as ONE explicit change and
  state what changed and why.
- CIK is the CANONICAL company key. Everything that can join, joins on `companies.cik`.
- TWO UNIVERSES: Form D + jobs = operating startups (the `companies` table, FK'd to it).
  8-K = public companies, a DIFFERENT set — `eight_k_events` has NO FK to companies.
- The JOIN lives in ONE place: the `company_velocity` and `funded_and_hiring` SQL views.
  Read from the views; never re-implement the surging/join logic in application code.
- Reuse the shared rate-limited HTTP client and Supabase wrapper. Never write parallel ones.

## HARD RULES (violating these breaks the product)
1. EDGAR: <=10 requests/sec across ALL sec.gov domains; ALWAYS send SEC_USER_AGENT.
   Missing UA = 403. Rate-limit centrally in the shared client.
2. Form D parsing is PURE XML — no LLM.
3. Form D `total_amount_sold` is CUMULATIVE; D/A amendments restate it. Dedupe by cik and
   store the INCREMENTAL amount (`incremental_amount_sold_usd`) by diffing the prior filing.
4. `total_offering_amount` is often the literal "Indefinite" — keep it in `*_raw`, leave the
   numeric column NULL. Never coerce to 0.
5. FILTER OUT pooled investment funds before treating anything as a funded startup
   (`is_pooled_fund` / industry group). Funds dominate the feed.
6. Form D has NO website field. The domain must be DERIVED (name + state). Expect failures.
7. PII: business names only. Strip all personal names everywhere.
8. 8-K: set `event_type` from the structured Item codes FIRST (deterministic, free); use the
   hosted LLM only to extract specifics + severity from prose.
9. 8-K classification uses a HOSTED model (ANTHROPIC_API_KEY), not a local model.
10. Job velocity = pure counting of daily snapshots. No LLM. NO BACKFILL — keep the worker
    running so history accumulates.
11. JD migrations = cheap regex prefilter THEN local/cheap LLM only on hits. Low-confidence
    ENRICHMENT only (`company_tech_signals`) — never a flagship endpoint.

## DEFINITION OF DONE — applies to EVERY phase
A phase is NOT done until ALL of these are true and shown:
- It writes automated tests that assert against REAL saved fixtures — e.g. real Form D
  `primary_doc.xml` files with known correct amounts, a known company's known posting count,
  a known 8-K's expected classification. NOT mocks of our own assumptions.
- Those tests RUN and PASS, and you show me the passing output.
- An INTEGRATION check proves this phase's output is consumable by the next phase against the
  schema: the columns/keys it writes are exactly what the downstream phase or view reads.
- Ingestion phases log the run to `ingestion_runs`.
Then append non-obvious learnings to NOTES below and remind me to commit.

## HUMAN CHECKPOINTS — do NOT skip (these need external ground truth a model can't supply)
- After PHASE 1 (Form D): STOP. Give me one real filing's URL plus the parsed row, so I can
  confirm the numbers match the actual SEC filing before anything is built on top.
- After the INTEGRATION PASS: STOP. Show me the `funded_and_hiring` output so I can confirm
  it is non-empty and plausible.
All OTHER phases: once the real-fixture tests pass, proceed and commit; just summarize what
you did. Do not wait for me to review routine phases.

## Endpoints (5)
`/signals/pre-announced-funding`, `/signals/surging-velocity`, `/signals/material-risks`,
`/signals/funded-and-hiring` (the headline JOIN), and `migrations` as an enrichment field.

## Style
Small, testable modules. Defensive parsing (fields may be missing / "Indefinite" / null).
Always show tests actually running — never claim something works without showing it run.

## NOTES — append durable, non-obvious learnings here as phases complete
- Phase 0 (scaffold): dev box had NO `uv` and NO Python 3.11 (only 3.12/3.10/3.9).
  Installed `uv` (per-user, `C:\Users\felix\.local\bin`) and it provisions a managed
  CPython 3.11.15; `.python-version` pins 3.11 so `uv sync` always uses it.
- `make` is NOT installed on this Windows box. The Makefile is kept for parity, but
  the real commands are `uv run ...` (uvicorn for the API, `python -m worker`). README
  documents both.
- The single shared client (`core/http_client.py`) enforces the global ≤10 req/s cap by
  holding the limiter lock ACROSS the throttle sleep — deliberate, so two threads can't
  each independently decide they're under the cap. Reuse this client everywhere (HARD RULE).
- SEC_USER_AGENT is enforced at header-prep time and only for `*.sec.gov` hosts; a sec.gov
  request with no UA is refused locally (clear error) instead of getting a 403 at the wire.
  Look-alikes (`notsec.gov`, `sec.gov.evil.com`) are correctly treated as non-SEC.
- `/health` is intentionally DB-free so an uptime probe never depends on Supabase.
- FastAPI's `TestClient` emits a `StarletteDeprecationWarning` (httpx → httpx2) — cosmetic,
  tests pass.
