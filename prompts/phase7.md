Read ./CLAUDE.md, /docs/schema.sql, and the existing worker modules. Schema is locked. Do exactly this
one phase:

--- PHASE 7 — Migrations enrichment ---
Add the migrations enrichment: regex-prefilter snapshotted JD text IN MEMORY (migrate|replace|switch|
migrating|moving off) and ONLY for matches call a cheap/local model to extract the named software. Insert
into the existing `company_tech_signals` table with a confidence score and source_posting_url —
low-confidence enrichment only, never a standalone premium endpoint, never persist raw JD text. Write a
real-fixture test on a handful of real postings (some with, some without migration language) and show the
extraction.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, and DEFINITION OF DONE. State the phase and its dependencies,
then go.