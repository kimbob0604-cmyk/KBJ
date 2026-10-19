"""KIS 장중 시장별 투자자(FHPTJ04030000) 파서 — 1분 수집 한 줄 → `InvestorMinute` 12행(P5 F2).

근거: docs/p5_flow_program_prompt.md §1.1·§1.3, docs/p5_design.md §1.7·§3.4·§4.7, docs/metrics.md
§21, ADR 0019(초안). 칸 이름은 GX `data/kis/models.py:InvestorRow`(실측 2026-09-28 — K2I 선물·콜·풋)
그대로다.

- TR 은 **호출 시점의 오늘 누적 한 줄**이다(분 이력 아님 — 응답에 시각 칸이 없다). KIS 공식 예제
  `examples_llm/domestic_stock/inquire_investor_time_by_market`
  (koreainvestment/open-trading-api)의 설명이 "HTS [0403] 시장별 시간동향 의 **상단 표**"이고,
  칸 표(COLUMN_MAPPING)에 시각 칸이 없다. 같은 계열의 FHPTJ04040000(시장별
  투자자매매동향(일별) — [0404])은 날짜별이라, 분 이력을 주는 시장별 투자자 TR 은 공식 예제에
  없다(2026-10-19 확인, 커밋 2077773). 그래서 못 받은 분은 뒤에서 채울 수 없고, 행 `ts` 는
  부르는 쪽이 정한 **슬롯 분**(호출 시각이 아님 — metrics §21)을 그대로 받는다.
- 투자자 12구분 `frgn·prsn·orgn·scrt·ivtr·pe_fund·bank·insu·mrbn·fund·etc_orgt·etc_corp` ×
  매도·매수·
  순매수 × 수량·대금. 순매수 수량 칸은 `pe_fund·etc_orgt·etc_corp` 가 `_ntby_vol`, 나머지는
  `_ntby_qty`(GX 실측) — 두 이름을 다 후보로 본다.
- **단위**: 대금은 KIS 그대로 **백만원**, 수량은 계약·주(GX `prv_gex.investor_flow` 계약 — D-P5-21
  ②,
  metrics §21 '원 저장'의 예외). 원으로 바꾸는 것은 API 다. 현물(코스피 KSP·0001, 코스닥 KSQ·1001)
  응답의 금액 단위도 백만원으로 본다 [확인 필요 — 마감 뒤 일별 확정과 대조(검산 ③)].
- 빈 칸·`-` 는 None(0 으로 채우지 않는다). 한 구분의 여섯 칸이 모두 비면 그 구분은 행이 없다.
  12구분이 **하나도** 없으면 `KisShapeError`(칸 이름이 바뀐 날 — 사유에 받은 키 이름만).
- 모든 값이 0(또는 빈 값)이면 질의가 아직 성립하지 않은 것(장 시작 전·휴장일 [확인 필요 #39 — 장전
  08:00~ 값이 오는지])으로 보고 `KisEmpty` — 0 을 사실로 쓰지 않는다(절대 규칙 2·4).
- 거래소 구분 입력이 없다(F2 §1.3) — KIS 기본값 하나(화면은 `KRX` 로 잠근다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Final, Literal

from kbj.core.p5_rows import MINUTE_INVESTORS, InvestorMinute
from kbj.core.quality import Quality
from kbj.data.private.kis.parse import (
    KisEmpty,
    KisShapeError,
    TrSpec,
    keys_of,
    kis_int,
    pick,
    rows_of,
)

__all__ = [
    "MEASURES",
    "TR_MARKET_MINUTE",
    "VALUE_UNIT",
    "field_names",
    "params_market_minute",
    "parse_market_minute",
]

# FHPTJ04030000 시장별 투자자매매동향(시세성) — 경로는 GX `scripts/probe_common.py`:41 와 KIS 공식
# 예제
TR_MARKET_MINUTE: Final = TrSpec(
    "FHPTJ04030000", "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
)
# 대금 단위 — 백만원(GX 실측 K2I). 저장도 백만원 그대로(investor_flow 계약).
# 현물은 [확인 필요 — 검산 ③]
VALUE_UNIT: Final = "백만원"

# 측정값 → 칸 접미사 후보(앞에서부터). `<투자자>_<접미사>`.
MEASURES: Final[Mapping[str, tuple[str, ...]]] = {
    "sell_qty": ("seln_vol",),
    "buy_qty": ("shnu_vol",),
    "net_qty": ("ntby_qty", "ntby_vol"),
    "sell_value": ("seln_tr_pbmn",),
    "buy_value": ("shnu_tr_pbmn",),
    "net_value": ("ntby_tr_pbmn",),
}


def field_names(investor: str) -> dict[str, tuple[str, ...]]:
    """한 투자자의 측정값 → 칸 이름 후보(가짜 서버·시험이 같은 표를 쓴다)."""
    return {m: tuple(f"{investor}_{s}" for s in sfx) for m, sfx in MEASURES.items()}


def params_market_minute(market: str, sector: str) -> dict[str, str]:
    """FHPTJ04030000 입력 — 시장 코드(`FID_INPUT_ISCD`)·업종 코드(`FID_INPUT_ISCD_2`).

    코드 표는 KIS 공식 Postman 샘플(open-trading-api
    `legacy/postman/실전계좌_POSTMAN_샘플코드_v2.6.json`): 코스피 `KSP`·`0001`,
    코스닥 `KSQ`·`1001`, 선물·콜·풋 `K2I`·`F001/OC01/OP01`, 주식선물 `999`·`S001`,
    위클리 `WKM`·`OC05/OP05`·`WKI`·`OC04/OP04`. 설정(`config/markets.yaml
    flows.minute.pairs`)에 켠 조합만 부른다."""
    if not market or not sector:
        raise ValueError("시장·업종 코드가 비었다 — 코드를 모르는 조합은 부르지 않는다")
    return {"FID_INPUT_ISCD": market, "FID_INPUT_ISCD_2": sector}


def _row(body: Mapping[str, Any]) -> dict[str, Any]:
    rows = rows_of(body, "output", "output1", what="FHPTJ04030000")
    if not rows:
        raise KisEmpty("FHPTJ04030000: 결과 행이 없다")
    # 한 줄 TR — 여러 줄이 오면 첫 줄(GX probe 와 같다). 줄 수는 [확인 필요 #39]
    return rows[0]


def parse_market_minute(
    body: Mapping[str, Any],
    *,
    ts: datetime,
    trade_date: date,
    market_code: str,
    sector_code: str,
    session: Literal["day", "night"] = "day",
    quality: Quality = Quality.OK,
) -> list[InvestorMinute]:
    """응답 한 줄 → 12구분 행(대금 백만원). ts 는 슬롯 분(부르는 쪽이 정한다)."""
    row = _row(body)
    out: list[InvestorMinute] = []
    nonzero = False
    for inv in MINUTE_INVESTORS:
        names = field_names(inv)
        vals = {m: kis_int(pick(row, *keys)) for m, keys in names.items()}
        if all(v is None for v in vals.values()):
            continue
        nonzero = nonzero or any(v not in (None, 0) for v in vals.values())
        out.append(
            InvestorMinute(
                ts=ts,
                trade_date=trade_date,
                session=session,
                market_code=market_code,
                sector_code=sector_code,
                investor=inv,
                sell_qty=vals["sell_qty"],
                buy_qty=vals["buy_qty"],
                net_qty=vals["net_qty"],
                sell_value=vals["sell_value"],
                buy_value=vals["buy_value"],
                net_value=vals["net_value"],
                quality=quality,
            )
        )
    if not out:
        raise KisShapeError(f"FHPTJ04030000: 투자자 칸이 하나도 없다 — 받은 키 {keys_of([row])}")
    if not nonzero:
        raise KisEmpty("FHPTJ04030000: 값이 모두 0 — 장 시작 전·휴장일(0 을 사실로 쓰지 않는다)")
    return out
