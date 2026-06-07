-- ============================================================================
-- Migration: drop domain columns + widen ATS providers
-- Run this ONCE in the Supabase SQL editor (it is idempotent / safe to re-run).
--
-- Why:
--  * derived_domain / domain_status were lossy and unused -> removed.
--  * ATS matching now spans 7 public job-board providers, so the ats_provider
--    CHECK constraint must allow the new ones.
-- The funded_and_hiring view selects derived_domain, so it must be dropped first
-- and recreated without it.
-- ============================================================================

-- 1. Drop the view that depends on companies.derived_domain.
drop view if exists funded_and_hiring;

-- 2. Drop the lossy domain columns and their index.
alter table companies drop column if exists derived_domain;
alter table companies drop column if exists domain_status;
drop index if exists idx_companies_domain;

-- 3. Widen ats_provider to all supported providers.
alter table companies drop constraint if exists companies_ats_provider_check;
alter table companies add constraint companies_ats_provider_check
  check (ats_provider in
    ('greenhouse','lever','ashby','smartrecruiters','recruitee','workable','breezy'));

-- 4. Recreate the headline join view (now without derived_domain).
create or replace view funded_and_hiring as
select
  c.cik,
  c.entity_name,
  c.ats_provider,
  f.latest_filing_date,
  f.latest_amount_sold_usd,
  v.current_open,
  v.baseline_open,
  v.velocity_ratio
from companies c
join (
  select cik,
         max(filed_at) as latest_filing_date,
         (array_agg(total_amount_sold_usd order by filed_at desc))[1] as latest_amount_sold_usd
  from form_d_filings
  where filed_at >= now() - interval '90 days'
  group by cik
) f on f.cik = c.cik
join company_velocity v on v.cik = c.cik
where v.is_surging;
