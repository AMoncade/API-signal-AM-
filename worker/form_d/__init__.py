"""Phase 1 - Form D ingestion + parsing.

Form D is the FIRST signal and seeds the whole company universe. Parsing is PURE
XML (HARD RULE #2 - no LLM): the fields are already structured. This package keeps
the parse step (``parser``) free of any I/O so it is exhaustively testable against
real saved ``primary_doc.xml`` fixtures, and isolates the network/DB orchestration
in ``ingest``.
"""
