"""KIS 투자자별 순매수 파서 — 종목(FHKST01010900)·시장(FHPTJ04040000)·가집계(FHPTJ04400000).

승격 원본: ET `board/ingest/kis.py`(`FIELD`:71, `_f`:165, `market_flows`:170, `stock_flows`:210,
`top_flows`:238), ET `monitor/flow/kissrc.py`(`fetch_daily`:48, `unit_check`:84). 설계
docs/p3_design.md §1.2·§3.5, docs/metrics.md §2.

규칙(ET 그대로 + KBJ 행 자료형)
- 금액은 KIS 백만원 → **원**(× `MILLION_KRW` — [실측 필요 #22], R1). 수량은 주.
- **없는 구분은 행을 만들지 않는다**(0 으로 채우지 않는다). 종목 TR 은 개인·외국인·기관 3구분이라
  기타법인 행이 없다 — 검산 ①(4구분 합 0)은 실데이터에서 **불가**(R2 — 메인 결정: 가능한 검산만 0
  차이, 불가는 사유 기록). 기관 7구분도 이 TR 에 없다.
- 날짜는 **응답이 싣고 온 것**을 쓴다(부르는 쪽 날짜로 덮지 않는다 — 휴장일에 전일 값이 오늘 값으로
  둔갑하지 않게). 날짜가 없는 행은 버린다. 날짜 있는 행이 하나도 없으면 실패.
- 투자자 구분이 **한 행에도** 없으면 실패(키가 바뀐 날 — 사유에 받은 키 이름). 한 행이라도 있으면 그
  행들로 충분하다(당일 행만 아직 빈 날).
- 시장 TR: 가장 최근 날짜에 개인·외국인·기관이 **셋 다 정확히 0** 이면 질의가 성립하지 않은 것으로
  보고 `KisEmpty`(ET :201~208). 다른 날짜의 '전부 0' 행도 사실이 아니라 버린다.
- 가집계(FHPTJ04400000)는 증권사 **추정치** — source `kis.prelim`, quality estimated. 마감 확정
  (FHKST01010900, source `kis`)이 같은 키를 덮고 차이를 남긴다(D-P3-7 — 저장소가 한다).
- 금액 자릿수 대조(`unit_check`, ET kissrc): 금액 합이 수량 × 종가 합과 자릿수가 같아야 한다 (배수
  0.2~5). 단위를 잘못 알면 모든 금액이 10⁶ 배 틀리므로 수집 처리기가 쓰기 전에 본다.

필드 이름: 종목 TR 은 ET 가 쓰던 이름(문서 기준 — 실측 #22). 시장 TR 의 기관 세부·기타법인 칸
(`scrt_`·`ivtr_`·`pe_fund_`·`insu_`·`bank_`·`fund_`·`mrbn_`·`etc_corp_`)은 KIS 문서 기준 **[추정]**
— ET 실측(2026-09-17)이 '30개 주체별 칼럼이 있다'고만 적었다. 칸이 없으면 그 구분은 행이 없다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import IntradayInvestor, Investor, InvestorDay
from kbj.data.private.kis.parse import (
    MILLION_KRW,
    KisEmpty,
    KisParseError,
    has_any,
    keys_of,
    kis_float,
    kis_int,
    pick,
    rows_of,
    venue_param,
    ymd,
)

__all__ = [
    "INST_FOREIGN_WHO",
    "MARKET_FIELDS",
    "PRELIM_SOURCE",
    "SOURCE",
    "STOCK_FIELDS",
    "UnitCheck",
    "closes_of",
    "merge_inst_foreign",
    "on_day",
    "params_inst_foreign",
    "params_market_investor",
    "params_stock_investor",
    "parse_inst_foreign_total",
    "parse_market_investor",
    "parse_stock_investor",
    "prelim_days",
    "unit_check",
]

SOURCE: Final = "kis"
PRELIM_SOURCE: Final = "kis.prelim"

DATE_KEYS: Final = ("stck_bsop_date", "bsop_date", "stck_clpr_date")
CLOSE_KEYS: Final = ("stck_clpr", "stck_prpr")
CODE_KEYS: Final = ("mksc_shrn_iscd", "stck_shrn_iscd", "pdno")


@dataclass(frozen=True)
class Fields:
    """한 투자자 구분의 (금액 후보, 수량 후보)."""

    amount: tuple[str, ...]
    qty: tuple[str, ...]


# 종목 투자자(FHKST01010900) — 3구분(ET FIELD)
STOCK_FIELDS: Final[Mapping[Investor, Fields]] = {
    Investor.INDIVIDUAL: Fields(("prsn_ntby_tr_pbmn",), ("prsn_ntby_qty",)),
    Investor.FOREIGN: Fields(("frgn_ntby_tr_pbmn",), ("frgn_ntby_qty",)),
    Investor.INSTITUTION: Fields(("orgn_ntby_tr_pbmn",), ("orgn_ntby_qty",)),
}

# 시장 투자자(FHPTJ04040000) — 3구분 + [추정] 기타법인·기관 7구분
MARKET_FIELDS: Final[Mapping[Investor, Fields]] = {
    **STOCK_FIELDS,
    Investor.OTHER_CORP: Fields(("etc_corp_ntby_tr_pbmn",), ("etc_corp_ntby_vol",)),
    Investor.FIN_INVEST: Fields(("scrt_ntby_tr_pbmn",), ("scrt_ntby_qty",)),
    Investor.TRUST: Fields(("ivtr_ntby_tr_pbmn",), ("ivtr_ntby_qty",)),
    Investor.PRIVATE_FUND: Fields(("pe_fund_ntby_tr_pbmn",), ("pe_fund_ntby_vol",)),
    Investor.INSURANCE: Fields(("insu_ntby_tr_pbmn",), ("insu_ntby_qty",)),
    Investor.BANK: Fields(("bank_ntby_tr_pbmn",), ("bank_ntby_qty",)),
    Investor.PENSION: Fields(("fund_ntby_tr_pbmn",), ("fund_ntby_qty",)),
    Investor.OTHER_FIN: Fields(("mrbn_ntby_tr_pbmn",), ("mrbn_ntby_qty",)),  # 종금 [추정]
}

# 가집계(FHPTJ04400000) — 외국인·기관 합계만 읽는다(세부 칸은 [실측 필요 #23])
INST_FOREIGN_FIELDS: Final[Mapping[Investor, Fields]] = {
    Investor.FOREIGN: Fields(("frgn_ntby_tr_pbmn",), ("frgn_ntby_qty",)),
    Investor.INSTITUTION: Fields(("orgn_ntby_tr_pbmn",), ("orgn_ntby_qty",)),
}
# 가집계 목록 구분(FID_ETC_CLS_CODE) — 1 외국인 · 2 기관 [추정 — 실측 필요 #23]
INST_FOREIGN_WHO: Final[Mapping[str, str]] = {"foreign": "1", "institution": "2"}
_THREE: Final = (Investor.INDIVIDUAL, Investor.FOREIGN, Investor.INSTITUTION)


# ── 파라미터 ─────────────────────────────────────────────────────────────────────────────


def params_stock_investor(code: str, venue: str = "KRX") -> dict[str, str]:
    """FHKST01010900 — ET ENDPOINTS['stock_investor'] (시장 구분은 거래소 구분 코드)."""
    return {"FID_COND_MRKT_DIV_CODE": venue_param(venue), "FID_INPUT_ISCD": code}


def _krx_only(venue: str, tr: str) -> None:
    if venue != "KRX":
        raise ValueError(f"{tr}: KRX 밖 거래소 구분 파라미터는 실측 전이다(#25): {venue!r}")


def params_market_investor(market: str, day: date, venue: str = "KRX") -> dict[str, str]:
    """FHPTJ04040000 — 시장 구분 'U'(ET 실측 2026-09-17 — 'J' 는 거절), 날짜는 필수 칸이지만
    무시되고 최근 300영업일이 온다(ET 실측). 투자자 구분 자리(`FID_INPUT_ISCD_1`)는 [실측 필요]."""
    _krx_only(venue, "FHPTJ04040000")
    d = f"{day:%Y%m%d}"
    return {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": market,
        "FID_INPUT_DATE_1": d,
        "FID_INPUT_DATE_2": d,
        "FID_INPUT_ISCD_1": "",
        "FID_INPUT_ISCD_2": "",
    }


def params_inst_foreign(market: str, who: str, venue: str = "KRX") -> dict[str, str]:
    """FHPTJ04400000 — ET ENDPOINTS['top_flows'] + 시장(0001·1001)·구분(외국인·기관)
    [실측 필요 #23].

    정렬 0 = 순매수 상위, 금액/수량 구분 0 [추정]."""
    _krx_only(venue, "FHPTJ04400000")
    return {
        "FID_COND_MRKT_DIV_CODE": "V",
        "FID_COND_SCR_DIV_CODE": "16449",
        "FID_INPUT_ISCD": market,
        "FID_DIV_CLS_CODE": "0",
        "FID_RANK_SORT_CLS_CODE": "0",
        "FID_ETC_CLS_CODE": INST_FOREIGN_WHO[who],
    }


# ── 파서 ────────────────────────────────────────────────────────────────────────────────


def _values(row: Mapping[str, Any], f: Fields) -> tuple[int | None, int | None]:
    return kis_int(pick(row, *f.amount), MILLION_KRW), kis_int(pick(row, *f.qty))


def _dated(rows: Sequence[dict[str, Any]], what: str) -> list[tuple[date, dict[str, Any]]]:
    out = [(d, r) for r in rows if (d := ymd(pick(r, *DATE_KEYS))) is not None]
    if rows and not out:
        raise KisParseError(f"{what}: 날짜가 있는 행이 없다 — 받은 키 {keys_of(rows)}")
    return out


def _require_any(
    rows: Sequence[Mapping[str, Any]], fields: Mapping[Investor, Fields], what: str
) -> None:
    keys = [k for f in fields.values() for k in (*f.amount, *f.qty)]
    if not any(has_any(r, keys) for r in rows):
        raise KisParseError(f"{what}: 투자자 구분이 한 행에도 없다 — 받은 키 {keys_of(rows)}")


def _days(
    code: str,
    dated: Sequence[tuple[date, Mapping[str, Any]]],
    fields: Mapping[Investor, Fields],
    *,
    source: str,
    venue: str,
    quality: Quality,
) -> list[InvestorDay]:
    out: list[InvestorDay] = []
    for d, r in dated:
        for inv, f in fields.items():
            amount, qty = _values(r, f)
            if amount is None and qty is None:
                continue  # 없는 구분은 만들지 않는다
            out.append(InvestorDay(code, d, inv, amount, qty, source, venue, quality))
    return out


def parse_stock_investor(
    body: Mapping[str, Any],
    code: str,
    *,
    venue: str = "KRX",
    source: str = SOURCE,
    quality: Quality = Quality.OK,
) -> list[InvestorDay]:
    """FHKST01010900 `output` → 날짜·구분마다 한 행(최근 약 30영업일). 금액은 원."""
    what = f"FHKST01010900 {code}"
    rows = rows_of(body, "output", "output1", what=what)
    if not rows:
        raise KisEmpty(f"{what}: 결과가 비었다")
    dated = _dated(rows, what)
    _require_any([r for _, r in dated], STOCK_FIELDS, what)
    return _days(code, dated, STOCK_FIELDS, source=source, venue=venue, quality=quality)


def closes_of(body: Mapping[str, Any]) -> dict[date, float]:
    """FHKST01010900 행의 날짜별 종가(금액 자릿수 대조용). 없으면 그 날은 빠진다."""
    out: dict[date, float] = {}
    for r in rows_of(body, "output", "output1", what="FHKST01010900"):
        d = ymd(pick(r, *DATE_KEYS))
        c = kis_float(pick(r, *CLOSE_KEYS))
        if d is not None and c:
            out[d] = c
    return out


def parse_market_investor(
    body: Mapping[str, Any],
    market: str,
    *,
    venue: str = "KRX",
    source: str = SOURCE,
    quality: Quality = Quality.OK,
) -> list[InvestorDay]:
    """FHPTJ04040000 `output1`(없으면 `output`) → 시장 코드(0001·1001)를 code 로 한 행들."""
    what = f"FHPTJ04040000 {market}"
    rows = rows_of(body, "output1", "output", what=what)
    if not rows:
        raise KisEmpty(f"{what}: 결과가 비었다")
    dated = _dated(rows, what)
    _require_any([r for _, r in dated], MARKET_FIELDS, what)

    def all_zero(r: Mapping[str, Any]) -> bool:
        vals = [_values(r, STOCK_FIELDS[i])[0] for i in _THREE]
        present = [v for v in vals if v is not None]
        return bool(present) and all(v == 0 for v in present)

    latest = max(d for d, _ in dated)
    if all(all_zero(r) for d, r in dated if d == latest):
        raise KisEmpty(
            f"{what}: {latest} 개인·외국인·기관 순매수가 전부 0 — 질의가 성립하지 않은 것으로 본다"
        )
    kept = [(d, r) for d, r in dated if not all_zero(r)]
    return _days(market, kept, MARKET_FIELDS, source=source, venue=venue, quality=quality)


def parse_inst_foreign_total(
    body: Mapping[str, Any],
    *,
    ts: datetime,
    venue: str = "KRX",
    source: str = PRELIM_SOURCE,
) -> list[IntradayInvestor]:
    """FHPTJ04400000 `output` → 종목·구분마다 한 줄(추정치, quality estimated). rank = 목록 순서.

    빈 목록은 빈 결과(가집계 회차 전 — 실패 아님). 행이 있는데 코드·구분이 하나도 없으면 실패.
    """
    what = "FHPTJ04400000"
    rows = rows_of(body, "output", "output1", what=what)
    if not rows:
        return []
    out: list[IntradayInvestor] = []
    coded = 0
    for i, r in enumerate(rows, start=1):
        code = str(pick(r, *CODE_KEYS) or "").strip()
        if not code:
            continue
        coded += 1
        for inv, f in INST_FOREIGN_FIELDS.items():
            amount, qty = _values(r, f)
            if amount is None and qty is None:
                continue
            out.append(
                IntradayInvestor(code, ts, inv, venue, amount, qty, i, source, Quality.ESTIMATED)
            )
    if not coded:
        raise KisParseError(f"{what}: 종목코드가 한 행에도 없다 — 받은 키 {keys_of(rows)}")
    if not out:
        raise KisParseError(f"{what}: 투자자 구분이 한 행에도 없다 — 받은 키 {keys_of(rows)}")
    return out


_WHO_INVESTOR: Final[Mapping[str, Investor]] = {
    "foreign": Investor.FOREIGN,
    "institution": Investor.INSTITUTION,
}


def merge_inst_foreign(
    lists: Sequence[tuple[str, Sequence[IntradayInvestor]]],
) -> list[IntradayInvestor]:
    """가집계 목록(외국인 순위·기관 순위)을 (종목, 구분, 거래소) 한 줄로 합친다.

    순위(rank)는 **그 구분의 목록에서의 순위**다 — 외국인 목록의 외국인 값, 기관 목록의 기관
    값이 먼저다. 다른 목록에서만 본 구분 값(외국인 목록에 실린 기관 값 등)은 그 자리가 비어 있을
    때만 넣고 순위는 None(그 구분의 순위가 아니다).
    """
    primary: dict[tuple[str, Investor, str], IntradayInvestor] = {}
    secondary: dict[tuple[str, Investor, str], IntradayInvestor] = {}
    for who, rows in lists:
        own = _WHO_INVESTOR[who]
        for q in rows:
            k = (q.code, q.investor, q.venue)
            if q.investor is own:
                primary.setdefault(k, q)
            else:
                secondary.setdefault(k, replace(q, rank=None))
    out = dict(primary)
    for k, q in secondary.items():
        out.setdefault(k, q)
    return [out[k] for k in sorted(out, key=lambda k: (k[0], k[1].value, k[2]))]


def prelim_days(rows: Sequence[IntradayInvestor], day: date) -> list[InvestorDay]:
    """장중 가집계 → 일별 원장 오늘 행(source `kis.prelim`, estimated). (종목, 구분, 거래소)마다
    처음 나온 값 하나(같은 종목이 외국인·기관 목록에 둘 다 나와도 값은 같다)."""
    seen: dict[tuple[str, Investor, str], InvestorDay] = {}
    for q in rows:
        k = (q.code, q.investor, q.venue)
        if k in seen:
            continue
        seen[k] = InvestorDay(
            q.code, day, q.investor, q.net_value, q.net_qty, PRELIM_SOURCE, q.venue,
            Quality.ESTIMATED,
        )  # fmt: skip
    return list(seen.values())


def on_day(rows: Sequence[InvestorDay], day: date) -> list[InvestorDay]:
    """그 날짜의 행만(기준일 뒤 날짜를 고르지 않는다 — ET stockflows `_latest`)."""
    return [r for r in rows if r.date == day]


# ── 금액 자릿수 대조 (ET monitor/flow/kissrc.py:unit_check) ─────────────────────────────

RATIO_LO: Final = 0.2
RATIO_HI: Final = 5.0
RATIO_FLOOR: Final = 100_000_000  # 대조에 쓸 최소 규모(원) — 너무 작으면 비율이 튄다
UNIT_LABEL: Final = "금액 자릿수 대조(수량x종가)"


@dataclass(frozen=True)
class UnitCheck:
    label: str
    ratio: float | None
    ok: bool


def unit_check(rows: Sequence[InvestorDay], closes: Mapping[tuple[str, date], float]) -> UnitCheck:
    """금액이 수량 × 종가와 자릿수가 같은가(ET 그대로). closes 는 (종목, 날짜) → 종가.

    대조할 것이 모자라면 **통과라고 하지 않는다**(ok=False, ratio None)."""
    got = ref = 0.0
    for r in rows:
        c = closes.get((r.code, r.date))
        if not c or r.net_value is None or r.net_qty is None:
            continue
        got += abs(r.net_value)
        ref += abs(r.net_qty) * c
    if ref < RATIO_FLOOR or got <= 0:
        return UnitCheck(UNIT_LABEL, None, False)
    ratio = got / ref
    return UnitCheck(UNIT_LABEL, round(ratio, 3), RATIO_LO <= ratio <= RATIO_HI)
