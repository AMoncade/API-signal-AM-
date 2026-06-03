Read ./CLAUDE.md, /docs/schema.sql, and the existing worker/core modules. Schema is locked; write to
existing tables only. Do exactly this one phase:

--- PHASE 2 — Domain derivation + ATS seeding ---
Build the seeding loop. For each non-pooled-fund company from Phase 1: derive a website domain from
entity_name + state_or_country (implement a resolver interface with two backends — one using
DOMAIN_RESOLVER_API_KEY if present, and a no-key heuristic fallback that normalizes the name and probes
likely domains via DNS); store derived_domain and domain_status on the company. From the domain, generate
candidate Greenhouse and Lever board tokens (bare second-level domain plus a couple of variants) and
probe boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true and the Lever postings endpoint via
the shared client: HTTP 200 + non-empty = hit → store ats_provider + ats_token; 404/empty = skip. Record
failures. Write real-fixture tests (a few companies with known domains/known board tokens), run it over
the Phase 1 companies, and report domain hit rate and ATS match rate. Show the output.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, and DEFINITION OF DONE. State the phase and the tables/modules
it depends on, then go.