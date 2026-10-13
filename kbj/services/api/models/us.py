"""간밤 미국 신고가 응답 모델(docs/p5_design.md §5 — `UsBoard`). 모양은 웨이브 1(FND)이 고정, 본문은
US 묶음(이 파일은 US 만 고친다 — §1.10). 미국 거래일(뉴욕 날짜)을 반드시 싣는다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from kbj.services.api.models.common import ApiModel

__all__ = ["UsBoard", "UsBoardRow", "UsSectorRow"]


class UsBoardRow(ApiModel):
    symbol: str
    name: str | None
    sector: str | None
    close: float | None  # 달러
    chg_pct: float | None
    market_cap: float | None  # 달러
    label: str | None  # 신고가 종류(52주 등 — board/us 정의)


class UsSectorRow(ApiModel):
    sector: str
    n: int
    newhigh: int
    ret_pct: float | None


class UsBoard(ApiModel):
    us_trade_date: date  # 미국 거래일(뉴욕 날짜)
    basis: Literal["close", "high"]
    summary_lines: list[str]  # us_summary 산출 그대로(템플릿 문장 — LLM 없음)
    newhigh: list[UsBoardRow]
    sectors: list[UsSectorRow]
    mega_movers: list[UsBoardRow]
    pending: int  # 이력 252봉 미만이라 보류한 종목 수(숨기지 않는다)
