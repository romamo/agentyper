"""Logging configuration for agentyper's -v / -vv verbosity flags."""

from __future__ import annotations

import logging


def configure_logging(verbose: int) -> None:
    """
    Configure the root logger based on verbosity level.

    Args:
        verbose: 0 = WARNING (default), 1 = INFO (-v), 2+ = DEBUG (-vv)
    """
    level_map = {
        0: logging.WARNING,
        1: logging.INFO,
    }
    level = level_map.get(verbose, logging.DEBUG)

    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger().setLevel(level)
