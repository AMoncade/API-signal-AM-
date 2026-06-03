# Signals API — Corporate Intent Intelligence

Interpreted B2B **signals** built from two free public sources — SEC EDGAR
(Form D + 8-K) and public ATS job boards (Greenhouse + Lever). The product is the
normalization, classification, and the **join** between funding and hiring — not
the raw data. See `docs/signals-api-spec.md` (what/why) and
`docs/build-playbook.md` (the phased how). `docs/schema.sql` is the single source
of truth for the database.

## Build progress

We build phase by phase (see `docs/build-playbook.md` §5 and `prompts/`). Current
state:

| Phase | Scope | Status |
|---|---|---|
| **0 — Scaffold** | Packages, shared `core` (config + Supabase wrapper + rate-limited HTTP client), `/health`, tests | ✅ Done |
| 1 — Form D ingestion + parser | Pull D/D-A filings, parse XML, store funding signals | ⬜ Not started |
| 2 — Domain derivation + ATS seeding | Name+state → domain → Greenhouse/Lever board token | ⬜ Not started |
| 3 — Job snapshots + velocity | Daily open-posting counts → surging-velocity signal | ⬜ Not started |
| 4 — 8-K ingestion + classification | Item codes → event type; hosted LLM for specifics/severity | ⬜ Not started |
| 5 — Read API + RapidAPI auth | The 5 signal endpoints, proxy-secret middleware | ⬜ Not started |
| 5.5 — Integration pass | End-to-end check; `funded_and_hiring` returns rows | ⬜ Not started |
| 6 — Schedule + deploy | Worker on a schedule, Dockerfiles, deploy | ⬜ Not started |
| 7 — Migrations enrichment | Regex prefilter → cheap LLM; low-confidence field | ⬜ Not started |

> **Now: Phase 0 complete.** Runnable skeleton + healthcheck only, no signal
> logic yet. Next up is Phase 1 (Form D ingestion). Update this table as each
> phase lands.

## Architecture

Two runnable components share one `core` package and one Supabase database:

| Component | What it is | Entry point |
|---|---|---|
| `worker` | Scheduled ingestion (pull → parse/classify → write signals) | `python -m worker` |
| `api` | Read-only FastAPI service serving the signal endpoints | `uvicorn api.main:app` |
| `core` | **Shared** config, Supabase client wrapper, and the ONE rate-limited HTTP client | imported by both |

The `core` package holds the utilities **everything reuses** — never write a
parallel HTTP client or config loader:

- `core/config.py` — typed env loading (`get_settings()`), reads `.env` in dev.
- `core/http_client.py` — the single rate-limited `HttpClient`. Enforces a
  **global** request cap (default 10 req/s) and attaches the mandatory
  `SEC_USER_AGENT` header on every `*.sec.gov` request (missing UA = 403).
- `core/supabase_client.py` — `get_supabase()`, a service-role client wrapper.

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — the package/Python manager. Install:
  - Windows (PowerShell): `irm https://astral.sh/uv/install.ps1 | iex`
  - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Python 3.11** — `uv` provisions it automatically (pinned in `.python-version`).
  If needed: `uv python install 3.11`.
- A **Supabase** project (URL + service-role key) — needed once you ingest/serve
  real data; not required to boot `/health`.

## Setup

```bash
# 1. Install dependencies into a 3.11 venv
uv sync --extra dev          # or: make install

# 2. Configure secrets
cp .env.example .env         # then edit .env
```

`.env` variables are documented in `.env.example` (playbook §1.2). Only
`SEC_USER_AGENT` is needed for EDGAR; Supabase/Anthropic keys come into play in
later phases.

## Run

```bash
# Read API — serves the healthcheck at http://localhost:8000/health
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload   # or: make run-api

# Ingestion worker (Phase 0: wires up the shared client, no jobs yet)
uv run python -m worker                                            # or: make run-worker
```

Check health:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"signals-api","version":"0.1.0"}
```

> On Windows, `make` is usually not installed — use the `uv run ...` commands
> directly (they are what the Makefile targets call).

## Tests

```bash
uv run pytest                # or: make test
```

The Phase 0 tests assert **real behavior** of the scaffold: the global rate cap
actually throttles, the `SEC_USER_AGENT` header is attached only to `sec.gov`
requests (and a sec.gov request without a UA is refused), and `/health` returns
200. (The "tests against real saved fixtures" rule in CLAUDE.md applies to the
signal phases, which have no data yet.)

## Apply the database schema to Supabase

`docs/schema.sql` is the **single source of truth** for the database — do not
edit table/column definitions ad hoc. To load it:

1. Create a Supabase project and copy its **Project URL** and **service-role
   key** into `.env` (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`).
2. In the Supabase dashboard: **SQL Editor → New query**, paste the entire
   contents of `docs/schema.sql`, and click **Run**. It is idempotent
   (`create table if not exists` / `create or replace`), so re-running is safe.
3. (Optional CLI alternative) From the project's Postgres connection string
   (Supabase → Project Settings → Database):
   ```bash
   psql "$SUPABASE_DB_URL" -f docs/schema.sql
   ```

The schema enables RLS with no anon policies, so the tables are not publicly
readable — the worker and API reach them via the service-role key, which
bypasses RLS.

## Project layout

```
core/        shared config, Supabase wrapper, rate-limited HTTP client
api/         read-only FastAPI app (/health for now)
worker/      scheduled ingestion entry point (scaffold)
tests/       Phase 0 behavior tests
docs/        schema.sql (DB contract), spec, build playbook
prompts/     the per-phase build prompts
```
