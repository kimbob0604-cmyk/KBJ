"""KIS 지수·업종 현재가 파서 — 장중 지수 타일·업종 히트맵·장중 시장 거래대금(metrics §6.1·§6.3).

SD `server.py:_fetch_kr_indices_live`:877(네이버 스크랩)의 대체(conflict_map §1.13). TR 은 **[추정
TR — 실측 필요 #24]**: 국내업종 현재지수 FHPUP02100000, 업종 구분별 전체시세 FHPUP02140000. 없으면
장중 타일은 '전일 확정'만 보인다(R6).

- 지수 값 `bstp_nmix_prpr`, 등락률 `bstp_nmix_prdy_ctrt`, 누적 거래량 `acml_vol`, 누적 거래대금
  `acml_tr_pbmn`(**백만원** → 원 [실측 필요]). 업종 목록은 `output2`(없으면 `output`), 업종 코드
  `bstp_cls_code`, 이름 `hts_kor_isnm`.
- 모두 장중 잠정(quality estimated, source `kis`). 값이 없는 행은 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import IndexQuote, SectorQuote
from kbj.data.private.kis.parse import (
    MILLION_KRW,
    KisParseError,
    keys_of,
    kis_float,
    kis_int,
    pick,
    rows_of,
)

__all__ = [
    "INDEX_TURNOVER_UNIT_KRW",
    "SECTOR_BLNG",
    "SECTOR_CODE_PREFIX",
    "SECTOR_MARKETS",
    "SECTOR_SINGLES",
    "SOURCE",
    "params_index_quote",
    "params_sector_quotes",
    "parse_index_quote",
    "parse_sector_quotes",
]

SOURCE: Final = "kis"
INDEX_TURNOVER_UNIT_KRW: Final = MILLION_KRW  # 지수 누적 거래대금 — 백만원 [실측 필요 #24]
# 업종 전체시세를 부를 시장 → (KIS 업종 코드, 시장 분류) [추정 — #24]
SECTOR_MARKETS: Final[Mapping[str, tuple[str, str]]] = {
    "KOSPI": ("0001", "K"),
    "KOSDAQ": ("1001", "Q"),
}
# 시장마다 부를 소속 구분(FID_BLNG_CLS_CODE) — 실측 2026-10-20: 코스닥에 '0'(전업종)을 주면 선물·
# 환율·코스피 업종이 섞인 100줄이 와(코스닥 IT서비스 칸에 코스피 0029 가 들어갔다) 코스닥 업종이
# 거의 없다. '3' = 코스닥 산업 업종 17개, '1' = 기타 구분(오락·문화 1015 등). 코스피 '0' = 38개.
SECTOR_BLNG: Final[Mapping[str, tuple[str, ...]]] = {"KOSPI": ("0",), "KOSDAQ": ("3", "1")}
# 업종 코드 첫 자리(시장 밖 줄 — 선물·KSQ150·환율 등 — 은 버린다)
SECTOR_CODE_PREFIX: Final[Mapping[str, str]] = {"KOSPI": "0", "KOSDAQ": "1"}
# 목록에 없는 코스닥 업종 — 지수 한 건씩(FHPUP02100000, 응답에 이름이 없어 여기 이름). 코드는
# 2026-10-20 값 대조 [추정]: KRX 10-19 종가 → KIS 장중 값(건설 70.70→67.63, 통신 1108.8→1085.4,
# IT서비스 637.88→630.13, 제조 2785.86→2685.07). 다음 거래일 KRX 종가로 확인(probe_results).
SECTOR_SINGLES: Final[Mapping[str, Mapping[str, str]]] = {
    "KOSDAQ": {"1009": "제조", "1010": "건설", "1032": "통신", "1033": "IT 서비스"},
}


def params_index_quote(code: str) -> dict[str, str]:
    return {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}


def params_sector_quotes(market: str, blng: str | None = None) -> dict[str, str]:
    code, cls = SECTOR_MARKETS[market]
    return {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": code,
        "FID_COND_SCR_DIV_CODE": "20214",
        "FID_MRKT_CLS_CODE": cls,
        "FID_BLNG_CLS_CODE": blng if blng is not None else SECTOR_BLNG[market][0],
    }


def parse_index_quote(
    body: Mapping[str, Any], code: str, ts: datetime, *, name: str | None = None
) -> IndexQuote:
    what = f"FHPUP02100000 {code}"
    rows = rows_of(body, "output", "output1", what=what)
    if len(rows) != 1:
        raise KisParseError(f"{what}: 결과가 한 행이 아니다({len(rows)}행)")
    out = rows[0]
    value = kis_float(pick(out, "bstp_nmix_prpr"))
    if value is None or value <= 0:
        raise KisParseError(f"{what}: 지수 값(bstp_nmix_prpr)이 없다 — 받은 키 {keys_of(rows)}")
    return IndexQuote(
        code=code,
        ts=ts,
        name=name or (str(pick(out, "hts_kor_isnm") or "") or None),
        value=value,
        chg_pct=kis_float(pick(out, "bstp_nmix_prdy_ctrt")),
        turnover=kis_int(pick(out, "acml_tr_pbmn"), INDEX_TURNOVER_UNIT_KRW),
        volume=kis_int(pick(out, "acml_vol")),
        source=SOURCE,
        quality=Quality.ESTIMATED,
    )


def parse_sector_quotes(body: Mapping[str, Any], market: str, ts: datetime) -> list[SectorQuote]:
    """업종 목록 → 업종마다 한 줄. 목록이 비었거나 값 있는 행이 없으면 실패(조용히 0행 아님)."""
    what = f"FHPUP02140000 {market}"
    rows = rows_of(body, "output2", "output", what=what)
    out: list[SectorQuote] = []
    prefix = SECTOR_CODE_PREFIX.get(market, "")
    for r in rows:
        code = str(pick(r, "bstp_cls_code") or "").strip()
        value = kis_float(pick(r, "bstp_nmix_prpr"))
        if not code or value is None or not code.startswith(prefix):
            continue  # 시장 밖 줄(선물·환율·다른 시장 업종)은 버린다
        out.append(
            SectorQuote(
                market=market,
                code=code,
                ts=ts,
                name=str(pick(r, "hts_kor_isnm") or "") or None,
                value=value,
                chg_pct=kis_float(pick(r, "bstp_nmix_prdy_ctrt")),
                turnover=kis_int(pick(r, "acml_tr_pbmn"), INDEX_TURNOVER_UNIT_KRW),
                source=SOURCE,
                quality=Quality.ESTIMATED,
            )
        )
    if not out:
        raise KisParseError(f"{what}: 업종 행이 없다 — 받은 키 {keys_of(rows)}")
    return out
