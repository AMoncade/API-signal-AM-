"""Worker entry point: ``python -m worker`` (or the ``worker`` console script).

Phase 0 does NO ingestion. It only proves the shared wiring loads: config plus
the single rate-limited HTTP client that every future job will reuse. Real jobs
and the schedule come in later phases.
"""

from __future__ import annotations

import logging

from core.config import get_settings
from core.http_client import HttpClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("worker")


def main() -> None:
    settings = get_settings()
    log.info(
        "worker scaffold starting (SEC_USER_AGENT configured: %s)",
        bool(settings.sec_user_agent),
    )
    # Construct the ONE shared client here so every future job reuses it and the
    # global rate cap is honored across all of them. No requests are made yet.
    with HttpClient(settings):
        log.info(
            "shared rate-limited HTTP client ready (global cap=%.1f req/s). "
            "No ingestion jobs implemented yet (Phase 0).",
            settings.http_max_requests_per_second,
        )
    log.info("worker scaffold done — nothing to ingest yet.")


if __name__ == "__main__":
    main()
