"""Phase 7 - migrations enrichment (the low-confidence extra, done cheaply).

A cheap regex PREFILTER runs over snapshotted JD text IN MEMORY; only the small
fraction of postings that mention migration language go to a cheap/local model for
software extraction (HARD RULE #11 - cuts inference volume ~90%). Results are
LOW-CONFIDENCE enrichment in company_tech_signals - never a flagship endpoint, and
the raw JD text is NEVER persisted.
"""
