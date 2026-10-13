"""KIS 프로그램매매 TR 파서 — 시장 일별·장중·투자자별 당일·종목 장중·종목 일별(P5 F2).

근거: docs/p5_flow_program_prompt.md §1.4, docs/p5_design.md §1.7·§3.3·§4.7, docs/metrics.md §22.
TR·경로는 KIS 공식 예제(`koreainvestment/open-trading-api` `examples_llm/domestic_stock`) 기준이고,
**입력 칸·응답 칸 이름·단위·반영 시각은 전부 [확인 필요 #40·#41]**(키를 받기 전 — 실호출로 확인한
적 없다). 그래서 수집 작업(`program.*`)은 등록부에서 꺼져 있다. 실측 뒤에는 이 파일의 상수 표만
고친다(가짜 KIS·시험·합성 fixture 가 같은 표를 import 한다 — 문자열을 다시 쓰지 않는다).

규칙
- 시장 표는 긴 형식: kind(`arbt` 차익·`nabt` 비차익·`whol` 전체) × side(`entm` 위탁·`onsl` 자기·
  `smtn` 합계) × 매도·매수·순매수 × 수량·대금. 칸 이름은 `<kind>_<side>_<측정 접미사>`(예제
  `chk_*.py`).
  매수 칸은 예제 표기 '매수2'(`shnu2`)와 `shnu` 를 다 후보로 본다.
- **금액은 원 정수로 바꿔 넘긴다**(metrics §0·§22 — 저장 원). 시장 TR 대금 단위는 백만원 가정
  (`MARKET_VALUE_UNIT_KRW`), 종목 TR 대금 단위는 원 가정(`STOCK_VALUE_UNIT_KRW`) — 둘 다 [확인 필요
  #40·#41]. 틀리면 금액이 10⁶ 배 틀리므로 종목 행은 `stock_unit_ratio`(대금 ÷ (수량 × 현재가))로
  자릿수를 대조할 수 있게 한다(투자자 파서 `unit_check` 와 같은 생각).
- 빈 칸·`-` 는 None(0 으로 채우지 않는다). 날짜는 **응답이 싣고 온 것**만 쓴다(부르는 쪽 날짜로 덮지
  않는다) — 원하는 날짜 행이 없으면 `KisEmpty`(공표 전·휴장일).
- 한 (kind, side) 의 여섯 칸이 모두 비면 그 행은 없다. 9칸이 하나도 없으면 `KisShapeError`
  (받은 키 이름만).
- 값이 모두 0 이면 `KisEmpty`(장 시작 전 — 0 을 사실로 쓰지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from kbj.core.p5_rows import (
    PROGRAM_KINDS,
    PROGRAM_SIDES,
    ProgramInvestorRow,
    ProgramMarketRow,
    ProgramStockRow,
)
from kbj.core.quality import Quality
from kbj.data.private.kis.parse import (
    MILLION_KRW,
    KisEmpty,
    KisShapeError,
    TrSpec,
    keys_of,
    kis_float,
    kis_int,
    pick,
    rows_of,
    venue_param,
    ymd,
)

__all__ = [
    "INVESTOR_CODES",
    "INVESTOR_MARKET",
    "MARKET_VALUE_UNIT_KRW",
    "PROGRAM_TRS",
    "SOURCE",
    "STOCK_VALUE_UNIT_KRW",
    "Measure",
    "investor_field",
    "market_field",
    "params_investor_today",
    "params_market_daily",
    "params_market_intraday",
    "params_stock_daily",
    "params_stock_intraday",
    "parse_investor_today",
    "parse_market_daily",
    "parse_market_intraday",
    "parse_stock_daily",
    "parse_stock_intraday",
    "stock_field",
    "stock_unit_ratio",
]

SOURCE: Final = "KIS"
Market = Literal["K", "Q"]
Venue = Literal["KRX", "NXT", "TOTAL"]
Measure = Literal["sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", "net_value"]
MEASURE_NAMES: Final[tuple[Measure, ...]] = (
    "sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", "net_value",
)  # fmt: skip

# 데이터셋 이름 → TR(공식 예제 — 경로·tr_id). 데이터셋 행은 kbj/data/private/kis/datasets.py.
PROGRAM_TRS: Final[Mapping[str, TrSpec]] = {
    "program_market_daily": TrSpec(
        "FHPPG04600001", "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily"
    ),
    "program_market_intraday": TrSpec(
        "FHPPG04600101", "/uapi/domestic-stock/v1/quotations/comp-program-trade-today"
    ),
    "program_investor_today": TrSpec(
        "HHPPG046600C1", "/uapi/domestic-stock/v1/quotations/investor-program-trade-today"
    ),
    "program_stock_intraday": TrSpec(
        "FHPPG04650101", "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
    ),
    "program_stock_daily": TrSpec(
        "FHPPG04650201", "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily"
    ),
}

# 대금 단위(원으로 바꿀 배수) — [확인 필요 #40·#41]
MARKET_VALUE_UNIT_KRW: Final = MILLION_KRW  # 시장 일별·장중·투자자별 — 백만원 가정
STOCK_VALUE_UNIT_KRW: Final = 1  # 종목 장중·일별 — 원 가정

# 측정값 → 칸 접미사 후보(시장 표). 매수는 예제 표기 '매수2'(shnu2) 먼저 [확인 필요 #40]
_MARKET_SFX: Final[Mapping[Measure, tuple[str, ...]]] = {
    "sell_qty": ("seln_vol",),
    "buy_qty": ("shnu2_vol", "shnu_vol"),
    "net_qty": ("ntby_qty", "ntby_vol"),
    "sell_value": ("seln_tr_pbmn",),
    "buy_value": ("shnu2_tr_pbmn", "shnu_tr_pbmn"),
    "net_value": ("ntby_tr_pbmn",),
}
# 종목 표 — 전체(whol) 합계(smtn) 순 [확인 필요 #41]
_STOCK_SFX: Final[Mapping[Measure, tuple[str, ...]]] = {
    "sell_qty": ("whol_smtn_seln_vol",),
    "buy_qty": ("whol_smtn_shnu_vol",),
    "net_qty": ("whol_smtn_ntby_qty",),
    "sell_value": ("whol_smtn_seln_tr_pbmn",),
    "buy_value": ("whol_smtn_shnu_tr_pbmn",),
    "net_value": ("whol_smtn_ntby_tr_pbmn",),
}
# 투자자별 당일 — `<kind>_<접미사>` [확인 필요 #40]
_INVESTOR_SFX: Final[Mapping[Measure, tuple[str, ...]]] = {
    "sell_qty": ("seln_vol", "seln_qty"),
    "buy_qty": ("shnu_vol", "shnu_qty"),
    "net_qty": ("ntby_qty", "ntby_vol"),
    "sell_value": ("seln_tr_pbmn",),
    "buy_value": ("shnu_tr_pbmn",),
    "net_value": ("ntby_tr_pbmn",),
}
# 투자자별 당일 TR 의 시장 입력(1 코스피·4 코스닥 — F2 §1.4)
INVESTOR_MARKET: Final[Mapping[str, str]] = {"K": "1", "Q": "4"}
# 투자자 구분 코드 → 소문자 이름(metrics §21 12구분과 같은 말) — 응답 칸·코드 [확인 필요 #40]
INVESTOR_CODES: Final[Mapping[str, str]] = {
    "1000": "scrt",  # 금융투자
    "2000": "insu",  # 보험
    "3000": "ivtr",  # 투신
    "3100": "pe_fund",  # 사모
    "4000": "bank",  # 은행
    "5000": "mrbn",  # 종금(기타금융)
    "6000": "fund",  # 연기금
    "7000": "etc_corp",  # 기타법인
    "7100": "etc_orgt",  # 기타단체
    "8000": "prsn",  # 개인
    "9000": "frgn",  # 외국인
    "9999": "orgn",  # 기관 합계
}
_INVESTOR_CODE_KEYS: Final = ("invr_cls_code", "invr_code")
_DATE_KEYS: Final = ("stck_bsop_date", "bsop_date")
_HOUR_KEYS: Final = ("bsop_hour", "stck_cntg_hour")
_INDEX_KEYS: Final = ("bstp_nmix_prpr", "nmix_prpr")
_PRICE_KEYS: Final = ("stck_prpr", "stck_clpr")
_CHG_KEYS: Final = ("prdy_ctrt",)


def market_field(kind: str, side: str, measure: Measure) -> tuple[str, ...]:
    """시장 표 칸 이름 후보(가짜 서버·시험이 같은 함수를 쓴다)."""
    return tuple(f"{kind}_{side}_{s}" for s in _MARKET_SFX[measure])


def investor_field(kind: str, measure: Measure) -> tuple[str, ...]:
    return tuple(f"{kind}_{s}" for s in _INVESTOR_SFX[measure])


def stock_field(measure: Measure) -> tuple[str, ...]:
    return _STOCK_SFX[measure]


# ── 입력 ────────────────────────────────────────────────────────────────────────────────


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _market(market: str) -> Market:
    if market not in ("K", "Q"):
        raise ValueError(f"프로그램 시장은 K(코스피)·Q(코스닥): {market!r}")
    return market  # pyright: ignore[reportReturnType]


def params_market_daily(market: str, venue: str, start: date, end: date) -> dict[str, str]:
    """FHPPG04600001 — 거래소·시장·시작·끝 날짜(구간 상한 [확인 필요 #40])."""
    if start > end:
        raise ValueError("시작 날짜가 끝 날짜보다 뒤다")
    return {
        "FID_COND_MRKT_DIV_CODE": venue_param(venue),
        "FID_MRKT_CLS_CODE": _market(market),
        "FID_INPUT_DATE_1": _ymd(start),
        "FID_INPUT_DATE_2": _ymd(end),
    }


def params_market_intraday(market: str, venue: str) -> dict[str, str]:
    """FHPPG04600101 — 거래소·시장·구간·시각. 구간(`FID_SCTN_CLS_CODE`)·시각(`FID_INPUT_HOUR_1`)
    입력의 뜻은 [확인 필요 #40] — 빈 값(전체·최근)으로 부른다."""
    return {
        "FID_COND_MRKT_DIV_CODE": venue_param(venue),
        "FID_MRKT_CLS_CODE": _market(market),
        "FID_SCTN_CLS_CODE": "",
        "FID_INPUT_ISCD": "",
        "FID_COND_MRKT_DIV_CODE1": "",
        "FID_INPUT_HOUR_1": "",
    }


def params_investor_today(market: str, venue: str) -> dict[str, str]:
    """HHPPG046600C1 — 거래소·시장(1 코스피·4 코스닥). 입력 칸 이름 [확인 필요 #40]."""
    return {
        "EXCH_DIV_CLS_CODE": venue_param(venue),
        "MRKT_DIV_CLS_CODE": INVESTOR_MARKET[_market(market)],
    }


def params_stock_intraday(code: str, venue: str) -> dict[str, str]:
    """FHPPG04650101 — 거래소·종목코드."""
    return {"FID_COND_MRKT_DIV_CODE": venue_param(venue), "FID_INPUT_ISCD": code}


def params_stock_daily(code: str, venue: str, day: date) -> dict[str, str]:
    """FHPPG04650201 — 거래소·종목코드·날짜(그날 이하 일별 [확인 필요 #41])."""
    return {
        "FID_COND_MRKT_DIV_CODE": venue_param(venue),
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": _ymd(day),
    }


# ── 응답 ────────────────────────────────────────────────────────────────────────────────


def _measures(
    row: Mapping[str, Any], names: Mapping[Measure, tuple[str, ...]], scale: int
) -> dict[Measure, int | None]:
    out: dict[Measure, int | None] = {}
    for m in MEASURE_NAMES:
        is_value = m.endswith("_value")
        out[m] = kis_int(pick(row, *names[m]), scale if is_value else 1)
    return out


@dataclass(frozen=True)
class _Ctx:
    trade_date: date
    market: Market
    venue: Venue
    source: str
    quality: Quality
    ts: datetime | None


def _market_rows(row: Mapping[str, Any], c: _Ctx, what: str) -> list[ProgramMarketRow]:
    idx = kis_float(pick(row, *_INDEX_KEYS))
    out: list[ProgramMarketRow] = []
    nonzero = False
    for kind in PROGRAM_KINDS:
        for side in PROGRAM_SIDES:
            names: dict[Measure, tuple[str, ...]] = {
                m: market_field(kind, side, m) for m in MEASURE_NAMES
            }
            v = _measures(row, names, MARKET_VALUE_UNIT_KRW)
            if all(x is None for x in v.values()):
                continue
            nonzero = nonzero or any(x not in (None, 0) for x in v.values())
            out.append(
                ProgramMarketRow(
                    trade_date=c.trade_date, market=c.market, venue=c.venue,
                    kind=kind, side=side,  # pyright: ignore[reportArgumentType]
                    sell_qty=v["sell_qty"], buy_qty=v["buy_qty"], net_qty=v["net_qty"],
                    sell_value=v["sell_value"], buy_value=v["buy_value"],
                    net_value=v["net_value"], index_close=idx, source=c.source,
                    quality=c.quality, ts=c.ts,
                )
            )  # fmt: skip
    if not out:
        raise KisShapeError(f"{what}: 차익·비차익·전체 칸이 하나도 없다 — 받은 키 {keys_of([row])}")
    if not nonzero:
        raise KisEmpty(f"{what}: 값이 모두 0 — 공표 전(0 을 사실로 쓰지 않는다)")
    return out


def parse_market_daily(
    body: Mapping[str, Any],
    *,
    day: date,
    market: str,
    venue: str,
    quality: Quality = Quality.OK,
) -> list[ProgramMarketRow]:
    """FHPPG04600001 → 그날(`day`) 9행(원). 응답에 그날 행이 없으면 `KisEmpty`(공표 전·휴장일)."""
    rows = rows_of(body, "output", "output1", what="FHPPG04600001")
    mine = [r for r in rows if ymd(pick(r, *_DATE_KEYS)) == day]
    if not mine:
        raise KisEmpty(f"FHPPG04600001: {day} 행이 없다(받은 {len(rows)}행)")
    c = _Ctx(day, _market(market), venue, SOURCE, quality, None)  # pyright: ignore[reportArgumentType]
    return _market_rows(mine[0], c, "FHPPG04600001")


def parse_market_intraday(
    body: Mapping[str, Any],
    *,
    ts: datetime,
    trade_date: date,
    market: str,
    venue: str,
    quality: Quality = Quality.OK,
) -> list[ProgramMarketRow]:
    """FHPPG04600101 → 가장 늦은 시각 행(오늘 누적) 9행(원). ts 는 부르는 쪽 슬롯 분."""
    rows = rows_of(body, "output", "output1", what="FHPPG04600101")
    if not rows:
        raise KisEmpty("FHPPG04600101: 결과 행이 없다")
    timed = [r for r in rows if pick(r, *_HOUR_KEYS) is not None]
    latest = max(timed, key=lambda r: str(pick(r, *_HOUR_KEYS))) if timed else rows[0]
    c = _Ctx(trade_date, _market(market), venue, SOURCE, quality, ts)  # pyright: ignore[reportArgumentType]
    return _market_rows(latest, c, "FHPPG04600101")


def parse_investor_today(
    body: Mapping[str, Any],
    *,
    trade_date: date,
    market: str,
    venue: str,
    quality: Quality = Quality.OK,
) -> list[ProgramInvestorRow]:
    """HHPPG046600C1 → 투자자 × kind 행(원). 모르는 구분 코드는 버린다(사유는 세지 않는다 — 코드 표
    [확인 필요 #40]). 한 행도 못 만들면 `KisShapeError`."""
    rows = rows_of(body, "output", "output1", what="HHPPG046600C1")
    if not rows:
        raise KisEmpty("HHPPG046600C1: 결과 행이 없다")
    mkt = _market(market)
    out: list[ProgramInvestorRow] = []
    for r in rows:
        inv = INVESTOR_CODES.get(str(pick(r, *_INVESTOR_CODE_KEYS) or "").strip())
        if inv is None:
            continue
        for kind in PROGRAM_KINDS:
            names: dict[Measure, tuple[str, ...]] = {
                m: investor_field(kind, m) for m in MEASURE_NAMES
            }
            v = _measures(r, names, MARKET_VALUE_UNIT_KRW)
            if all(x is None for x in v.values()):
                continue
            out.append(
                ProgramInvestorRow(
                    trade_date=trade_date, market=mkt, venue=venue,  # pyright: ignore[reportArgumentType]
                    investor=inv, kind=kind,  # pyright: ignore[reportArgumentType]
                    sell_qty=v["sell_qty"], buy_qty=v["buy_qty"], net_qty=v["net_qty"],
                    sell_value=v["sell_value"], buy_value=v["buy_value"],
                    net_value=v["net_value"], source=SOURCE, quality=quality,
                )
            )  # fmt: skip
    if not out:
        raise KisShapeError(f"HHPPG046600C1: 투자자 행을 못 만들었다 — 받은 키 {keys_of(rows)}")
    return out


def _stock_row(
    r: Mapping[str, Any],
    *,
    code: str,
    trade_date: date,
    market: str,
    venue: str,
    scope: Literal["scan", "sweep"],
    quality: Quality,
    ts: datetime | None,
    what: str,
) -> ProgramStockRow:
    v = _measures(r, _STOCK_SFX, STOCK_VALUE_UNIT_KRW)
    if all(x is None for x in v.values()):
        raise KisShapeError(f"{what}: 프로그램 칸이 없다 — 받은 키 {keys_of([r])}")
    return ProgramStockRow(
        trade_date=trade_date, code=code, market=_market(market),
        venue=venue,  # pyright: ignore[reportArgumentType]
        scope=scope, price=kis_float(pick(r, *_PRICE_KEYS)),
        chg_pct=kis_float(pick(r, *_CHG_KEYS)),
        sell_qty=v["sell_qty"], buy_qty=v["buy_qty"], net_qty=v["net_qty"],
        sell_value=v["sell_value"], buy_value=v["buy_value"], net_value=v["net_value"],
        source=SOURCE, quality=quality, ts=ts,
    )  # fmt: skip


def parse_stock_intraday(
    body: Mapping[str, Any],
    *,
    code: str,
    ts: datetime,
    trade_date: date,
    market: str,
    venue: str,
    quality: Quality = Quality.ESTIMATED,
) -> ProgramStockRow:
    """FHPPG04650101 → 가장 늦은 체결 시각 행(오늘 누적) 한 행(scope scan). ts 는 슬롯."""
    rows = rows_of(body, "output", "output1", what="FHPPG04650101")
    if not rows:
        raise KisEmpty(f"FHPPG04650101 {code}: 결과 행이 없다")
    timed = [r for r in rows if pick(r, *_HOUR_KEYS) is not None]
    latest = max(timed, key=lambda r: str(pick(r, *_HOUR_KEYS))) if timed else rows[0]
    return _stock_row(
        latest, code=code, trade_date=trade_date, market=market, venue=venue, scope="scan",
        quality=quality, ts=ts, what=f"FHPPG04650101 {code}",
    )  # fmt: skip


def parse_stock_daily(
    body: Mapping[str, Any],
    *,
    code: str,
    day: date,
    market: str,
    venue: str,
    quality: Quality = Quality.OK,
) -> ProgramStockRow:
    """FHPPG04650201 → 그날 행 하나(scope sweep). 그날 행이 없으면 `KisEmpty`."""
    rows = rows_of(body, "output", "output1", what="FHPPG04650201")
    mine = [r for r in rows if ymd(pick(r, *_DATE_KEYS)) == day]
    if not mine:
        raise KisEmpty(f"FHPPG04650201: 그날 행이 없다(받은 {len(rows)}행)")
    return _stock_row(
        mine[0], code=code, trade_date=day, market=market, venue=venue, scope="sweep",
        quality=quality, ts=None, what="FHPPG04650201",
    )  # fmt: skip


def stock_unit_ratio(rows: Sequence[ProgramStockRow]) -> float | None:
    """금액 자릿수 대조 — Σ(매수+매도 대금) ÷ Σ((매수+매도 수량) × 현재가). 단위를 맞게 알면 1 근처
    (0.2~5 — 투자자 파서 `unit_check` 와 같은 범위). 대조할 행이 없으면 None."""
    num = den = 0.0
    for r in rows:
        if None in (r.price, r.buy_value, r.sell_value, r.buy_qty, r.sell_qty):
            continue
        num += float((r.buy_value or 0) + (r.sell_value or 0))
        den += float((r.buy_qty or 0) + (r.sell_qty or 0)) * float(r.price or 0.0)
    return None if den <= 0 else num / den
