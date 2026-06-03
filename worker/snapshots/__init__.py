"""Phase 3 - daily job snapshots feeding the surging-velocity signal.

For every company with a matched ATS board we record ONE row per day of the open
posting count (and per-department counts when cheap) in job_snapshots. COUNTS
ONLY - never raw descriptions (HARD RULE #10 / spec §6).

Velocity itself is NOT computed here: the surge logic lives in the company_velocity
VIEW in /docs/schema.sql (the single source of truth). There is NO BACKFILL - the
time series exists only from the first snapshot forward, which is why the worker
must run every day.
"""
