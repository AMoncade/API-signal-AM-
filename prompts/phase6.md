Read ./CLAUDE.md, /docs/schema.sql, and the existing worker/api modules. Do exactly this one phase:

--- PHASE 6 — Schedule + deploy ---
Make the worker run on a schedule (Form D + 8-K + job snapshots nightly; keep EDGAR under 10 req/s
globally and stagger the pulls; log each run to ingestion_runs). Add a Dockerfile per component and
deployment config for {Fly.io / Railway / my VPS — pick one and ask if unsure}. Document setting the env
vars on that host and how to verify the scheduled job ran. Walk me through deploying both components.
---

Follow CLAUDE.md's CONTRACTS, HARD RULES, and DEFINITION OF DONE. State the phase and its dependencies,
then go.