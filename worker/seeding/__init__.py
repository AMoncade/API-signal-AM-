"""Phase 2 - domain derivation + ATS seeding.

Form D has NO website field (HARD RULE #6), so the domain must be DERIVED from the
issuer's legal name + state. From a derived domain we generate candidate Greenhouse
/ Lever board tokens and probe the public boards APIs; a 200 + non-empty response
means the company is hiring-trackable, and we store its ats_provider + ats_token.

The spec is explicit that derivation FAILS on a meaningful fraction of filers -
those simply drop out of the hiring side. We record the outcome so the hit rate
is visible.
"""
