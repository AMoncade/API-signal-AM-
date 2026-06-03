# Build Playbook — Corporate Intent Intelligence API (zero → live)

Companion to `signals-api-spec.md`. The spec is the **what/why**; this is the **how**. Keep both in your repo under `/docs` so Claude Code can read them.

---

## 1. Prerequisites — accounts, keys, tools

Set these up *before* opening Claude Code. Claude Code can write code but can't create your accounts.

| Thing | Why | Cost | Where |
|---|---|---|---|
| **Claude Code** | Your build agent | Pro/Max sub or API credits | see §1.1 |
| **Python 3.11+** + `uv` | Ingestion worker + API | free | python.org / `uv` via astral.sh |
| **Supabase project** | Postgres DB (free tier 500 MB) | free | supabase.com → new project → grab Project URL + service-role key |
| **Hosted LLM API key** | 8-K classification only | usage-based, low | console.anthropic.com (or other) |
| **Always-on host** | Run the worker 24/7 (velocity has no backfill) | ~$5/mo | Fly.io, Railway, Render, or any cheap VPS |
| **RapidAPI provider account** | List & monetize | free (they take ~20%) | rapidapi.com → provider dashboard |
| **Domain resolver** *(optional)* | name → website for the seeding loop | free tier or heuristic | a search API, or DIY (see Phase 2) |
| **SEC User-Agent string** | Mandatory header for EDGAR | free | just pick `"YourCo contact@you.com"` |

### 1.1 Install Claude Code
- **Recommended (no Node needed):** macOS/Linux/WSL `curl -fsSL https://claude.ai/install.sh | bash` · Windows PowerShell `irm https://claude.ai/install.ps1 | iex`
- **npm alternative (needs Node.js 18+):** `npm install -g @anthropic-ai/claude-code`
- Then run `claude` to authenticate, and `claude doctor` to confirm it's healthy.

### 1.2 Secrets you'll put in `.env`
```
SEC_USER_AGENT="YourCo contact@you.com"
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...          # worker writes with this
ANTHROPIC_API_KEY=...             # 8-K classification
DOMAIN_RESOLVER_API_KEY=...       # optional
RAPIDAPI_PROXY_SECRET=...         # set later; validates traffic came via RapidAPI
```

---

## 2. Stack (the decision Claude Code should not re-litigate)

One repo, two runnable components + a shared DB:
- **Ingestion worker** — Python. Pulls EDGAR + ATS, parses, classifies, writes signals to Supabase. Runs on a schedule on the always-on host.
- **Read API** — Python + FastAPI. Serves the signal endpoints to RapidAPI. Read-only against Supabase.
- **DB** — Supabase Postgres. **Stores extracted signals only — never raw filing/JD text.**

---

## 3. How to drive Claude Code well (read this once)

- **Feed it the docs.** Put `signals-api-spec.md` and this file in `/docs`. Start sessions by telling it to read them.
- **One phase per session.** Run `/clear` between phases so context stays clean and cheap.
- **Plan before code on the big phases.** Ask for a plan, read it, correct it, *then* say "implement." (Claude Code's Plan Mode is built for this.)
- **Make it prove each phase runs.** Every prompt below ends with "run it and show me the output / write a smoke test." Don't accept code you haven't seen execute.
- **Commit on green.** `git commit` after each phase works. If a later phase goes sideways you can roll back.
- **The gotchas live in CLAUDE.md** (next section) so they persist across every session without you re-typing them.

---

## 4. CLAUDE.md — paste this into the repo root

This is the persistent brief. It encodes the traps Claude Code would otherwise fall into.

```markdown
# Project: Corporate Intent Intelligence API

Read /docs/signals-api-spec.md and /docs/build-playbook.md before writing code.

## What this is
A B2B signals API built from two FREE public sources: SEC EDGAR (Form D + 8-K) and
public ATS job boards (Greenhouse + Lever). We sell interpreted signals and the JOIN
between funding and hiring — not raw data.

## Stack (do not change without asking)
- Python 3.11, `uv` for deps.
- Ingestion worker (scheduled) + FastAPI read API. Supabase Postgres.
- Store EXTRACTED SIGNALS ONLY. Never persist raw filing or job-description text.

## Hard rules (violating these breaks the product)
1. EDGAR: max 10 requests/second across ALL sec.gov domains; ALWAYS send the
   SEC_USER_AGENT header. Missing UA = 403. Rate-limit centrally.
2. Form D parsing is PURE XML — no LLM. The fields are already structured.
3. Form D `totalAmountSold` is CUMULATIVE; amendments (D/A) restate the running total.
   Dedupe by cik+offering and diff against the prior filing or amounts inflate.
4. `totalOfferingAmount` is often the literal string "Indefinite". Never coerce to 0.
5. FILTER OUT pooled investment funds (isPooledInvestmentFundType / industryGroupType)
   before calling anything a "funded startup".
6. Form D has NO website field. Domain must be DERIVED (name+state -> resolver/heuristic).
7. PII: strip all personal names from output. Business names only.
8. 8-K: classify event type from the structured Item codes FIRST (free, deterministic);
   use the hosted LLM only to extract specifics + severity from prose.
9. 8-K classification uses a HOSTED model (ANTHROPIC_API_KEY), not a local model.
10. Job velocity = pure counting of snapshots over time. No LLM.
11. JD migration parsing = cheap regex prefilter (migrate|replace|switch|migrating)
    THEN local/cheap LLM only on the hits. Treat migrations as low-confidence enrichment.

## Endpoints (5)
/signals/pre-announced-funding, /signals/surging-velocity, /signals/material-risks,
/signals/funded-and-hiring (THE headline join), and a `migrations` enrichment field.

## Style
Small, testable modules. A rate-limited HTTP client used everywhere. Defensive parsing
(fields may be missing/“Indefinite”/null). Every prompt: run it and show output.
```

---

## 5. The phased prompts

Work top to bottom. Each phase: **Goal → Prompt (copy the block) → Verify before moving on.**

### Phase 0 — Scaffold
**Goal:** runnable skeleton, config, healthcheck. No business logic.
```
Read /docs/signals-api-spec.md and /docs/build-playbook.md and the CLAUDE.md, then scaffold
the project. Python 3.11 with uv. Two entry points: a `worker` package (scheduled ingestion)
and an `api` package (FastAPI, read-only). Shared `core` package for: config/env loading, a
Supabase client, and a single rate-limited HTTP client class that enforces a global cap and
attaches the SEC_USER_AGENT header for sec.gov requests. Add .env.example with the variables
from the playbook, a Makefile with `run-worker` and `run-api` targets, a README, and a
`/health` endpoint on the API. Don't implement any signal logic yet. Then run the API and show
me the healthcheck responding.
```
**Verify:** `make run-api`, hit `/health`, get 200. Commit.

### Phase 1 — Form D ingestion + parser (your first real signal)
**Goal:** pull new Form D filings, parse the XML, store funding signals.
```
Implement Form D ingestion in the worker. Steps: (1) find new D and D/A filings from the
EDGAR daily index, (2) fetch each primary_doc.xml via the rate-limited client, (3) parse it
per /docs Appendix A — confirm the actual tag paths against the live files, don't trust the
appendix blindly. Extract: cik, entityName, entityType, state, industryGroupType,
isPooledInvestmentFundType, totalOfferingAmount (keep "Indefinite" as-is), totalAmountSold,
dateOfFirstSale, federal exemption. Apply the CLAUDE.md rules: handle "Indefinite", dedupe
D/A by cik+offering and store the INCREMENTAL amount sold, flag pooled funds, strip personal
names. Design the Supabase tables for companies and form_d_filings and give me the SQL
migration. Write a smoke test that parses 3 real recent filings and prints the extracted rows.
Run it and show me the output.
```
**Verify:** the 3 parsed rows look right; "Indefinite" survives; a pooled fund is flagged. Commit.

### Phase 2 — Domain derivation + ATS seeding
**Goal:** turn funded companies into ATS board tokens.
```
Build the seeding loop. For each non-pooled-fund company from Phase 1: derive a website domain
from entityName + state (implement a resolver interface with two backends — one using
DOMAIN_RESOLVER_API_KEY if present, and a no-key heuristic fallback that normalizes the name
and probes likely domains via DNS). From the domain, generate candidate Greenhouse and Lever
board tokens (try the bare second-level domain plus a couple of variants). Probe
boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true and the Lever postings endpoint:
HTTP 200 + non-empty = hit, store ats_provider + ats_token on the company; 404/empty = skip.
Record derivation failures so I can see the hit rate. Run it over the Phase 1 companies and
report: how many got a domain, how many matched an ATS board.
```
**Verify:** sane hit rate; spot-check a couple of matched tokens resolve to the right company. Commit.

### Phase 3 — Job snapshots + velocity
**Goal:** nightly posting counts → surging-velocity signal.
```
Implement job snapshotting and velocity. For every company with an ats_token, fetch current
open postings (rate-limited, cached politely), store a daily snapshot of the open count (and
per-department counts if cheap) in a job_snapshots table — counts only, NO raw descriptions.
Add a velocity computation: flag a company "surging" when open count >= 2x its count 30 days
ago (make the multiple and window configurable). Be explicit in code/comments that there is no
backfill — velocity only exists from the first snapshot forward. Write the SQL migration and a
smoke test that snapshots a few known companies and prints counts. Run it and show output.
```
**Verify:** counts match what you see on the company's real careers page. Commit. **Now leave the worker running daily** so history accumulates.

### Phase 4 — 8-K ingestion + classification
**Goal:** material-risks signal, Item-codes-first, hosted LLM for specifics.
```
Implement 8-K processing. Find new 8-K filings from the EDGAR index; read the structured Item
codes from the filing metadata (do NOT classify event type from prose — map Item codes to
eventType per /docs Appendix B). For event-bearing items only, fetch the filing text and call
the HOSTED model (ANTHROPIC_API_KEY) using the exact system prompt and output JSON schema in
Appendix B; validate the JSON, then apply the deterministic severity rubric as an override.
Store eight_k_events (signals only, include sourceUrl, no raw text). Write the SQL migration
and a smoke test on 3 recent 8-Ks (include one Item 5.02 and one Item 1.03). Run it, show me
the classified JSON, and report token cost per filing.
```
**Verify:** the 5.02 classifies as exec_departure with sensible severity/isAbrupt; bankruptcy = critical. Commit.

### Phase 5 — The read API + RapidAPI auth
**Goal:** the five endpoints, secured for RapidAPI.
```
Implement the FastAPI read endpoints against the signal tables (read-only):
/signals/pre-announced-funding, /signals/surging-velocity, /signals/material-risks,
/signals/funded-and-hiring (the JOIN: companies with a recent Form D AND a current surging
velocity flag), and expose `migrations` as an enrichment field on company records (not a
flagship route). Every response: clean JSON, pagination with a small default page size,
filterable by date/sector where it makes sense, and include the source URL on each record.
Add middleware that validates the X-RapidAPI-Proxy-Secret header against RAPIDAPI_PROXY_SECRET
and rejects requests that didn't come through the RapidAPI proxy. Write endpoint tests with a
seeded test DB. Run the API and show me each endpoint returning data.
```
**Verify:** every endpoint returns; the join only shows funded-AND-surging; proxy-secret rejection works. Commit.

### Phase 6 — Schedule + deploy
**Goal:** the worker runs 24/7; the API is reachable.
```
Make the worker run on a schedule (Form D + 8-K + job snapshots nightly; keep EDGAR under
10 req/s globally and stagger the pulls). Add a Dockerfile for each component and deployment
config for {Fly.io / Railway / my VPS — pick one and ask if unsure}. Document exactly how to
set the env vars in that host and how to verify the scheduled job ran (a simple run-log table
or healthcheck ping). Walk me through deploying both components.
```
**Verify:** worker logs a successful nightly run on the host; the API is publicly reachable over HTTPS. Commit.

### Phase 7 — Migrations enrichment (last, optional)
**Goal:** the low-confidence extra, done cheaply.
```
Add the migrations enrichment: regex-prefilter snapshotted JD text in-memory (migrate|replace|
switch|migrating|moving off) and ONLY for matches, call a cheap/local model to extract the
named software. Attach results as a low-confidence field on company records with a confidence
score; do not create a premium standalone endpoint. Never persist the raw JD text. Show the
extraction on a handful of real postings.
```
**Verify:** matches are plausible; non-matches were skipped (cost stayed low). Commit.

---

## 6. After the code works — list on RapidAPI (manual, not Claude Code)

1. In the RapidAPI **provider** dashboard, add your API and point it at your deployed base URL.
2. Define the endpoints, then set `RAPIDAPI_PROXY_SECRET` in both RapidAPI and your host's env, so your middleware can verify traffic.
3. Create pricing tiers per the spec: a **free tier** (small request cap, the funnel) plus paid tiers; **list the signals separately**, not one bundle.
4. Write the listing docs and example requests for each endpoint.
5. RapidAPI's provider flow has changed since the Nokia acquisition — confirm the current proxy-secret header name and onboarding steps against their live provider docs before you finalize the auth middleware.

---

## 7. One-screen gotcha checklist (pin this)

- [ ] EDGAR: ≤10 req/s globally + User-Agent on every call
- [ ] Form D parsed as XML, **no LLM**
- [ ] D/A amendments deduped; store **incremental** amount sold
- [ ] `"Indefinite"` offering amounts handled, not zeroed
- [ ] Pooled investment funds filtered out of "funded startup"
- [ ] Domain **derived** (no website field exists in Form D)
- [ ] Personal names stripped everywhere (PII)
- [ ] 8-K: Item codes first, hosted LLM only for specifics + severity
- [ ] Velocity = counting only; **no backfill**, so keep the worker running
- [ ] DB stores **signals only**, never raw text (500 MB free tier)
- [ ] API validates the RapidAPI proxy secret
- [ ] Free tier exists; signals listed separately

> Build order recap: Phase 1 (Form D) ships your first signal on the cleanest data. Get the worker running early (Phase 3) so velocity history accumulates. The join (Phase 5) is the headline product. Migrations (Phase 7) is last and lowest-stakes.
```
