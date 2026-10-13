"""Yahoo chart 어댑터(로그인 — DATA_TIERS §1 '약관상 로그인'). docs/p5_design.md §1.1(FND)·D-P5-18.

매크로 12종(로그인 탭 '시장' — MAC)과 미국 지수 일봉(US)이 같이 쓴다. 승격 원본
`legacy/etf_traker/board/us/sources.py:parse_yahoo`·`yahoo_daily`.
"""

from __future__ import annotations

from kbj.data.private.yahoo.client import (
    YahooClient,
    YahooError,
    YahooNotFound,
    YahooThrottled,
    parse_yahoo,
    to_bars,
)
from kbj.data.private.yahoo.datasets import DATASETS

__all__ = [
    "DATASETS",
    "YahooClient",
    "YahooError",
    "YahooNotFound",
    "YahooThrottled",
    "parse_yahoo",
    "to_bars",
]
