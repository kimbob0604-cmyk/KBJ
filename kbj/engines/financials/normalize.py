"""분기 정규화 — DART 재무제표 응답 → `pub_fin.quarterly` 행(`QuarterRow`, 원 단위·분기 단독).

docs/p4_design.md §1.5·§4.5, docs/metrics.md §9, D-P4-5(재무 2층·분기 환산 하나).

- 분기 단독 값은 **dart-report `statements.to_quarterly` 하나**로 만든다(ADR 0001 Q4 기본값). 보고서
  응답 → `Period`(같은 `_pick` 규칙 — legacy 와 같은 값)에 이 모듈이 더 뽑는
  계정(지배순이익·주당이익· 감가상각·차입금·지배자본·비지배지분·단기금융상품)을 얹어 `to_quarterly`
  에 넣는다.
- 연결(CFS)·별도(OFS)는 **따로** 환산한다 — 한 분기는 CFS, 직전은 OFS 인 차분(legacy `fetch_periods`
  의 분기별 폴백)이 섞이지 않게. 저장 키에 `fs_div` 가 있다. 읽는 쪽이 CFS 우선(§4.5).
- 3개월값 대조(ET board): 분기·반기 보고서의 당기 3개월값(`thstrm_amount`)은 값으로 쓰지 않고
  `reported_3m` 에 남긴다. 차분값과 1% 넘게 다르면 quality=estimated + 사유('정정 반영 차이').
- PIT(D-P4-11): 행의 `rcept_dt` = 그 분기 값을 만든 보고서들의 접수일 중 늦은 것(차분이면 직전
  보고서도) — 접수번호 앞 8자리(YYYYMMDD). 모르면 None(PIT 계산에서 빠진다).
- 주요계정 층(`fnlttMultiAcnt` — 원시 행을 저장하지 않는다)은 직전 분기 누적을 이미 저장한 분기
  단독 행의 합으로 되살려(`cumulative_from_quarters`) 같은 `to_quarterly` 에 넣는다. 되살릴 수
  없으면(분기가 비었거나 차분 불가) 수집 쪽이 직전 보고서를 받아 그 누적을 쓴다(2026-10-21 —
  한 분기가 비면 그 해 남은 분기가 전부 '차분 불가'로 굳었다).

[확인 필요 #35] 이 모듈이 더 뽑는 계정의 태그·이름(`EXTRA`), 현금흐름표 감가상각 칸이 누적인지,
전체 재무제표에 줄이 없으면 0 으로 보는 규칙(차입금·단기금융상품 — **전체 재무제표 층만**.
주요계정 층은 그 줄이 응답에 아예 없으므로 None), 금융업 매출 동의어(보험수익·순영업수익 — legacy
매출이 비었을 때만). 실측으로 다르면 `EXTRA` 한 곳.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from kbj.core.fin_rows import REPRT, CorpProfile, QuarterRow, StatementRaw
from kbj.core.quality import Quality
from kbj.engines.financials.period import fiscal_quarter
from kbj.engines.financials.statements import (
    BS_KEYS,
    EOK,
    TAGS,
    Period,
    _norm,
    _pick,
    _to_num,
    to_quarterly,
)

__all__ = [
    "DIFF3M_TOL_PCT",
    "EXTRA",
    "FLOW_MAP",
    "Report",
    "cumulative_from_quarters",
    "period_of",
    "quarter_rows",
    "rcept_date",
    "report_of_raw",
]

Kind = Literal["is", "bs", "cf"]
DIFF3M_TOL_PCT: Final = 1.0  # 3개월값 대조 허용(%) — 넘으면 estimated(§4.5)

# 더 뽑는 계정: 키 → (account_id 후보, account_nm 후보, 재무제표 구분) [확인 필요 #35]
EXTRA: Final[Mapping[str, tuple[tuple[str, ...], tuple[str, ...], Kind]]] = {
    "ni_owner": (
        ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),
        (
            "지배기업의소유주에게귀속되는당기순이익",
            "지배기업소유주지분순이익",
            "지배기업소유주지분",
        ),
        "is",
    ),
    "eps_basic": (
        ("ifrs-full_BasicEarningsLossPerShare",),
        ("기본주당이익", "기본주당순이익", "기본주당이익(손실)", "기본주당순이익(손실)"),
        "is",
    ),
    "dep": (("ifrs-full_AdjustmentsForDepreciationExpense",), ("감가상각비",), "cf"),
    "amort": (("ifrs-full_AdjustmentsForAmortisationExpense",), ("무형자산상각비",), "cf"),
    "equity_owner": (
        ("ifrs-full_EquityAttributableToOwnersOfParent",),
        ("지배기업의소유주에게귀속되는자본", "지배기업소유주지분"),
        "bs",
    ),
    "minority": (("ifrs-full_NoncontrollingInterests",), ("비지배지분",), "bs"),
    # 금융업 매출 자리 동의어(§9 '금융업' — ET NAMES:37). '영업수익'은 legacy TAGS 에 이미 있다 —
    # 이 키는 금융업이고 legacy 매출이 비었을 때만 쓴다(`quarter_rows`)
    "fin_revenue": (("ifrs-full_InsuranceRevenue",), ("보험수익", "순영업수익"), "is"),
    "short_inv": (
        ("dart_ShortTermDepositsNotClassifiedAsCashEquivalents",),
        ("단기금융상품",),
        "bs",
    ),
    "short_borrow": (("ifrs-full_ShorttermBorrowings",), ("단기차입금",), "bs"),
    "current_ltd": (
        ("ifrs-full_CurrentPortionOfLongtermBorrowings",),
        ("유동성장기부채", "유동성장기차입금"),
        "bs",
    ),
    "bonds": (("dart_BondsIssued",), ("사채",), "bs"),
    "long_borrow": (("ifrs-full_LongtermBorrowings",), ("장기차입금",), "bs"),
}
_DEBT: Final = ("short_borrow", "current_ltd", "bonds", "long_borrow")
_SJ: Final[Mapping[Kind, frozenset[str]]] = {
    "is": frozenset({"IS", "CIS"}),
    "bs": frozenset({"BS"}),
    "cf": frozenset({"CF"}),
}

# to_quarterly 행의 키(legacy 이름) → QuarterRow 칸(원)
FLOW_MAP: Final[Mapping[str, str]] = {
    "revenue": "revenue",
    "cogs": "cogs",
    "gross_profit": "gross_profit",
    "sgna": "sgna",
    "operating_income": "op",
    "pretax_income": "pretax",
    "tax": "tax",
    "net_income": "ni",
    "ni_owner": "ni_owner",
    "d_and_a": "d_and_a",
}
_BAL_MAP: Final[Mapping[str, str]] = {
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "total_equity": "total_equity",
    "cash": "cash",
    "equity_owner": "equity_owner",
    "minority": "minority",
    "short_inv": "short_inv",
    "total_debt": "total_debt",
}
# 3개월값 대조 칸(QuarterRow.reported_3m 키) ← legacy TAGS 키
_THREE_M: Final[Mapping[str, str]] = {
    "revenue": "revenue",
    "operating_income": "op",
    "pretax_income": "pretax",
    "net_income": "ni",
}
_QLABEL: Final[Mapping[str, int]] = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}


@dataclass(frozen=True)
class Report:
    """보고서 하나의 정규화 입력 — `Period`(누적·상태표) + 3개월값 + 접수일·통화."""

    period: Period
    reprt_code: str
    rcept_dt: date | None
    reported_3m: Mapping[str, int]
    currency: str


def rcept_date(rcept_no: str | None) -> date | None:
    """DART 접수번호 앞 8자리(YYYYMMDD) → 접수일. 형식이 아니면 None."""
    if not rcept_no or len(rcept_no) < 8 or not rcept_no[:8].isdigit():
        return None
    try:
        return date(int(rcept_no[:4]), int(rcept_no[4:6]), int(rcept_no[6:8]))
    except ValueError:
        return None


def _value(r: Mapping[str, Any], kind: Kind) -> float | None:
    if kind == "bs":
        return _to_num(r.get("thstrm_amount"))
    add = _to_num(r.get("thstrm_add_amount"))
    return add if add is not None else _to_num(r.get("thstrm_amount"))


def _pick_extra(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """`EXTRA` 계정 하나 — account_id 완전일치, 없으면 같은 재무제표 구분의 account_nm."""
    ids, names, kind = EXTRA[key]
    id_set = {i.lower() for i in ids}
    name_set = {_norm(n) for n in names}
    for r in rows:
        if str(r.get("account_id") or "").lower() in id_set:
            v = _value(r, kind)
            if v is not None:
                return v
    for r in rows:
        if r.get("sj_div") in _SJ[kind] and _norm(r.get("account_nm")) in name_set:
            v = _value(r, kind)
            if v is not None:
                return v
    return None


def _three_month(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """분기·반기 보고서의 당기 3개월값(손익 — `thstrm_add_amount` 가 있는 행의 `thstrm_amount`)."""
    ids, names = TAGS[key]
    id_set = {i.lower() for i in ids}
    name_set = {_norm(n) for n in names}
    for match in (
        lambda r: str(r.get("account_id") or "").lower() in id_set,
        lambda r: r.get("sj_div") in ("IS", "CIS") and _norm(r.get("account_nm")) in name_set,
    ):
        for r in rows:
            if match(r) and r.get("thstrm_add_amount") not in (None, ""):
                v = _to_num(r.get("thstrm_amount"))
                if v is not None:
                    return v
    return None


def period_of(year: int, reprt_code: str, fs_div: str, rows: Sequence[Mapping[str, Any]]) -> Period:
    """응답 행 → `Period`(legacy `fetch_periods` 와 같은 `_pick` + 더 뽑는 계정)."""
    raw = [dict(r) for r in rows]
    p = Period(year=year, quarter=REPRT[reprt_code][0], fs_div=fs_div, raw=raw)
    for key in TAGS:
        v = _pick(raw, key)
        if v is None:
            continue
        (p.balance if key in BS_KEYS else p.cumulative)[key] = v
    extra = {k: _pick_extra(raw, k) for k in EXTRA}
    for k in ("ni_owner", "eps_basic", "fin_revenue"):
        v = extra[k]
        if v is not None:
            p.cumulative[k] = v
    dep, amort = extra["dep"], extra["amort"]
    if dep is not None or amort is not None:
        p.cumulative["d_and_a"] = (dep or 0.0) + (amort or 0.0)
    for k in ("equity_owner", "minority", "short_inv"):
        v = extra[k]
        if v is not None:
            p.balance[k] = v
    if "total_assets" in p.balance:
        # 전체 재무제표에 줄이 없으면 그 차입·단기금융상품은 없다(0) [확인 필요 #35]
        p.balance["total_debt"] = sum(extra[k] or 0.0 for k in _DEBT)
        p.balance.setdefault("short_inv", 0.0)
    return p


def report_of_raw(raw: StatementRaw) -> Report:
    """`pub_fin.statement_raw` 한 건 → `Report`."""
    rows = raw.rows
    p = period_of(raw.bsns_year, raw.reprt_code, raw.fs_div, rows)
    three: dict[str, int] = {}
    if raw.reprt_code != "11011":
        for key, col in _THREE_M.items():
            v = _three_month(rows, key)
            if v is not None:
                three[col] = round(v)
    rcept = raw.rcept_no or next((str(r["rcept_no"]) for r in rows if r.get("rcept_no")), None)
    currency = raw.currency or next((str(r["currency"]) for r in rows if r.get("currency")), "KRW")
    return Report(p, raw.reprt_code, rcept_date(rcept), three, currency)


def cumulative_from_quarters(
    quarters: Iterable[QuarterRow], year: int, upto_q: int, fs_div: str
) -> Report | None:
    """저장한 분기 단독 행(1 … upto_q)의 합 → 그 분기까지의 누적 `Report`(주요계정 층 차분용).

    분기가 하나라도 없거나 차분을 못 한(derived — 손익 칸이 전부 None) 분기가 있으면 None — 호출자가
    직전 보고서 누적을 따로 받는다(그 분기를 '직전 누적 있음'으로 보고 손익 칸만 None 으로 비우지
    않게). 칸이 None 인 분기가 있으면 그 칸은 누적에서 빠진다(→ 차분 None).
    """
    rows = {r.fiscal_q: r for r in quarters if r.fiscal_year == year and r.fs_div == fs_div}
    if any(q not in rows or rows[q].derived for q in range(1, upto_q + 1)):
        return None
    span = [rows[q] for q in range(1, upto_q + 1)]
    p = Period(year=year, quarter=f"{upto_q}Q", fs_div=fs_div)
    for legacy_key, col in FLOW_MAP.items():
        vals = [getattr(r, col) for r in span]
        if all(v is not None for v in vals):
            p.cumulative[legacy_key] = float(sum(vals))
    when = _latest([r.rcept_dt for r in span])
    reprt = next(k for k, (lab, _m) in REPRT.items() if lab == f"{upto_q}Q")
    return Report(p, reprt, when, {}, span[-1].currency)


def _latest(dates: Sequence[date | None]) -> date | None:
    """모두 알면 가장 늦은 날, 하나라도 모르면 None."""
    known = [d for d in dates if d is not None]
    return max(known) if known and len(known) == len(dates) else None


def _fs(value: str) -> Literal["CFS", "OFS"]:
    if value in ("CFS", "OFS"):
        return "CFS" if value == "CFS" else "OFS"
    raise ValueError(f"fs_div 는 CFS·OFS: {value!r}")


def _won(v: Any) -> int | None:
    if v is None:
        return None
    return round(float(v) * EOK)


def quarter_rows(
    reports: Sequence[Report],
    profile: CorpProfile | None,
    *,
    tier: Literal["main", "full"],
    corp_code: str,
    financial_prefixes: Iterable[str] = (),
    keep: Iterable[tuple[int, int]] | None = None,
) -> list[QuarterRow]:
    """한 회사·한 기준(fs_div)의 보고서들 → 분기 단독 `QuarterRow`(원).

    `keep` 을 주면 그 (사업연도, 분기)만 돌려준다(되살린 누적 보고서의 분기는 다시 쓰지 않게).
    """
    if not reports:
        return []
    fs_divs = {r.period.fs_div for r in reports}
    if len(fs_divs) != 1:
        raise ValueError(f"한 기준(fs_div)씩 정규화한다: {sorted(fs_divs)}")
    fs = next(iter(fs_divs))
    by_key = {(r.period.year, r.period.quarter): r for r in reports}
    acc_mt = profile.acc_mt if profile is not None else None
    financial = bool(profile and profile.is_financial(financial_prefixes))
    keep_set = set(keep) if keep is not None else None
    out: list[QuarterRow] = []
    for row in to_quarterly([r.period for r in reports]):
        year, qlabel = row["year"], row["quarter"]
        q = _QLABEL[qlabel]
        if keep_set is not None and (year, q) not in keep_set:
            continue
        rep = by_key[(year, qlabel)]
        prev = by_key.get((year, f"{q - 1}Q")) if q > 1 else None
        notes: list[str] = []
        quality = Quality.OK
        per = fiscal_quarter(year, q, acc_mt)
        if acc_mt is None:
            notes.append("결산월 모름 — 12월 결산으로 가정 [확인 필요 #34]")
            quality = Quality.ESTIMATED
        rcept_dt = _latest([rep.rcept_dt] + ([prev.rcept_dt] if prev is not None else []))
        if rcept_dt is not None and rcept_dt < per.end:
            notes.append("접수일이 분기말보다 앞 — PIT 기준일을 비웠다")
            rcept_dt = None
        vals: dict[str, Any] = {col: _won(row.get(k)) for k, col in FLOW_MAP.items()}
        vals.update({col: _won(row.get(k)) for k, col in _BAL_MAP.items()})
        eps = row.get("eps_basic")
        if row["derived"]:
            notes.append("직전 분기 누적 없음 — 분기 단독을 만들지 않았다(derived)")
        if vals["ni_owner"] is None and vals["ni"] is not None:
            vals["ni_owner"] = vals["ni"]
            if fs == "CFS":
                notes.append("지배순이익 칸 없음 — 당기순이익(비지배 포함)으로 대체")
                quality = Quality.ESTIMATED
        if fs == "OFS":
            # 별도재무제표에는 비지배지분이 없다(정의상 0) — 지배자본 = 자본총계
            if vals["total_equity"] is not None:
                if vals["equity_owner"] is None:
                    vals["equity_owner"] = vals["total_equity"]
                if vals["minority"] is None:
                    vals["minority"] = 0
        elif vals["equity_owner"] is None and vals["total_equity"] is not None:
            vals["equity_owner"] = vals["total_equity"]
            notes.append("지배기업 자본 칸 없음 — 자본총계(비지배 포함)로 대체")
            quality = Quality.ESTIMATED
        if tier == "main":
            # 주요계정(fnlttMultiAcnt)에는 차입금·단기금융상품 줄이 없다 — `period_of` 의
            # '줄이 없으면 0' 규칙은 전체 재무제표에만 맞는다. 없는 값은 None(0 으로 채우지 않는다)
            vals["total_debt"] = vals["short_inv"] = None
        if financial and vals["revenue"] is None and row.get("fin_revenue") is not None:
            vals["revenue"] = _won(row.get("fin_revenue"))
            notes.append("금융업 — 매출 자리에 보험수익·순영업수익을 썼다 [확인 필요 #35]")
        if financial:
            vals["cogs"] = vals["gross_profit"] = None
            notes.append("금융업 — 매출총이익·EBITDA 해당 없음(financial_sector)")
        if not financial and vals["op"] is not None and vals["d_and_a"] is not None:
            vals["ebitda"] = vals["op"] + vals["d_and_a"]
        else:
            vals["ebitda"] = None
        if vals["total_debt"] is not None and vals["cash"] is not None:
            vals["net_debt"] = vals["total_debt"] - vals["cash"] - (vals["short_inv"] or 0)
        else:
            vals["net_debt"] = None
        diff_pct: float | None = None
        for col, rep3 in rep.reported_3m.items():
            mine = vals.get(col)
            if mine is None or rep3 == 0:
                continue
            pct = abs(mine - rep3) / abs(rep3) * 100
            diff_pct = pct if diff_pct is None else max(diff_pct, pct)
        if diff_pct is not None and diff_pct > DIFF3M_TOL_PCT:
            notes.append(
                f"정정 반영 차이 — 보고서 3개월값과 누적 차분이 {diff_pct:.2f}% 다르다"
                "(차분값을 쓴다)"
            )
            quality = Quality.ESTIMATED
        out.append(
            QuarterRow(
                corp_code=corp_code,
                fiscal_year=year,
                fiscal_q=q,
                fs_div=_fs(fs),
                period_start=per.start,
                period_end=per.end,
                rcept_dt=rcept_dt,
                tier=tier,
                eps_basic=None if eps is None else round(float(eps) * EOK, 6),
                derived=bool(row["derived"]),
                reported_3m=dict(rep.reported_3m),
                diff3m_max_pct=None if diff_pct is None else round(diff_pct, 6),
                currency=rep.currency,
                notes=tuple(notes),
                quality=quality,
                **vals,
            )
        )
    return out
