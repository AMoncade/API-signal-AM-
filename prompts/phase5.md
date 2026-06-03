Read ./CLAUDE.md, /docs/schema.sql, and the existing api/core modules. Schema is locked. Do exactly this
one phase:

--- PHASE 5 — Read API + RapidAPI auth ---
Implement the read-only FastAPI endpoints against the signal tables and views:
/signals/pre-announced-funding (from form_d_filings), /signals/surging-velocity (from the
company_velocity view), /signals/material-risks (from eight_k_events), /signals/funded-and-hiring (READ
FROM the funded_and_hiring view — do NOT re-implement the join), and expose `migrations` as an enrichment
field from company_tech_signals (not a flagship route). Every response: clean JSON, pagination with a
small default page size, filterable by date/sector where sensible, source URL on each record. Add
middleware validating the X-RapidAPI-Proxy-Secret header against RAPIDAPI_PROXY_SECRET and rejecting
non-proxy traffic (confirm the current header name against live RapidAPI provider docs). Write endpoint
tests against a seeded test DB. Run the API and show each endpoint returning data.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, DEFINITION OF DONE. This is a HARD phase — ultrathink a short
plan first. State the phase and its dependencies, then go.