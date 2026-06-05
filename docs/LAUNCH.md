# Launch checklist: code is done, this is the go-live path

All phases (0-7 plus the integration pass) are implemented and committed; 87 tests
pass against real saved fixtures. What remains needs external accounts and a host,
things only a human can create. Work top to bottom. The clock that matters:
**velocity has no backfill**, so every day the worker is not running is a
permanent hole, and `funded-and-hiring` (the headline product) needs ~30 days of
snapshots before it returns rows. Step 4 is the deadline; everything after it can
happen while history accumulates.

## 1. Rebuild the local env on this machine (~10 min)

This box currently has no `.venv` and no `.env`. In PowerShell, from the repo root:

```powershell
# Option A (preferred): uv, which provisions Python 3.11 per .python-version
irm https://astral.sh/uv/install.ps1 | iex
uv sync --extra dev
uv run pytest                      # expect: all green, no secrets needed

# Option B: plain venv on an existing Python 3.11/3.12
python -m venv .venv
.venv\Scripts\pip install -e . pytest
.venv\Scripts\python.exe -m pytest
```

## 2. Create the external accounts (~30 min)

| Account | What to grab | Used for |
|---|---|---|
| supabase.com, new project | Project URL + **service-role** key | the database |
| console.anthropic.com | API key | 8-K classification only |
| A 24/7 container host (Railway / Render / any small VPS) | n/a | the always-on worker + API |
| rapidapi.com provider account | n/a | step 7, not blocking |

Then load the schema: Supabase dashboard, SQL Editor, paste ALL of
`docs/schema.sql`, Run. Idempotent; safe to re-run.

## 3. Fill `.env` and validate locally (~15 min)

`cp .env.example .env`, fill in `SEC_USER_AGENT` (descriptive `"Name email"`
string), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `ANTHROPIC_API_KEY`. Then prove
each pipe live, credential-free first, then writing:

```powershell
python -m worker form_d  --dry-run --limit 25    # parses live EDGAR, no DB
python -m worker seed    --dry-run --limit 60    # live ATS probing, no DB
python -m worker eight_k --dry-run --limit 40    # Item-code classify, no LLM

python -m worker run-all --limit 60              # first real write to Supabase
```

Confirm in Supabase: `select * from ingestion_runs order by started_at desc;`
should show each job with status `success`, and `companies` / `form_d_filings`
should be non-empty.

## 4. Deploy: THE DEADLINE (~1 hr, do not slip past this week)

Follow `docs/DEPLOY.md` (docker compose: one image, `api` + `worker` services).
Set the `.env` values on the host. Verify after the first night:

- `ingestion_runs` has fresh rows each morning (the worker survived the night).
- `/health` responds over HTTPS.

From the first successful night, snapshot history starts accumulating. Day count
to a real `funded-and-hiring`: ~30.

## 5. Watch it for the first week (~5 min/day)

`select job_name, status, items_processed, started_at from ingestion_runs order
by started_at desc limit 10;` and read `notes` on any `error` row. Spot-check one
parsed Form D against its EDGAR source URL (human checkpoint hygiene).

## 6. Optional quality upgrades (anytime, non-blocking)

- `DOMAIN_RESOLVER_API_KEY` + `DOMAIN_RESOLVER_API_URL`: the no-key heuristic
  resolver is precision-tuned and misses boards; a real resolver raises seeding
  recall. The `ApiDomainResolver` backend now issues a real query through the
  shared client and reads the domain from the JSON response (provider-agnostic),
  falling back to the heuristic on any failure. Set both env vars to point it at
  your chosen provider; no code change needed.
- Ollama on the host: upgrades migrations extraction from the keyword fallback.

## 7. RapidAPI listing (during the 30-day accumulation window)

1. Provider dashboard, add API, point at the deployed base URL.
2. **Confirm the current proxy-secret header name** against live RapidAPI provider
   docs (it has changed historically). Ours is configurable: `RAPIDAPI_PROXY_HEADER`,
   default `X-RapidAPI-Proxy-Secret`.
3. Set `RAPIDAPI_PROXY_SECRET` in both RapidAPI and the host env; verify a direct
   (non-proxy) request is rejected and a proxied one passes.
4. List the signals **separately** with a free tier on each (spec §7):
   `pre-announced-funding` and `material-risks` can list immediately;
   `surging-velocity` and `funded-and-hiring` list after day ~30.
5. Listing copy: lead with the join ("filed a private round AND surging on
   hiring"), be honest about Greenhouse/Lever coverage skew, every record carries
   a source URL.

## 8. Day ~30: final human checkpoint

`GET /signals/funded-and-hiring` must return non-empty, plausible rows against
real (unseeded) data. Confirm, then publish the remaining two listings.
