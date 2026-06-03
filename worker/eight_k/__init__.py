"""Phase 4 - 8-K ingestion + classification (the material-risks signal).

Two-stage by design (Appendix B / HARD RULE #8): the event TYPE is set from the
structured Item codes FIRST (deterministic, free) - we never ask an LLM what kind
of event it is. Only for event-bearing items do we fetch the prose and call a
HOSTED model (ANTHROPIC_API_KEY, HARD RULE #9) to extract specifics + a severity,
then a deterministic severity rubric is applied as an override.

8-K issuers are PUBLIC companies - a DIFFERENT universe from the Form D operating
startups - so eight_k_events has NO FK to companies (it joins only opportunistically
on cik). SIGNALS ONLY: we store the <=240-char derived summary, never raw text.
"""
