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

## Building the database (worker jobs)

The worker turns live sources into signal rows in Supabase. On this dev box use
`.venv\Scripts\python.exe -m worker ...`; on a machine with uv, `uv run python -m worker ...`.

**Quick launchers (Windows, run from anywhere):**

```powershell
.\scan.ps1                         # full nightly pipeline (run-all)
.\scan.ps1 form_d --date 20260603  # any worker job + args (free signals)
.\dashboard.ps1                    # serve the API + open Swagger at /docs
```

`scan.ps1` / `dashboard.ps1` find the project folder and venv themselves (and the
app now loads `.env` by absolute path), so they work from any directory. If
PowerShell blocks a script, run `powershell -ExecutionPolicy Bypass -File .\scan.ps1`.

**Preview anything first (no DB writes, no API keys):**

```bash
python -m worker form_d   --dry-run --limit 25   # parse live Form D filings
python -m worker seed     --dry-run --limit 80   # derive domains + match ATS (shows ats_rejected)
python -m worker snapshot --dry-run --limit 60   # ingest -> seed -> snapshot counts
python -m worker eight_k  --dry-run --limit 40   # deterministic 8-K Item-code classify (no LLM)
python -m worker migrations --dry-run            # JD migration-hint extraction
```

**Build the real database (writes to Supabase; needs `SUPABASE_*` in `.env`):**

```bash
# One day's Form D filings (any past EDGAR business day, YYYYMMDD):
python -m worker form_d --date 20260601
# The whole pipeline once for the latest published index (8-K needs ANTHROPIC_API_KEY):
python -m worker run-all
# A specific date, capped (good for a first backfill without large LLM spend):
python -m worker run-all --date 20260602 --limit 60
```

**Accumulate history (this is what makes velocity real):**

```bash
python -m worker schedule --hour 6     # runs the full pipeline daily at 06:00 UTC, forever
```

Velocity and `funded-and-hiring` have **no backfill** -- they only exist from the
first job snapshot forward, so keep `schedule` running. Until ~30 days of snapshots
accumulate, `surging-velocity` / `funded-and-hiring` are empty by design (seed a
baseline snapshot to demo the join sooner; see the integration test).

**Confirm it worked:**

- API: `uvicorn api.main:app --reload` then GET `/signals/...` (or `/health`).
- Or in the Supabase SQL editor:
  ```sql
  select job_name, status, items_processed, started_at from ingestion_runs order by started_at desc limit 10;
  select count(*) from companies;  select count(*) from form_d_filings;
  ```

## 8-K classification: cost and free/cheaper options

Only the **8-K `material-risks`** signal calls a language model (Form D, velocity,
the join, and migrations are all free). The default is the hosted Anthropic model,
which can run a few dollars for a busy day. Set these in `.env` to cut or remove cost:

| Goal | Setting |
|---|---|
| **Free, local, no key** (run a model on your own machine via [Ollama](https://ollama.com)) | `EIGHT_K_PROVIDER=ollama` then `ollama pull llama3.1` |
| **~10x cheaper hosted** | `ANTHROPIC_MODEL=claude-haiku-4-5-20251001` |
| **Fewer calls** (skip 8.01 "Other" + 1.01/1.02 contract items) | `EIGHT_K_SKIP_LOW_VALUE=true` |
| **Skip 8-K entirely** | leave `ANTHROPIC_API_KEY` empty and `EIGHT_K_PROVIDER=anthropic` |

With Ollama: install it, run `ollama pull llama3.1` (or `qwen2.5`), set
`EIGHT_K_PROVIDER=ollama`, and scans cost nothing and aren't gated by API latency.
The event TYPE is always set deterministically from the Item codes and severity from
the rubric, so the local model only fills specifics — a small local model is fine.
The LLM input is also capped at ~12k chars (the event narrative, not the exhibits)
to keep both cost and latency down. Note: the per-request pacing you saw is mostly
model latency on sequential calls; Ollama (local) or Haiku (faster) both reduce it.

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

## Continuing on another machine (with Claude Code)

Everything Claude Code needs to keep building lives in the repo, so a fresh clone on
any computer can continue the project:

1. **Clone and open in Claude Code.** It reads `CLAUDE.md` automatically (contracts,
   hard rules, and the per-phase NOTES), plus `docs/` and `prompts/`. That is the
   project's memory; it travels with the repo (it is not stored on any one machine).
2. **Install deps:** `uv sync --extra dev`. This provisions Python 3.11 (pinned in
   `.python-version`) and installs everything from `uv.lock`, including `supabase` and
   `pytest`. No `uv` yet? Windows: `irm https://astral.sh/uv/install.ps1 | iex`;
   macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
3. **Recreate secrets:** `cp .env.example .env` and fill it in. `.env` is gitignored
   and never committed, so this is the one manual step per machine. Reuse the SAME
   values as elsewhere:
   - `SEC_USER_AGENT` (any descriptive `"Name email"` string)
   - `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (the existing project; schema already loaded)
   - `ANTHROPIC_API_KEY` (only for the 8-K `material-risks` signal)
4. **Verify:** `uv run pytest` (fully offline, needs no secrets) should be all green.
5. **Run:** `uv run uvicorn api.main:app --reload` (API) or
   `uv run python -m worker run-all` (populate from live EDGAR/ATS/8-K).

The Supabase project is shared, so a new machine points at the same data with no
re-setup. Only run `docs/schema.sql` again when starting a brand-new database.

## Project layout

```
core/        shared config, Supabase wrapper, rate-limited HTTP client
api/         read-only FastAPI app (/health for now)
worker/      scheduled ingestion entry point (scaffold)
tests/       Phase 0 behavior tests
docs/        schema.sql (DB contract), spec, build playbook
prompts/     the per-phase build prompts
```
