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
| 1 — Form D ingestion + parser | Pull D/D-A filings, parse XML, store funding signals | ✅ Done |
| 2 — Domain derivation + ATS seeding | Name+state → domain → Greenhouse/Lever board token | ✅ Done |
| 3 — Job snapshots + velocity | Daily open-posting counts → surging-velocity signal | ✅ Done |
| 4 — 8-K ingestion + classification | Item codes → event type; hosted LLM for specifics/severity | ✅ Done |
| 5 — Read API + RapidAPI auth | The 5 signal endpoints, proxy-secret middleware | ✅ Done |
| 5.5 — Integration pass | End-to-end check; `funded_and_hiring` returns rows | ✅ Done |
| 6 — Schedule + deploy | Worker on a schedule, Dockerfiles, deploy | ✅ Done |
| 7 — Migrations enrichment | Regex prefilter → cheap LLM; low-confidence field | ✅ Done |

> **Now: all phases (0-7) implemented and tested.** The full pipeline runs end to
> end and the headline `funded_and_hiring` join returns rows in the integration
> check. 81 tests pass against real saved EDGAR/ATS fixtures. Live, credential-free
> demos: `python -m worker form_d|seed|snapshot|eight_k|migrations --dry-run`.
> Going live needs external accounts: a Supabase project (load `docs/schema.sql`),
> an `ANTHROPIC_API_KEY` for the 8-K signal, and a 24/7 host (see `docs/DEPLOY.md`).

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

# Ingestion worker — jobs (each has a credential-free --dry-run demo):
uv run python -m worker form_d   --dry-run --limit 25   # Phase 1: parse live Form D filings
uv run python -m worker seed     --dry-run --limit 60   # Phase 2: derive domains + match ATS boards
uv run python -m worker snapshot --dry-run --limit 60   # Phase 3: ingest -> seed -> snapshot counts
uv run python -m worker eight_k  --dry-run --limit 40   # Phase 4: deterministic 8-K Item-code classify
uv run python -m worker migrations --dry-run            # Phase 7: JD migration-hint extraction

# Production jobs (need Supabase creds; 8-K needs ANTHROPIC_API_KEY):
uv run python -m worker run-all                         # run the whole pipeline once
uv run python -m worker schedule --hour 6               # Phase 6: run it daily at 06:00 UTC, forever
```

On this dev box there is no `uv`; a Python 3.12 `.venv` is used directly, e.g.
`.venv\Scripts\python.exe -m worker form_d --dry-run --limit 25`.

See **`docs/DEPLOY.md`** for Dockerized deployment (API + scheduled worker) and how
to verify the nightly run via the `ingestion_runs` table.

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
