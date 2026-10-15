"""Logging setup shared by all command-line entry points."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Send log records to stderr with timestamps (picked up by ``docker logs``).

    Args:
        level: Standard logging level name, e.g. ``"INFO"``.
    """
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
