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