Read ./CLAUDE.md, /docs/schema.sql, and the existing worker/core modules. Schema is locked. Do exactly
this one phase:

--- PHASE 3 — Job snapshots + velocity ---
Implement job snapshotting. For every company with an ats_token, fetch current open postings via the
shared rate-limited client (cache politely), and insert one row per company per day into the existing
`job_snapshots` table — open_positions count (and dept_counts JSONB if cheap). COUNTS ONLY, no raw
descriptions. The surging/velocity logic ALREADY LIVES in the `company_velocity` view in schema.sql —
do NOT re-implement it; read from the view. Note in comments that there is no backfill. Write
real-fixture tests (snapshot a couple of known companies, assert counts) AND an integration check that
a fresh snapshot makes the company appear in `company_velocity`. Run them and show output. Log the run
to ingestion_runs.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, and DEFINITION OF DONE. State the phase and the tables/views
it depends on, then go.