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
- Phase 1 (Form D): confirmed live XML paths vs Appendix A. The root `<edgarSubmission>`
  has NO namespace. `dateOfFirstSale` is under `offeringData/typeOfFiling/dateOfFirstSale/value`
  (NOT directly under offeringData) and may be `<yetToOccur>` instead. `isPooledInvestmentFundType`
  is under `typesOfSecuritiesOffered`; we treat that OR `industryGroupType == "Pooled Investment
  Fund"` as pooled. `federalExemptionsExclusions/item` can repeat (joined with commas).
- `accession_number` and `filed_at` are NOT in primary_doc.xml -- they come from the daily
  index. So `parse_form_d()` is pure on the XML; the ingest layer supplies those two from the
  `FilingRef`. CIK is taken zero-padded (10 digits) from the XML; the index URL uses unpadded.
- Pooled funds really do dominate: a sample day was ~80% funds (20/25), all correctly skipped.
  Pooled = skipped ENTIRELY (no company, no filing), since form_d_filings FKs companies(cik).
- Incremental amount: per schema design we diff `total_amount_sold` by cik against the most
  recent prior filing (Form D has no explicit offering id). Verified on a real Secured Income
  Fund-II D/A chain: 300,392,879 - 274,233,604 = 26,159,275.
- `worker/store.py` is a thin DATA-ACCESS layer over the one `get_supabase()` wrapper (not a
  parallel client). `SupabaseStore` imports `supabase` lazily, and `InMemoryStore` (no DB)
  powers `--dry-run` and the integration tests, so Phase 1 runs/tests with zero credentials.
- This dev box (adrienmoncade) has NO `uv`; `python` is the Windows Store stub. A working
  Python 3.12 `.venv` (with deps minus `supabase`) already exists from a prior session and is
  used directly: `.venv\Scripts\python.exe -m pytest`. Install `supabase` + set creds to write.
- Phase 1 adversarial review fixed: master.idx is split so company names containing `|` are
  NOT dropped (was silent data loss); parser now rejects missing/zero CIK and blank entityName
  (canonical-key hygiene); `latest_amount_sold_for_cik` has a deterministic (filed_at,
  accession) tie-break since one daily index gives every filing the same midnight filed_at.
- Phase 2 (seeding): the no-key heuristic resolver is LOSSY in two ways worth remembering.
  (1) DNS "resolves" almost any `.com` (parked pages), so domain_status='resolved' is
  optimistic, not proof of the right company. (2) Short name-derived board tokens cause FALSE
  POSITIVES (e.g. "APEX TECH GROWTH PARTNERS" matched an unrelated greenhouse board "apex").
  The accurate path is the ApiDomainResolver backend (needs DOMAIN_RESOLVER_API_KEY) plus a
  human spot-check of matched tokens (phase2 verify step). Greenhouse `/jobs` (no content=true)
  carries no per-job departments, so dept_counts comes from Lever; counts only, never raw JD.
- Phase 4 (8-K): the full-submission SGML header carries NO numeric `<ITEMS>` tags -- only
  standardized `ITEM INFORMATION:` TITLE lines. We recover codes from titles (item_codes.py)
  and cross-checked against the submissions-API numeric items (BBBY = 1.03,3.03,5.02,5.03,
  7.01,9.01). eventType comes from the codes; the hosted model only fills specifics + a
  proposed severity, which the deterministic rubric then overrides. Event-bearing filter keeps
  the LLM off routine 8-Ks (a sample day: 18/40 were event-bearing). `eight_k --dry-run` shows
  this with NO API key. Live classification needs ANTHROPIC_API_KEY (model: anthropic_model,
  default claude-sonnet-4-6); the Anthropic call reuses the shared HttpClient (not a sec.gov URL).
- Phase 5 (API): reads live tables via embeds + the SQL views (company_velocity, funded_and_hiring)
  via api/repository.py; the join logic is NEVER reimplemented in app code. Pagination uses
  range(offset, offset+limit) (limit+1 sentinel for has_more) with a UNIQUE secondary sort key
  (accession_number / cik) so equal-timestamp rows don't shuffle across pages. Proxy-secret gate
  uses hmac.compare_digest; header name is configurable (rapidapi_proxy_header) since RapidAPI's
  has changed historically -- CONFIRM before launch. Endpoints tested with a fake repo (no DB).
- Phase 5.5 (integration): InMemoryStore mirrors company_velocity AND funded_and_hiring for the
  offline end-to-end check; the joined row is validated against the API response model so a column
  rename in one phase fails loudly. funded_and_hiring is EMPTY until ~30d of snapshots exist.
- Phase 6 (deploy): one Dockerfile, two roles (api via uvicorn; worker via `python -m worker
  schedule`, a self-contained daily loop -> no host cron needed). worker/pipeline.run_all runs all
  jobs in order through the one client (global rate cap holds), each logging to ingestion_runs;
  8-K is skipped if no ANTHROPIC_API_KEY. docker-compose.yml + docs/DEPLOY.md (verify via
  ingestion_runs). NO BACKFILL -> the worker must stay up.
- Phase 7 (migrations): regex prefilter (migrat|replac|switch|moving off|sunset|deprecat|...) gates
  the model; only hits go to the extractor (OllamaExtractor local model, or KeywordExtractor
  fallback over a software catalog). Low-confidence (<=0.6) enrichment in company_tech_signals;
  raw JD text is processed in memory and NEVER stored.
- Phase 2 false-positive fix (worker/seeding/ats.py): a probe hit is now VERIFIED before
  storing. Greenhouse echoes the board owner's `company_name` on each job -> we require it to
  agree with the Form D legal name (distinctive stems equal, or board = the issuer's leading
  distinctive word >=5 chars; a shared short prefix is NOT enough). Lever has no name endpoint,
  so the token must be a strong derivation of the name (full concat / domain SLD / leading word
  >=5). Bare first-word tokens <5 chars are no longer probed. seed stats include `ats_rejected`.
  Verified live: "APEX TECH GROWTH PARTNERS II, LLC" no longer matches the greenhouse board
  "Apex Eye". This trades some recall for precision (user asked for fewer false positives);
  the API-key DomainResolver remains the higher-recall path when a key is configured.
- 8-K validation is lenient on the model's eventType/severity (coerced, not raised) because the
  Item codes + rubric are authoritative; per-filing classify errors are isolated (classify_errors)
  so one bad model response can't abort the nightly batch.
- 8-K cost/provider: EIGHT_K_PROVIDER selects 'anthropic' (hosted) or 'ollama' (FREE local model
  via worker/eight_k/classifier.OllamaClassifier -> get_classifier factory). ANTHROPIC_MODEL can be
  set to claude-haiku-4-5-20251001 (~10x cheaper). EIGHT_K_SKIP_LOW_VALUE skips 8.01/1.01/1.02
  (high volume, low value). LLM input capped at 12k chars (was 50k). A user reported ~$5/day on
  Sonnet; these knobs address that. The per-request pacing is mostly sequential model latency.
- `.env` now loads by ABSOLUTE path (core/config: _ENV_FILE = repo/.env), so `python -m worker ...`
  works from ANY directory. Convenience launchers `scan.ps1` / `dashboard.ps1` resolve the repo +
  venv themselves.
- Hardening pass (3 fixes): (1) the shared HttpClient now RETRIES transient failures (HTTP
  429/500/502/503/504 and httpx.TransportError) with exponential backoff, honoring a numeric
  Retry-After (the HTTP-date form falls back to backoff), capped at 30s. Each retry re-acquires
  the global rate slot, so the <=10 req/s cap (HARD RULE #1) still holds across retries. Knobs:
  http_max_retries (default 3), http_retry_backoff_seconds (default 0.5); 0 disables. POSTs are
  retried too (Anthropic/Ollama) and the json body is resubmitted unchanged. (2) Form D ingest_one
  now catches (ValueError, ParseError) from parse_form_d and counts it as stats.parse_errors,
  instead of letting one malformed-but-HTTP-200 filing abort the whole daily run (it used to
  propagate to ingest_form_d, mark the run 'error', and drop every later filing). Mirrors the 8-K
  classify_errors isolation. (3) ApiDomainResolver is no longer a stub: with DOMAIN_RESOLVER_API_KEY
  AND DOMAIN_RESOLVER_API_URL set it issues a real GET (Bearer key, ?name=&country=) via the shared
  client and reads the domain from the JSON (provider-agnostic _extract over common shapes), falling
  back to the heuristic on any non-200 / network error / empty result. get_resolver now requires
  BOTH key and url (key-only still yields the heuristic, as before).
- Demo dashboard (demo/dashboard.html, served at GET /demo): single file, no deps, renders the
  four signal endpoints as live tables with provenance links. /demo is in EXEMPT_PATHS (it is a
  static shell only) but its /signals fetches stay behind the proxy gate, so the demo only works
  against an instance with RAPIDAPI_PROXY_SECRET unset. CORS middleware (GET-only, outermost)
  added so the same file also works opened from disk; it does not weaken the gate (the proxy
  middleware still runs on every /signals call). Tests: tests/test_demo.py. NOTE: two agent
  sessions edited this repo concurrently on 2026-06-04 and clobbered each other's api/ edits;
  run ONE session at a time.
- Demo data mode (SIGNALS_DEMO_DATA=1): get_repository() returns api/demo_data.DemoRepository
  (seeded, in-memory, internally consistent across funding/velocity/join/migrations) instead of
  SupabaseRepository, so /demo renders fully populated with NO database, NO credentials, and NO
  supabase package installed. Strictly opt-in (default off); production reads the real DB. Added
  because a fresh box has no supabase package -> every /signals call 500s on the lazy
  `from supabase import ...`; demo mode never touches that import path.
