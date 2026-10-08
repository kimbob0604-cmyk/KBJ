"""KIS 주식현재가(FHKST01010100) 파서 → 종목 스냅(`Snap`)·일봉(`Bar`) — 장 마감 수집용.

승격 원본: SD `kis_api.py`(현재가 조회), ET `board/ingest/kis.py:FIELD`:71. 설계 docs/p3_design.md
§1.2·§3.5. 필드 이름은 KIS 문서 기준이고 실측 전이다 **[실측 필요 — 체크리스트 #22·#25]**.

- 종가 `stck_prpr`(마감 뒤 현재가 = 종가), 등락률 `prdy_ctrt`(%), 거래량 `acml_vol`, 거래대금
  `acml_tr_pbmn`(**원** [실측 필요 — 시간외 포함 여부]), 시가총액 `hts_avls`(**억원** → 원 [실측
  필요]), 상장주식수 `lstn_stcn`, 시가·고가·저가 `stck_oprc`·`stck_hgpr`·`stck_lwpr`.
- 상태(관리·정지·정리매매 — metrics §3 스크리닝 기본 제외): `iscd_stat_cls_code`(51 관리종목·58
  거래정지 [추정])·`mang_issu_cls_code`(관리 Y/N)·`temp_stop_yn`(정지 Y/N)·`sltr_yn`(정리매매 Y/N).
  **상태 칸이 하나도 없으면 `status_flags=None`(모름)** — 빈 튜플('상태 없음 확인')과 구분한다(R22).
- 종가가 없으면 그 종목은 실패(`KisParseError`) — 0 으로 채우지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap
from kbj.data.private.kis.parse import (
    KisParseError,
    kis_float,
    kis_int,
    pick,
    rows_of,
    venue_param,
)

__all__ = [
    "MKTCAP_UNIT_KRW",
    "TURNOVER_UNIT_KRW",
    "bar_from_quote",
    "params_quote",
    "parse_quote",
    "status_flags",
]

TURNOVER_UNIT_KRW: Final = 1  # acml_tr_pbmn 은 원 [실측 필요]
MKTCAP_UNIT_KRW: Final = 100_000_000  # hts_avls 는 억원 [실측 필요]
_MARKETS: Final[Mapping[str, str]] = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KONEX": "KONEX"}
_STATUS_KEYS: Final = ("iscd_stat_cls_code", "mang_issu_cls_code", "temp_stop_yn", "sltr_yn")


def params_quote(code: str, venue: str = "KRX") -> dict[str, str]:
    return {"FID_COND_MRKT_DIV_CODE": venue_param(venue), "FID_INPUT_ISCD": code}


def status_flags(out: Mapping[str, Any]) -> tuple[str, ...] | None:
    """상태 칸 → 플래그. 칸이 하나도 없으면 None(모름)."""
    if not any(k in out for k in _STATUS_KEYS):
        return None
    flags: list[str] = []
    stat = str(out.get("iscd_stat_cls_code") or "").strip()
    if stat == "51" or str(out.get("mang_issu_cls_code") or "").upper() == "Y":
        flags.append("managed")
    if stat == "58" or str(out.get("temp_stop_yn") or "").upper() == "Y":
        flags.append("halted")
    if str(out.get("sltr_yn") or "").upper() == "Y":
        flags.append("liquidation")
    return tuple(flags)


def _output(body: Mapping[str, Any], code: str) -> dict[str, Any]:
    rows = rows_of(body, "output", what=f"FHKST01010100 {code}")
    if len(rows) != 1:
        raise KisParseError(f"FHKST01010100 {code}: 결과가 한 행이 아니다({len(rows)}행)")
    return rows[0]


def parse_quote(
    body: Mapping[str, Any],
    code: str,
    *,
    day: date,
    venue: str = "KRX",
    source: str = "kis",
    quality: Quality = Quality.OK,
    market: str | None = None,
    name: str | None = None,
) -> Snap:
    """FHKST01010100 `output` → 그날 스냅. 응답의 종목코드가 다르면 실패(남의 값을 쓰지 않는다)."""
    out = _output(body, code)
    got = str(pick(out, "stck_shrn_iscd", "mksc_shrn_iscd") or code).strip()
    if got != code:
        raise KisParseError(f"FHKST01010100 {code}: 응답 종목코드가 다르다({got})")
    close = kis_float(pick(out, "stck_prpr", "stck_clpr"))
    if close is None or close <= 0:
        raise KisParseError(f"FHKST01010100 {code}: 종가(stck_prpr)가 없다")
    turnover = kis_int(pick(out, "acml_tr_pbmn"), TURNOVER_UNIT_KRW)
    mkt = str(pick(out, "rprs_mrkt_kor_name") or "").strip().upper()
    return Snap(
        code=code,
        date=day,
        name=name or (str(pick(out, "hts_kor_isnm", "bstp_kor_isnm") or "") or None),
        market=_MARKETS.get(mkt, market),
        kind=None,
        close=close,
        chg_pct=kis_float(pick(out, "prdy_ctrt")),
        volume=kis_int(pick(out, "acml_vol")),
        turnover=turnover,
        turnover_is_estimate=turnover is None,
        mktcap=kis_int(pick(out, "hts_avls"), MKTCAP_UNIT_KRW),
        shares=kis_int(pick(out, "lstn_stcn")),
        status_flags=status_flags(out),
        source=source,
        venue=venue,
        quality=quality,
    )


def bar_from_quote(body: Mapping[str, Any], snap: Snap, *, quality: Quality | None = None) -> Bar:
    """같은 응답의 시가·고가·저가 + 스냅 종가 → 그날 일봉(source·venue 는 스냅과 같다).

    고가 기준 신고가(board)가 당일 고가를 쓴다. 못 받은 칸은 None(종가로 채우지 않는다)."""
    out = _output(body, snap.code)
    return Bar(
        code=snap.code,
        date=snap.date,
        open=kis_float(pick(out, "stck_oprc")),
        high=kis_float(pick(out, "stck_hgpr")),
        low=kis_float(pick(out, "stck_lwpr")),
        close=snap.close,
        volume=snap.volume,
        turnover=snap.turnover,
        source=snap.source,
        venue=snap.venue,
        quality=snap.quality if quality is None else quality,
    )
