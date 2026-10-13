"""뉴욕연은 기준금리 어댑터(공개). docs/p5_design.md §1.2·§4.1."""

from __future__ import annotations

from kbj.data.public.nyfed.client import (
    RATE_GROUPS,
    NyfedClient,
    NyfedError,
    NyfedThrottled,
    RatePoint,
    parse_rates,
)
from kbj.data.public.nyfed.datasets import DATASETS

__all__ = [
    "DATASETS",
    "RATE_GROUPS",
    "NyfedClient",
    "NyfedError",
    "NyfedThrottled",
    "RatePoint",
    "parse_rates",
]
