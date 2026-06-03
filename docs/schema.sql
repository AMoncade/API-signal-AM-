-- ============================================================================
-- Corporate Intent Intelligence API — DATABASE SCHEMA (single source of truth)
-- Target: Supabase / PostgreSQL 13+
-- ============================================================================
-- This file is THE contract. Every build phase reads it and extends it; no phase
-- invents or renames tables/columns. If a change is truly needed, edit THIS file
-- in one deliberate commit and note what changed.
--
-- Core contracts:
--   * CIK is the canonical company key. Everything that can join, joins on cik.
--   * Two universes (see spec): Form D + jobs = operating startups (the companies
--     table). 8-K = public companies, a DIFFERENT set — so eight_k_events does NOT
--     foreign-key into companies. They overlap only by chance, on cik.
--   * SIGNALS ONLY. No raw filing text, no raw job descriptions are ever stored.
--     The single free-text we keep is the short, derived 8-k summary.
--   * Form D nuances are encoded in column design: "Indefinite" offering amounts
--     are preserved as text, and cumulative vs incremental amounts are separate.
--
-- How to load in Supabase: paste this whole file into the SQL Editor and run.
-- The backend (worker + API) connects with the SERVICE ROLE key, which bypasses
-- RLS. RLS is enabled with no anon policies, so the tables are not publicly
-- readable via the auto-generated API.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Helper: keep updated_at fresh
-- ----------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ============================================================================
-- companies — the canonical entity. Seeded from non-fund Form D filers (Phase 1),
-- enriched with a derived domain + matched ATS board (Phase 2).
-- ============================================================================
create table if not exists companies (
  cik                 text primary key,                 -- 10-digit zero-padded EDGAR CIK (CANONICAL KEY)
  entity_name         text not null,                    -- issuer legal name (business name only — no PII)
  state_or_country    text,                             -- from Form D issuer address
  entity_type         text,                             -- Corporation / LLC / Limited Partnership ...
  industry_group      text,                             -- Form D offeringData.industryGroup.industryGroupType

  -- Phase 2: domain derivation + ATS seeding (Form D has NO website field — derived)
  derived_domain      text,
  domain_status       text not null default 'pending'
                        check (domain_status in ('pending','resolved','unresolved','failed')),
  ats_provider        text check (ats_provider in ('greenhouse','lever')),
  ats_token           text,                             -- board token/slug; NULL until matched

  first_seen_at       timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index if not exists idx_companies_domain      on companies (derived_domain);
create index if not exists idx_companies_state        on companies (state_or_country);
-- the "seeded and hiring-trackable" set:
create index if not exists idx_companies_ats          on companies (ats_provider, ats_token)
  where ats_token is not null;

drop trigger if exists trg_companies_updated_at on companies;
create trigger trg_companies_updated_at
  before update on companies
  for each row execute function set_updated_at();

-- ============================================================================
-- form_d_filings — one row per Form D / D/A filing (Phase 1).
-- accession_number is the natural PK, which prevents re-inserting the same filing.
-- Amendments (D/A) RESTATE the cumulative total_amount_sold; the worker computes
-- incremental_amount_sold by diffing against the prior filing for the same cik.
-- ============================================================================
create table if not exists form_d_filings (
  accession_number          text primary key,           -- e.g. 0001946140-22-000002
  cik                       text not null references companies (cik) on delete cascade,
  submission_type           text not null               -- 'D' or 'D/A'
                              check (submission_type in ('D','D/A')),
  is_amendment              boolean generated always as (submission_type = 'D/A') stored,
  filed_at                  timestamptz not null,
  date_of_first_sale        date,                        -- NULL if "yet to occur"

  -- Offering amounts. Preserve "Indefinite" verbatim; numeric column is NULL then.
  total_offering_amount_raw text,                        -- exactly as filed (may be 'Indefinite')
  total_offering_amount_usd numeric,                     -- parsed; NULL when not numeric
  total_amount_sold_usd     numeric,                     -- CUMULATIVE, as reported on this filing
  incremental_amount_sold_usd numeric,                   -- COMPUTED by worker: this filing minus prior

  industry_group            text,
  is_pooled_fund            boolean not null default false, -- funds are filtered OUT upstream; flag kept for audit
  federal_exemption         text,                        -- e.g. '06b' (Rule 506(b)), '06c' (506(c))

  created_at                timestamptz not null default now()
);
create index if not exists idx_formd_cik        on form_d_filings (cik);
create index if not exists idx_formd_filed_at    on form_d_filings (filed_at desc);
create index if not exists idx_formd_first_sale  on form_d_filings (date_of_first_sale desc);

-- ============================================================================
-- job_snapshots — daily count of open postings per company (Phase 3).
-- COUNTS ONLY — no raw descriptions. One snapshot per company per day.
-- Velocity has NO backfill: it exists only from the first snapshot forward.
-- ============================================================================
create table if not exists job_snapshots (
  cik             text not null references companies (cik) on delete cascade,
  snapshot_date   date not null default current_date,
  open_positions  integer not null check (open_positions >= 0),
  dept_counts     jsonb,                                 -- optional {"engineering":12,"sales":8}
  captured_at     timestamptz not null default now(),
  primary key (cik, snapshot_date)
);
create index if not exists idx_snapshots_date on job_snapshots (snapshot_date desc);

-- ============================================================================
-- eight_k_events — classified material events from 8-K filings (Phase 4).
-- SEPARATE UNIVERSE (public companies): intentionally NO FK to companies.
-- event_type is set from the structured 8-K Item codes first; the hosted LLM only
-- fills specifics + severity. summary is the one short derived free-text we keep.
-- ============================================================================
create table if not exists eight_k_events (
  accession_number  text primary key,
  cik               text not null,                       -- not an FK (different universe); indexed for opportunistic joins
  entity_name       text not null,                       -- business name only
  filed_at          timestamptz not null,
  item_codes        text[] not null default '{}',        -- e.g. {'5.02','1.03'}
  event_type        text not null
                      check (event_type in (
                        'bankruptcy','restatement','restructuring_layoffs','impairment',
                        'debt_acceleration','delisting_risk','cyber_incident',
                        'exec_departure','auditor_change','contract_change','other')),
  severity          text not null
                      check (severity in ('low','medium','high','critical')),
  is_abrupt         boolean,                              -- relevant for exec departures (Item 5.02)
  affected_role     text,                                 -- e.g. 'CFO'; NULL if n/a
  summary           text check (char_length(summary) <= 240),  -- neutral, factual, derived
  confidence        numeric(3,2) check (confidence >= 0 and confidence <= 1),
  source_url        text not null,                        -- provenance: link to the EDGAR filing
  created_at        timestamptz not null default now()
);
create index if not exists idx_8k_cik        on eight_k_events (cik);
create index if not exists idx_8k_filed_at    on eight_k_events (filed_at desc);
create index if not exists idx_8k_event_type  on eight_k_events (event_type);
create index if not exists idx_8k_severity    on eight_k_events (severity);

-- ============================================================================
-- company_tech_signals — migrations ENRICHMENT (Phase 7). Low-confidence only.
-- Derived from JD keyword hits; never a flagship endpoint, never stores raw JD text.
-- ============================================================================
create table if not exists company_tech_signals (
  id                 bigint generated always as identity primary key,
  cik                text not null references companies (cik) on delete cascade,
  software           text not null,                       -- e.g. 'Salesforce'
  signal_type        text not null default 'migration'
                       check (signal_type in ('migration','adoption','evaluation')),
  confidence         numeric(3,2) check (confidence >= 0 and confidence <= 1),
  source_posting_url text,
  detected_at        timestamptz not null default now(),
  unique (cik, software, signal_type)
);
create index if not exists idx_tech_cik on company_tech_signals (cik);

-- ============================================================================
-- ingestion_runs — worker run log, so you can verify the nightly job actually ran
-- (Phase 6). Cheap observability for the always-on worker.
-- ============================================================================
create table if not exists ingestion_runs (
  id              bigint generated always as identity primary key,
  job_name        text not null,                          -- 'form_d' | 'eight_k' | 'job_snapshots' | 'seeding'
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  status          text not null default 'running'
                    check (status in ('running','success','error')),
  items_processed integer not null default 0,
  notes           text
);
create index if not exists idx_runs_job_started on ingestion_runs (job_name, started_at desc);

-- ============================================================================
-- VIEWS — these ARE the signal contracts the API reads. The join logic lives here
-- in exactly one place, so it cannot drift between phases.
-- ============================================================================

-- Velocity: latest open count vs the count ~30 days earlier. Surging = >= 2x.
-- (Window/threshold also configurable in the API; this view is the baseline truth.)
create or replace view company_velocity as
with latest as (
  select distinct on (cik) cik, snapshot_date as latest_date, open_positions as current_open
  from job_snapshots
  order by cik, snapshot_date desc
),
baseline as (
  select distinct on (js.cik) js.cik, js.open_positions as baseline_open, js.snapshot_date as baseline_date
  from job_snapshots js
  join latest l on l.cik = js.cik
  where js.snapshot_date <= l.latest_date - interval '30 days'
  order by js.cik, js.snapshot_date desc
)
select
  l.cik,
  l.latest_date,
  l.current_open,
  b.baseline_open,
  b.baseline_date,
  case when b.baseline_open is not null and b.baseline_open > 0
       then round(l.current_open::numeric / b.baseline_open, 2) end as velocity_ratio,
  coalesce(b.baseline_open is not null and b.baseline_open > 0
           and l.current_open >= 2 * b.baseline_open, false) as is_surging
from latest l
left join baseline b on b.cik = l.cik;

-- The headline JOIN: funded (Form D in last 90d) AND currently surging on hiring.
create or replace view funded_and_hiring as
select
  c.cik,
  c.entity_name,
  c.derived_domain,
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

-- ============================================================================
-- SECURITY — backend-only access. Enable RLS with no anon policies so the
-- auto-generated public API cannot read these tables. The service role key used
-- by the worker and read API bypasses RLS entirely.
-- ============================================================================
alter table companies            enable row level security;
alter table form_d_filings        enable row level security;
alter table job_snapshots         enable row level security;
alter table eight_k_events        enable row level security;
alter table company_tech_signals  enable row level security;
alter table ingestion_runs        enable row level security;

-- (Intentionally no policies granted to anon/authenticated. Add policies later
--  only if you decide to expose any table through Supabase's public API.)
