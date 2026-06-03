"""EDGAR access helpers shared by the Form D (Phase 1) and 8-K (Phase 4) jobs.

Discovery is done by DIFFING the published daily index files rather than polling
thousands of CIKs (spec §2). All requests go through the shared rate-limited
HttpClient, which enforces the global <=10 req/s cap and the mandatory
SEC_USER_AGENT header (HARD RULE #1).
"""
