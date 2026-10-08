"""KIS ETF/ETN 현재가(FHPST02400000) 파서 → 장중 ETF 시세(`EtfQuote`) — 괴리율 경고용.

설계 docs/p3_design.md §1.2·§3.3, docs/metrics.md §4(순유입 계산은 **마감 NAV 만** — 장중 iNAV 는
괴리율 경고에만). 필드는 KIS 문서 기준 **[실측 필요 — 체크리스트 #21·#23]**: 현재가 `stck_prpr`,
장중 NAV `nav`, 괴리율 `dprt`(%), 거래대금 `acml_tr_pbmn`(원), 거래량 `acml_vol`.

- 괴리율이 응답에 없으면 (현재가 ÷ NAV − 1) × 100 으로 계산한다(둘 다 있을 때만 — LLM 이 아닌 코드
  계산). NAV 가 없으면 None — 지어내지 않는다.
- 현재가가 없으면 실패(`KisParseError`). quality 는 언제나 estimated(장중).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import EtfQuote
from kbj.data.private.kis.parse import KisParseError, kis_float, kis_int, pick, rows_of
from kbj.data.private.kis.quotes import TURNOVER_UNIT_KRW

__all__ = ["SOURCE", "params_etf_quote", "parse_etf_quote"]

SOURCE: Final = "kis"


def params_etf_quote(code: str) -> dict[str, str]:
    """ETF 는 거래소를 나누지 않는다(카탈로그 — NXT 거래 여부 [실측 필요])."""
    return {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}


def parse_etf_quote(
    body: Mapping[str, Any], code: str, ts: datetime, *, source: str = SOURCE
) -> EtfQuote:
    what = f"FHPST02400000 {code}"
    rows = rows_of(body, "output", what=what)
    if len(rows) != 1:
        raise KisParseError(f"{what}: 결과가 한 행이 아니다({len(rows)}행)")
    out = rows[0]
    price = kis_float(pick(out, "stck_prpr"))
    if price is None or price <= 0:
        raise KisParseError(f"{what}: 현재가(stck_prpr)가 없다")
    inav = kis_float(pick(out, "nav"))
    premium = kis_float(pick(out, "dprt"))
    if premium is None and inav:
        premium = round((price / inav - 1) * 100, 4)
    return EtfQuote(
        code=code,
        ts=ts,
        price=price,
        inav=inav,
        premium_pct=premium,
        turnover=kis_int(pick(out, "acml_tr_pbmn"), TURNOVER_UNIT_KRW),
        volume=kis_int(pick(out, "acml_vol")),
        source=source,
        quality=Quality.ESTIMATED,
    )
