"""A seeded, database-free Repository for the /demo dashboard.

Lets you SEE the dashboard fully populated with realistic sample rows without a
Supabase project, credentials, or even the ``supabase`` package installed. It is
strictly opt-in: ``get_repository`` returns this only when ``SIGNALS_DEMO_DATA``
is truthy (default off), so production is unaffected.

The sample data is fictional but shaped exactly like the real signal rows (same
fields the SQL views/tables produce), and it is internally consistent: the two
companies in ``funded_and_hiring`` also appear in funding and velocity, and carry
migration tags. Nothing here talks to a network or a database.
"""

from __future__ import annotations

from api.repository import Page, board_url, filing_index_url

# --- companies that are both raising money AND surging on hiring (the join) ---
# cik -> (entity_name, state, industry, domain, ats_provider, ats_token)
_NIMBUS = "0001900001"
_COBALT = "0001900002"
_QUANTA = "0001900005"


def _funding_row(
    cik, accession, name, state, industry, sub_type, filed_at, offering_raw, offering_usd,
    sold_usd, incremental_usd, exemption,
) -> dict:
    return {
        "cik": cik,
        "entity_name": name,
        "state_or_country": state,
        "derived_domain": None,
        "industry_group": industry,
        "submission_type": sub_type,
        "filed_at": filed_at,
        "date_of_first_sale": filed_at[:10],
        "total_offering_amount_raw": offering_raw,
        "total_offering_amount_usd": offering_usd,
        "total_amount_sold_usd": sold_usd,
        "incremental_amount_sold_usd": incremental_usd,
        "federal_exemption": exemption,
        "source_url": filing_index_url(cik, accession),
    }


_FUNDING = [
    _funding_row(_QUANTA, "0001900005-26-000001", "Quanta Logistics, Inc.", "WA",
                 "Other Technology", "D", "2026-06-02T00:00:00+00:00",
                 "Indefinite", None, 3200000.0, 3200000.0, "06b"),
    _funding_row("0001900008", "0001900008-26-000002", "Tessera AI, Inc.", "CA",
                 "Other Technology", "D", "2026-06-02T00:00:00+00:00",
                 "8000000", 8000000.0, 5000000.0, 5000000.0, "06b"),
    _funding_row(_COBALT, "0001900002-26-000003", "Cobalt Health, Inc.", "MA",
                 "Health Care", "D/A", "2026-05-30T00:00:00+00:00",
                 "40000000", 40000000.0, 38500000.0, 8500000.0, "06b"),
    _funding_row(_NIMBUS, "0001900001-26-000004", "Nimbus Robotics, Inc.", "CA",
                 "Other Technology", "D", "2026-05-28T00:00:00+00:00",
                 "15000000", 15000000.0, 12000000.0, 12000000.0, "06b"),
    _funding_row("0001900003", "0001900003-26-000001", "Ledgerwise, LLC", "NY",
                 "Commercial Banking", "D", "2026-05-22T00:00:00+00:00",
                 "6000000", 6000000.0, 4250000.0, 4250000.0, "06c"),
    _funding_row("0001900004", "0001900004-26-000001", "Verdant Energy Corp", "TX",
                 "Energy Conservation", "D", "2026-05-18T00:00:00+00:00",
                 "25000000", 25000000.0, 9000000.0, 9000000.0, "06b"),
    _funding_row("0001900006", "0001900006-26-000005", "Beacon Biosciences, Inc.", "CA",
                 "Biotechnology", "D", "2026-05-12T00:00:00+00:00",
                 "50000000", 50000000.0, 50000000.0, 50000000.0, "06b"),
    _funding_row("0001900007", "0001900007-26-000002", "Harbor Freight Capital, LP", "IL",
                 "Commercial", "D", "2026-05-09T00:00:00+00:00",
                 "10000000", 10000000.0, 1500000.0, 1500000.0, "06c"),
]


def _velocity_row(cik, name, provider, token, domain, current, baseline, ratio) -> dict:
    return {
        "cik": cik,
        "entity_name": name,
        "derived_domain": domain,
        "ats_provider": provider,
        "latest_date": "2026-06-03",
        "current_open": current,
        "baseline_open": baseline,
        "baseline_date": "2026-05-04",
        "velocity_ratio": ratio,
        "is_surging": True,
        "source_url": board_url(provider, token),
    }


_VELOCITY = [
    _velocity_row(_NIMBUS, "Nimbus Robotics, Inc.", "greenhouse", "nimbusrobotics",
                  "nimbusrobotics.com", 84, 28, 3.0),
    _velocity_row(_COBALT, "Cobalt Health, Inc.", "lever", "cobalthealth",
                  "cobalthealth.com", 60, 24, 2.5),
    _velocity_row(_QUANTA, "Quanta Logistics, Inc.", "greenhouse", "quantalogistics",
                  "quantalogistics.com", 40, 18, 2.22),
]


def _risk_row(cik, accession, name, filed_at, item_codes, event_type, severity,
              summary, *, is_abrupt=None, affected_role=None, confidence=0.9) -> dict:
    return {
        "cik": cik,
        "entity_name": name,
        "filed_at": filed_at,
        "item_codes": item_codes,
        "event_type": event_type,
        "severity": severity,
        "is_abrupt": is_abrupt,
        "affected_role": affected_role,
        "summary": summary,
        "confidence": confidence,
        "source_url": filing_index_url(cik, accession),
    }


_RISKS = [
    _risk_row("0000950001", "0000950001-26-000011", "Helios Semiconductor Corp",
              "2026-06-02T00:00:00+00:00", ["1.03"], "bankruptcy", "critical",
              "Filed for Chapter 11 reorganization; operations to continue during the process."),
    _risk_row("0000950002", "0000950002-26-000007", "Atlas Retail Group, Inc.",
              "2026-06-01T00:00:00+00:00", ["5.02"], "exec_departure", "high",
              "Chief Financial Officer departed effective immediately; no successor named.",
              is_abrupt=True, affected_role="CFO"),
    _risk_row("0000950003", "0000950003-26-000004", "Pioneer Mining Co",
              "2026-05-29T00:00:00+00:00", ["2.06"], "impairment", "high",
              "Recorded a non-cash impairment charge against mining assets."),
    _risk_row("0000950004", "0000950004-26-000009", "Sterling Media Holdings",
              "2026-05-27T00:00:00+00:00", ["4.02"], "restatement", "critical",
              "Prior financial statements should no longer be relied upon; restatement underway."),
    _risk_row("0000950005", "0000950005-26-000003", "Borealis Pharma, Inc.",
              "2026-05-24T00:00:00+00:00", ["1.05"], "cyber_incident", "high",
              "Disclosed a material cybersecurity incident affecting internal systems.",
              confidence=0.8),
    _risk_row("0000950007", "0000950007-26-000002", "Oakline Foods Corp",
              "2026-05-15T00:00:00+00:00", ["4.01"], "auditor_change", "medium",
              "Changed its independent registered public accounting firm."),
    _risk_row("0000950006", "0000950006-26-000006", "Granite Industrial, Inc.",
              "2026-05-13T00:00:00+00:00", ["1.01"], "contract_change", "low",
              "Entered into a material definitive supply agreement."),
]


def _join_row(cik, name, domain, provider, filed_at, sold_usd, current, baseline, ratio) -> dict:
    return {
        "cik": cik,
        "entity_name": name,
        "derived_domain": domain,
        "ats_provider": provider,
        "latest_filing_date": filed_at,
        "latest_amount_sold_usd": sold_usd,
        "current_open": current,
        "baseline_open": baseline,
        "velocity_ratio": ratio,
    }


_JOIN = [
    _join_row(_NIMBUS, "Nimbus Robotics, Inc.", "nimbusrobotics.com", "greenhouse",
              "2026-05-28T00:00:00+00:00", 12000000.0, 84, 28, 3.0),
    _join_row(_COBALT, "Cobalt Health, Inc.", "cobalthealth.com", "lever",
              "2026-05-30T00:00:00+00:00", 38500000.0, 60, 24, 2.5),
    _join_row(_QUANTA, "Quanta Logistics, Inc.", "quantalogistics.com", "greenhouse",
              "2026-06-02T00:00:00+00:00", 3200000.0, 40, 18, 2.22),
]


def _mig(software, conf, token):
    return {
        "software": software,
        "signal_type": "migration",
        "confidence": conf,
        "source_posting_url": f"https://boards.greenhouse.io/{token}/jobs/4101",
        "detected_at": None,
    }


_MIGRATIONS = {
    _NIMBUS: [_mig("Salesforce", 0.45, "nimbusrobotics"), _mig("Segment", 0.38, "nimbusrobotics")],
    _COBALT: [_mig("Workday", 0.42, "cobalthealth")],
    _QUANTA: [_mig("NetSuite", 0.36, "quantalogistics")],
}


def _page(rows: list[dict], offset: int, limit: int) -> Page:
    # Mirror the production limit+1 sentinel so pagination behaves identically.
    window = rows[offset : offset + limit + 1]
    return Page(items=window[:limit], has_more=len(window) > limit)


def _after(row_filed_at: str, filed_after: str | None) -> bool:
    return filed_after is None or row_filed_at[:10] >= filed_after


class DemoRepository:
    """In-memory Repository serving the seeded sample data above. No DB, no network."""

    def pre_announced_funding(
        self, *, offset: int, limit: int, sector: str | None = None, filed_after: str | None = None
    ) -> Page:
        rows = [
            r for r in _FUNDING
            if (not sector or r["industry_group"] == sector) and _after(r["filed_at"], filed_after)
        ]
        return _page(rows, offset, limit)

    def surging_velocity(self, *, offset: int, limit: int, min_ratio: float | None = None) -> Page:
        rows = [r for r in _VELOCITY if min_ratio is None or (r["velocity_ratio"] or 0) >= min_ratio]
        return _page(rows, offset, limit)

    def material_risks(
        self,
        *,
        offset: int,
        limit: int,
        severity: str | None = None,
        event_type: str | None = None,
        filed_after: str | None = None,
    ) -> Page:
        rows = [
            r for r in _RISKS
            if (not severity or r["severity"] == severity)
            and (not event_type or r["event_type"] == event_type)
            and _after(r["filed_at"], filed_after)
        ]
        return _page(rows, offset, limit)

    def funded_and_hiring(self, *, offset: int, limit: int) -> Page:
        return _page(_JOIN, offset, limit)

    def migrations_for_ciks(self, ciks: list[str]) -> dict[str, list[dict]]:
        return {cik: _MIGRATIONS.get(cik, []) for cik in ciks}
