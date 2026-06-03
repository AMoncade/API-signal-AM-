# Signals API — Product Specification

A B2B intelligence API built from **two free public data sources**, sold as developer-facing signal endpoints on RapidAPI. The defensible value is not access to the raw data (it's public) — it's the **normalization, classification, and the cross-source join** that no single source provides.

---

## 1. Core thesis

| | |
|---|---|
| **What you sell** | Interpreted *signals*, not raw data. The sources are free; the product is the analysis layer and the join. |
| **The wedge** | "Filed a private funding round **and** is now surging on hiring." Neither source gives this alone. |
| **What you do NOT sell** | Exclusive early access (Form D is public on EDGAR instantly), or broad commodity job/permit data (incumbents already do this cheaply). |

---

## 2. The two ingestion pipes

### Pipe A — ATS public job boards (Greenhouse + Lever)
- **Endpoints:** `boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`; Lever's public postings JSON API.
- **Auth:** None. GET job-board data is public and intended for consumption (companies use it to power their own career sites). This is the *intended use*, which is why it's legally clean — unlike scraping gated LinkedIn person-data.
- **Critical constraint:** No "list all companies" endpoint. `{board_token}` is a board slug, *not* the company name, and is often not the obvious string. You must already hold a validated token to query a company.
- **Coverage:** Tech/startup-skewed. Good overlap with venture-backed companies; poor for traditional enterprise, local business, public megacaps.

### Pipe B — SEC EDGAR (Form D + 8-K)
- **Auth:** None, no API key. **Hard limit: 10 requests/second across all EDGAR domains; a descriptive `User-Agent` header is mandatory** (its absence is the #1 cause of 403s).
- **Form D** = private/exempt offerings (startup funding). **Structured XML** — the data is in labeled fields already.
- **8-K** = unscheduled material events (exec departures, lawsuits, bankruptcies). **Prose** — needs classification.
- **Architecture note:** Prefer nightly bulk-index diffing over polling thousands of CIKs. SEC publishes daily index files and bulk datasets for exactly this.

---

## 3. The cold-start solution (how you get the company universe)

Do **not** start from a mystery "list of 5,000 companies." Run it backwards so the funding pipe builds the hiring universe:

1. Poll EDGAR for **new Form D filings** → parse issuer legal name, CIK, state, entity type (see Appendix A). **Form D contains no website/domain field** (confirmed against the live XML), so the domain must be *derived*, not read.
2. **Derive the domain** (now a required step, not a freebie): issuer legal name + state → website, via a resolver (search API / enrichment service) or a name-normalization + DNS-probe heuristic. Budget for this failing on a meaningful fraction of filers — that fraction simply drops out of the hiring side.
3. From the resolved domain, generate candidate board tokens (the bare second-level domain is the most common Greenhouse/Lever slug, but not guaranteed — try a few variants).
4. **Probe** the Greenhouse/Lever endpoint (HTTP 200 + non-empty jobs = hit; 404 or empty array = skip).
5. Keep the hits and begin snapshotting their postings.

**Filter out pooled investment funds first.** A large share of Form D filings are VC/PE/hedge funds raising *their own* fund — not operating companies getting funded. Use `industryGroupType` / `isPooledInvestmentFundType` (Appendix A) to exclude them, or your "funded startup" signal is mostly fund paperwork.

Result: every tracked operating company is already in your highest-value cohort (recently funded), and the funded-and-hiring join falls out for free.

---

## 4. Processing layer — what touches AI and what does not

| Source | Method | Why |
|---|---|---|
| **Form D** | Parse XML directly. **No AI.** | Fields are already structured and labeled. An LLM here is slower, can hallucinate numbers, and adds nothing. |
| **8-K** | LLM classification — use a **hosted** model | Prose requiring nuanced judgment (material lawsuit vs. boilerplate). Low filing volume, high stakes; reliability *is* the product. |
| **Job velocity** | Pure counting. **No AI.** | Snapshot posting counts over time and diff. |
| **JD migration hints** | Regex prefilter → local LLM only on candidates | Cuts inference volume 90%+. Run local Ollama only on JDs that already match "migrate/implement/replace." |

### Form D field nuance (accuracy-critical)
Expose **two separate dollar fields**, never one "amount raised":
- `totalOfferingAmount` — the size of the offering (sometimes marked indefinite).
- `totalAmountSold` — what's actually been sold so far (the honest "raised" figure).
Also pull: issuer legal name, entity type, **industry group** (includes a venture-capital-fund category), date of first sale, minimum investment. Amounts include non-cash consideration at the issuer's good-faith valuation and may be contingent — caveat in your docs.

---

## 5. The signal endpoints

| Endpoint | Source | Method | Primary buyer | Strength |
|---|---|---|---|---|
| `GET /signals/pre-announced-funding` | Form D | XML parse | VCs, sales teams, recruiters | **Strong** — also seeds the universe |
| `GET /signals/surging-velocity` | Job counts | Counting | Recruiting agencies | **Strong** — but no backfill (see §6) |
| `GET /signals/material-risks` | 8-K | Hosted LLM | Fintech, investors | **Strong but separate** — different universe (public cos) |
| `GET /signals/funded-and-hiring` | Form D × jobs | Join | Sales, recruiters | **The wedge** — nobody else sells this in one call |
| `migrations` | JD text | Regex + local LLM | (enrichment) | **Weak** — ship as a *field* on company records, not a flagship endpoint |

**Provenance:** every signal should carry the source filing/posting URL. It's cheap and it's a selling point.

**PII rule:** output **business names only** — strip homeowner/individual personal-name fields. Keeps you clear of privacy exposure; trivial to enforce in normalization.

---

## 6. Infrastructure

- **Always-on cheap VM, not a laptop.** Velocity is built from continuous snapshots with **zero backfill** — a missed night is a permanent hole in the time series. This is also the argument to start ingesting *now*, before the product is polished.
- **Storage (Supabase free tier = 500 MB):** store **extracted signals only**, discard raw JD/filing text after processing. Tens of thousands of full job descriptions will blow the cap fast. You're selling insights, not a document warehouse.
- **Rate limiting:** EDGAR 10 req/s + User-Agent; moderate, cached polling against ATS endpoints.

---

## 7. Business model

- **List signals separately, not as one $199 bundle.** The signals have different buyers; bundling forces each to pay for things they don't want. Separate listings (or volume/feature tiers) let buyers self-select and usually capture more total revenue.
- **Include a free tier** with a small request cap. It's the standard RapidAPI funnel that lets developers evaluate before paying; no free tier = no on-ramp.
- **Tiered pricing**, not a flat single price (flat pricing is a race to the bottom).
- **Economics:** RapidAPI takes ~20%. Consider a direct sales channel (own landing page + Stripe) to keep full margin on customers who already trust you, and to avoid single-platform dependence.

---

## 8. Reality checks & risk posture

- **Legality:** Public ATS APIs (intended-use consumption) and public SEC records are about as safe as it gets — fundamentally different from the LinkedIn-derived person-data products that have drawn lawsuits. Still: *not legal advice.* Get a real opinion before adding any scrape-the-page tiers (e.g., Accela-style portals if you ever expand).
- **Coverage is limited** to Greenhouse/Lever companies (tech-skewed). Be honest about it in your docs.
- **No exclusive timing edge** on Form D — it's public the instant it posts and commercial feeds index it in ~300 ms. Sell the *join*, not a head start.
- **Revenue expectations:** treat individual signals as modest passive income that may compound; the join is the part with real pricing power.

---

## 9. Build order

1. **EDGAR side first** — cleanest data, no scraping, no PII, no per-company token sprawl. Ship `pre-announced-funding` (XML parse) and learn RapidAPI's listing/billing mechanics on the easy product.
2. **Stand up the always-on ingestion** and begin snapshotting jobs immediately to accumulate velocity history.
3. **Add `material-risks`** (8-K + hosted LLM) as a separate listing.
4. **Wire the join** (`funded-and-hiring`) once both pipes are flowing — this is the headline product.
5. **Demote migrations** to an enrichment field.

> Net effect of the corrections: *less* engineering, not more — killing the Form D AI step and regex-prefiltering the JDs both reduce work.

---

## Appendix A — Form D XML field reference

**Where the file lives:** `https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCESSION_NO_NO_DASHES}/primary_doc.xml`
**Finding new filings:** filter the EDGAR daily index (`form.idx`) or the per-CIK submissions feed to `submissionType` in `{"D", "D/A"}`. Current observed `schemaVersion`: `X0708`. Remember the 10 req/s + User-Agent rule.

Root element: `<edgarSubmission>`. Paths below confirmed against live filings.

**Issuer block — `primaryIssuer/`**
| Path | Meaning | Notes |
|---|---|---|
| `cik` | SEC entity ID | Your join key to 8-K and to dedupe amendments |
| `entityName` | Company legal name | The string you feed to domain derivation |
| `entityType` | Corporation / LP / LLC… | |
| `jurisdictionOfInc` | State/country of incorporation | |
| `issuerAddress/{street1,street2,city,stateOrCountry,zipCode}` | Address | Use `stateOrCountry` in domain derivation |
| `issuerPhoneNumber` | Phone | |
| `yearOfInc/value` | Year founded | |
| `issuerPreviousNameList/previousName` | Prior names (0..n) | |
| — | **website / domain** | **Does not exist. Must derive.** |

**Offering block — `offeringData/`**
| Path | Meaning | Notes |
|---|---|---|
| `industryGroup/industryGroupType` | Sector | e.g. "Pooled Investment Fund", "Other Technology" |
| `typesOfSecuritiesOffered/isEquityType` | bool | Filter for equity raises |
| `typesOfSecuritiesOffered/isPooledInvestmentFundType` | bool | **Exclude these — they're funds, not startups** |
| `offeringSalesAmounts/totalOfferingAmount` | Offering size | **May be `"Indefinite"` — handle non-numeric** |
| `offeringSalesAmounts/totalAmountSold` | Cumulative raised | The honest "raised" figure (see trap #1) |
| `offeringSalesAmounts/totalRemaining` | Remaining | |
| `minimumInvestmentAccepted` | Min check size | |
| `dateOfFirstSale/value` | First sale date | `dateOfFirstSale/yetToOccur` = bool |
| `federalExemptionsExclusions/item` | Exemption claimed | e.g. `06b` = Rule 506(b), `06c` = 506(c) |
| `investorsInfo/totalNumberAlreadyInvested` | Investor count | |
| `clarificationOfResponses/responseClarification` | Free text | Often holds "in addition to $X" notes |

**Related persons — `relatedPersonsList/relatedPersonInfo/` (0..n)** — names + relationships (Executive Officer, Director, Promoter). **PII: strip personal names from output.** You may keep relationship *counts* if useful.

**Accuracy traps (these break the product if ignored):**
1. **Amendments restate cumulative totals.** `totalAmountSold` is cumulative for the whole offering; a `D/A` re-reports the running total. Dedupe by `cik` + offering and diff against the prior filing, or you double-count and inflate raised amounts.
2. **`totalOfferingAmount` is often `"Indefinite"`** (especially funds). Parse defensively; don't coerce to 0.
3. **Pooled funds dominate the feed.** Filter them out (`isPooledInvestmentFundType` / `industryGroupType`) before calling anything a "funded startup."

---

## Appendix B — 8-K classification spec

**Key efficiency win:** 8-K filings are tagged with structured **Item codes** in the filing metadata (EDGAR index/header) — you do *not* classify the event type from scratch. Parse Item codes first (deterministic, free); use the hosted LLM only to extract specifics and assign severity from the prose. This cuts cost and sharply improves reliability.

**Item → event-type → default severity**
| 8-K Item | `eventType` | Default severity |
|---|---|---|
| 1.03 Bankruptcy or Receivership | `bankruptcy` | critical |
| 4.02 Non-Reliance on Prior Financials (restatement) | `restatement` | critical |
| 2.05 Costs of Exit/Disposal (restructuring, layoffs) | `restructuring_layoffs` | high |
| 2.06 Material Impairments | `impairment` | high |
| 2.04 Triggering Event Accelerating Financial Obligation | `debt_acceleration` | high |
| 3.01 Delisting / Listing-Rule Failure | `delisting_risk` | high |
| 1.05 Material Cybersecurity Incident | `cyber_incident` | high |
| 5.02 Departure of Directors/Officers | `exec_departure` | medium→high (see prompt) |
| 4.01 Change in Certifying Accountant | `auditor_change` | medium |
| 1.01 / 1.02 Entry/Termination of Material Agreement | `contract_change` | low→medium |
| 8.01 Other Events | `other` | varies (litigation often lands here) |

**Output schema (the LLM must return exactly this, JSON only):**
```json
{
  "cik": "string",
  "entityName": "string",
  "accessionNo": "string",
  "filedAt": "ISO-8601",
  "sourceUrl": "string",
  "itemCodes": ["5.02"],
  "eventType": "exec_departure",
  "severity": "low | medium | high | critical",
  "isAbrupt": true,
  "affectedRole": "string | null",
  "summary": "string, <=240 chars, neutral and factual",
  "confidence": 0.0
}
```

**System prompt:**
```
You are a financial-filing event classifier. You will receive (1) the plain text of a
single SEC Form 8-K and (2) the list of its Item codes. Output ONLY a JSON object matching
the schema provided — no preamble, no markdown fences, no commentary.

Rules:
- Use the Item codes as the PRIMARY signal for eventType. Use the prose only to fill
  specifics (affectedRole, summary) and to set severity.
- If the event matches no defined category, set eventType to "other".
- Never state a fact not present in the text. If a field is unknown, use null.
- summary must be neutral and factual: what happened, who, effective when. No adjectives,
  no speculation, <=240 characters.
- For Item 5.02 (departures): set isAbrupt=true ONLY if the text indicates the departure is
  effective immediately, follows a disagreement, or names no successor; otherwise false.
  Raise severity to "high" when isAbrupt is true OR the role is CEO/CFO/Chair; else "medium".
- confidence is your calibrated certainty the classification is correct (0.0–1.0).
- Output valid, parseable JSON only.
```

**Severity rubric (apply after the LLM returns, as a deterministic override where you can):**
- `critical` — bankruptcy, restatement, going-concern language.
- `high` — layoffs/restructuring, impairment, debt acceleration, delisting risk, cyber incident, abrupt C-suite/Chair exit.
- `medium` — auditor change, planned/orderly officer transition, non-core director change.
- `low` — routine agreement entry/termination, administrative items.

**Model choice:** use a hosted model for this endpoint (not local Ollama). Volume is low (only event-bearing 8-K items, after the Item-code prefilter), and misclassification here is the failure that churns paying fintech/investor buyers — the one place the quality premium is worth paying for.
