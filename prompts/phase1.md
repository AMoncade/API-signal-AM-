Read ./CLAUDE.md, /docs/schema.sql, /docs/signals-api-spec.md (especially Appendix A — the Form D
field reference), and the existing core/worker modules — the code on disk is the source of truth. The
schema is locked; write to the existing tables, do NOT alter them. Do exactly this one phase:

--- PHASE 1 — Form D ingestion + parser ---
Implement Form D ingestion in the worker. (1) Find new D and D/A filings from the EDGAR daily index.
(2) Fetch each primary_doc.xml via the shared rate-limited client. (3) Parse per spec Appendix A —
CONFIRM the actual XML tag paths against the live files; do not trust the appendix blindly. Write to
the existing `companies` and `form_d_filings` tables: upsert the company (cik canonical key,
entity_name, state_or_country, entity_type, industry_group), then insert the filing (accession_number,
submission_type, filed_at, date_of_first_sale, total_offering_amount_raw keeping "Indefinite" verbatim,
total_offering_amount_usd NULL when not numeric, total_amount_sold_usd, federal_exemption). Apply the
HARD RULES: dedupe D/A by cik and compute incremental_amount_sold_usd by diffing the prior filing; set
is_pooled_fund and do NOT create company rows for pooled funds; strip personal names. Write REAL-FIXTURE
tests that parse 3 real recent primary_doc.xml files with their known correct amounts and assert the
stored rows match. Run them and show the output. Log the run to ingestion_runs.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, DEFINITION OF DONE, and HUMAN CHECKPOINTS — this is a
checkpoint phase, so STOP after tests pass and give me one real filing URL + the parsed row to confirm
against the actual SEC filing before committing. State the phase and the tables/modules it depends on,
then go.