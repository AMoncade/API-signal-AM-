THIS SESSION: Integration pass. Do NOT treat this as isolated. Read ./CLAUDE.md, /docs/schema.sql, and
the ENTIRE codebase, then run the system end-to-end against real data: one fresh Form D company all the
way through to the funded_and_hiring join. Find and fix every seam where phases don't line up (mismatched
columns/keys, the join keying, mis-named or unused fields, a snapshot that doesn't surface in
company_velocity).

The funded_and_hiring view needs a ~30-day-old baseline snapshot to flag is_surging, which real data
won't have yet — so the view is empty by design on day one. Seed test job_snapshots with a baseline ~35
days old AND a current count >=2x it for at least one company that also has a recent Form D, so the join
actually returns rows. Verify the view returns that company.

Keep full context — we are NOT clearing or compacting during this phase. When done, STOP and show me the
funded_and_hiring output so I can confirm it is non-empty and plausible (human checkpoint).