Read ./CLAUDE.md, /docs/schema.sql, spec Appendix B, and the existing worker/core modules. Schema is
locked. Do exactly this one phase:

--- PHASE 4 — 8-K ingestion + classification ---
Implement 8-K processing. Find new 8-K filings from the EDGAR index; read the structured Item codes
from the filing metadata (do NOT classify event_type from prose — map Item codes → event_type per
Appendix B). For event-bearing items only, fetch the filing text and call the HOSTED model
(ANTHROPIC_API_KEY) with the exact system prompt and output JSON schema in Appendix B; validate the
JSON, then apply the deterministic severity rubric as an override. Insert into the existing
`eight_k_events` table (signals only: item_codes, event_type, severity, is_abrupt, affected_role, the
<=240-char summary, confidence, source_url; no raw text). Remember this table has NO FK to companies —
different universe. Write REAL-FIXTURE tests on 3 real recent 8-Ks (include one Item 5.02 and one Item
1.03) asserting the classification. Run them, show the classified JSON, and report token cost per filing.
Log the run to ingestion_runs.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, DEFINITION OF DONE. This is a HARD phase — ultrathink a short
plan first (what it reads/writes, how its output connects up- and downstream). State the phase and its
dependencies, then go.