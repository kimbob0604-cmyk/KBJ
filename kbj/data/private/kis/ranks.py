"""KIS 거래량 순위(FHPST01710000) 파서 — 장중 거래대금 순위(`RankRow`)와 목록 종목의 잠정 스냅.

설계 docs/p3_design.md §1.2·§3.5. 행 해석 방식은 ET `board/ingest/kis.py:top_flows`:238(코드·이름
후보, 코드 없는 행은 버림). 필드·파라미터는 KIS 문서 기준 **[실측 필요 — 체크리스트 #23]**:
`FID_BLNG_CLS_CODE` 3 = 거래금액순 [추정], 응답 최대 30행 [추정].

- 순위 = 응답의 `data_rank`, 없으면 목록 순서.
- 거래대금 `acml_tr_pbmn`(원 [실측 필요]), 현재가 `stck_prpr`, 등락률 `prdy_ctrt`, 거래량
  `acml_vol`.
- 모두 장중 잠정 — RankRow quality estimated, 스냅은 source `kis.prelim`·estimated·
  `turnover_is_estimate=True`(마감 확정 `kis` 행과 다음 날 `krx` 행이 원장 우선순위로 이긴다).
- 시장 거래대금 집계에 ETF·ETN 을 섞지 않게 순위에서 ETF·ETN·ELW 를 뺀다(`FID_TRGT_EXLS_CLS_CODE`
  [추정 — 자리 의미 실측 필요]).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import RankRow, Snap
from kbj.data.private.kis.parse import (
    KisParseError,
    keys_of,
    kis_float,
    kis_int,
    pick,
    rows_of,
    venue_param,
)
from kbj.data.private.kis.quotes import TURNOVER_UNIT_KRW

__all__ = ["MARKET_CODES", "params_volume_rank", "parse_rank_snaps", "parse_volume_rank"]

# 시장 이름 → KIS 업종 코드(순위 범위). 코스피 0001·코스닥 1001 [추정 — #24]
MARKET_CODES: Final[Mapping[str, str]] = {"KOSPI": "0001", "KOSDAQ": "1001"}
PRELIM_SOURCE: Final = "kis.prelim"
_CODE_KEYS: Final = ("mksc_shrn_iscd", "stck_shrn_iscd")


def params_volume_rank(market: str, venue: str = "KRX") -> dict[str, str]:
    """FHPST01710000 — 시장(KOSPI·KOSDAQ) 하나의 거래금액순 순위."""
    return {
        "FID_COND_MRKT_DIV_CODE": venue_param(venue),
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": MARKET_CODES[market],
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "3",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000001100",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }


def _coded(body: Mapping[str, Any], what: str) -> list[tuple[int, str, dict[str, Any]]]:
    rows = rows_of(body, "output", "output1", what=what)
    out: list[tuple[int, str, dict[str, Any]]] = []
    for i, r in enumerate(rows, start=1):
        code = str(pick(r, *_CODE_KEYS) or "").strip()
        if not code:
            continue
        rank = kis_int(pick(r, "data_rank")) or i
        out.append((rank, code, r))
    if rows and not out:
        raise KisParseError(f"{what}: 종목코드가 한 행에도 없다 — 받은 키 {keys_of(rows)}")
    ranks = [x[0] for x in out]
    if len(set(ranks)) != len(ranks):
        raise KisParseError(f"{what}: 순위가 겹친다")
    return out


def parse_volume_rank(
    body: Mapping[str, Any],
    *,
    market: str,
    ts: datetime,
    venue: str = "KRX",
    source: str = PRELIM_SOURCE,
) -> list[RankRow]:
    what = f"FHPST01710000 {market}"
    return [
        RankRow(
            market=market,
            ts=ts,
            rank=rank,
            venue=venue,
            code=code,
            name=str(pick(r, "hts_kor_isnm") or "") or None,
            turnover=kis_int(pick(r, "acml_tr_pbmn"), TURNOVER_UNIT_KRW),
            chg_pct=kis_float(pick(r, "prdy_ctrt")),
            source=source,
            quality=Quality.ESTIMATED,
        )
        for rank, code, r in _coded(body, what)
    ]


def parse_rank_snaps(
    body: Mapping[str, Any],
    *,
    market: str,
    day: date,
    venue: str = "KRX",
    source: str = PRELIM_SOURCE,
) -> list[Snap]:
    """목록 종목의 오늘 잠정 스냅(현재가·등락률·거래량·거래대금). 현재가가 없는 행은 뺀다."""
    what = f"FHPST01710000 {market}"
    out: list[Snap] = []
    for _rank, code, r in _coded(body, what):
        close = kis_float(pick(r, "stck_prpr"))
        if close is None:
            continue
        out.append(
            Snap(
                code=code,
                date=day,
                name=str(pick(r, "hts_kor_isnm") or "") or None,
                market=market,
                kind=None,
                close=close,
                chg_pct=kis_float(pick(r, "prdy_ctrt")),
                volume=kis_int(pick(r, "acml_vol")),
                turnover=kis_int(pick(r, "acml_tr_pbmn"), TURNOVER_UNIT_KRW),
                turnover_is_estimate=True,
                mktcap=None,
                shares=kis_int(pick(r, "lstn_stcn")),
                status_flags=None,
                source=source,
                venue=venue,
                quality=Quality.ESTIMATED,
            )
        )
    return out
