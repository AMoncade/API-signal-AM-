# Deploy (Phase 6) - schedule the worker, expose the API

Two components share one image (`Dockerfile`):

- **api** - the read-only FastAPI service (the five signal endpoints + `/health`).
- **worker** - the scheduled ingestion. `python -m worker schedule` runs the full
  nightly pipeline (Form D → ATS seeding → job snapshots → 8-K → migrations) once a
  day and logs every job to `ingestion_runs`.

> **Velocity has NO backfill.** A missed night is a permanent hole in the time
> series, so the worker must run continuously from day one (spec §6). This is the
> argument to deploy *now*, before the product is polished.

The default target below is **Docker Compose on a cheap always-on host** (a small
VPS, Render, or Railway - anywhere that runs a container 24/7). A Fly.io variant is
at the end. Pick whichever you already have an account on; switching is a one-file
change.

---

## 1. Prerequisites

- A **Supabase** project (Project URL + **service-role** key).
- An **Anthropic API key** (only for the 8-K `material-risks` signal; the pipeline
  skips 8-K and runs everything else if it is absent).
- A host that runs a Docker container continuously, plus Docker + Docker Compose.

## 2. Load the database schema

`docs/schema.sql` is the single source of truth. In the Supabase dashboard:
**SQL Editor → New query → paste all of `docs/schema.sql` → Run.** It is idempotent,
so re-running is safe. (CLI alternative: `psql "$SUPABASE_DB_URL" -f docs/schema.sql`.)

## 3. Configure secrets

Copy `.env.example` to `.env` on the host and fill in:

| Variable | Needed for | Notes |
|---|---|---|
| `SEC_USER_AGENT` | all EDGAR calls | `"YourCo contact@you.com"` - missing = 403 |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | worker writes + API reads | service-role key (bypasses RLS) |
| `ANTHROPIC_API_KEY` | 8-K classification | optional; absent ⇒ 8-K skipped |
| `ANTHROPIC_MODEL` | 8-K model | default `claude-sonnet-4-6` |
| `RAPIDAPI_PROXY_SECRET` | API auth | set on BOTH RapidAPI and here |
| `RAPIDAPI_PROXY_HEADER` | API auth | default `X-RapidAPI-Proxy-Secret`; **confirm against current RapidAPI docs** |
| `DOMAIN_RESOLVER_API_KEY` | better Phase-2 domains | optional |

`.env` is gitignored and excluded from the image; it is read at run time.

## 4. Build and run

```bash
docker compose up -d --build      # starts both `api` and `worker`
docker compose logs -f worker     # watch the scheduler
```

- API: `http://<host>:8000/health` → `{"status":"ok",...}`. Put it behind HTTPS
  (your platform's TLS, or a reverse proxy) before pointing RapidAPI at it.
- Worker: logs `next run at … UTC` and runs the pipeline daily at 06:00 UTC
  (`--hour`). To trigger one run immediately for a smoke test:
  ```bash
  docker compose run --rm worker uv run python -m worker run-all --limit 50
  ```

## 5. Verify the scheduled job actually ran

Every job writes to `ingestion_runs`. In the Supabase SQL editor:

```sql
select job_name, status, started_at, finished_at, items_processed, notes
from ingestion_runs
order by started_at desc
limit 20;
```

You should see recent `success` rows for `form_d`, `seeding`, `job_snapshots`,
`migrations` (and `eight_k` if the Anthropic key is set). A row stuck in `running`
or marked `error` (with the traceback in `notes`) tells you exactly which job failed.

Then confirm data is flowing:

```sql
select count(*) from companies;
select count(*) from form_d_filings;
select count(*) from job_snapshots;            -- grows by one row per company per day
select * from funded_and_hiring limit 10;      -- the headline signal (needs ~30d of snapshots)
```

`funded_and_hiring` is **empty by design until ~30 days** of snapshots accumulate
(it needs a baseline to compare against). That is expected, not a bug.

---

## Fly.io variant

Run two Fly apps (or one app with a separate worker process). Example `fly.toml`
for the API:

```toml
app = "signals-api"
[build]
  dockerfile = "Dockerfile"
[http_service]
  internal_port = 8000
  force_https = true
[[http_service.checks]]
  path = "/health"
```

Set secrets with `fly secrets set SEC_USER_AGENT=... SUPABASE_URL=... SUPABASE_SERVICE_KEY=... RAPIDAPI_PROXY_SECRET=...`.
For the worker, deploy a second app whose process runs
`python -m worker schedule` (override the image command), or use a Fly scheduled
machine that runs `python -m worker run-all` daily.
