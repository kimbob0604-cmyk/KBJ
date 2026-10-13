"""KIS 컨센서스 파서 — 종목추정실적·종목투자의견 응답 → `ConsensusRow`·`OpinionRow`(P4 묶음 K).

근거: docs/p4_design.md D-P4-9·10·§4.7·§9.3, ADR 0015, docs/metrics.md §10. 승격 원본 없음 —
SD `consensus_collector.py`·`consensus_quarterly_collector.py`(네이버 스크래핑)는 **승격하지
않고 대체**한다(U4 — 네이버·FnGuide 스크래핑 금지). SD `revision_calculator.METRIC_COLUMN`:41
의 지표 이름(eps·revenue·op_income·net_income)은 `ConsensusRow` 칸(eps·revenue·op·ni)으로
옮겼다.

TR id·경로는 `kbj.data.private.kis.datasets.CONSENSUS_TRS` 한 곳에서 가져온다(`TR_ESTIMATE`·
`TR_OPINION` — 문자열을 다시 쓰지 않는다). 아래 출력 이름·행 순서·단위는 **전부 [확인 필요
#29]**(KIS 문서 기억 기준 — 실측 전). 실측으로 바뀌면 이 모듈 머리의 상수 표만 고친다.

종목추정실적(`parse_estimate`) 모양 [확인 필요 #29]
- `output1`(객체): 종목코드 `sht_cd`, 추정일 `estdate`(YYYYMMDD) 등. 종목코드가 다르면 실패.
- `output4`(배열): 열마다 결산년월 `dt`(`YYYYMM`·`YYYY.MM` + 선택 표지 `E`·`(E)` = 추정,
  `A` = 실적).
- `output2`(배열 — 추정손익계산서, 행 순서 `PL_ROWS`)·`output3`(배열 — 투자지표, 행 순서
  `IND_ROWS`): 각 행의 `data1`~`data5` 가 `output4` 의 열과 같은 순서.
- 금액(매출·영업이익·순이익·EBITDA)은 **억원**으로 읽어 원으로 바꾼다(`AMOUNT_UNIT_KRW`).
  EPS 는 원/주. 결산년월이 연 단위라 행마다 `extra.period = "annual"`(분기 추정은 KIS 에
  없을 가능성이 높다 — D-P4-10).
- 순이익은 `ni` 에만 넣는다 — 지배주주 기준인지 모른다 [확인 필요 #29]. `ni_owner` 는
  None(지어내지 않는다 — 선행 PER 은 EPS 경로로, 묶음 V).
- 추정·실적 표지가 응답 어디에도 없으면 결산일이 스냅 날짜 뒤인 열을 추정으로 보고 그 행은
  `quality=estimated` + `extra.is_estimate_inferred`. 표지가 일부 열에만 있으면 표지 없는 열은
  실적으로 읽되, 결산일이 스냅 날짜 뒤인 열은 실적일 수 없으므로 같은 방식으로 추정(estimated +
  표지)으로 읽는다 — KIS 가 실적만 `A` 로 표시하는 경우에도 FY1·FY2 를 잃지 않게.
- KIS 가 준 비율(PER·EV/EBITDA·ROE·부채비율·증감율 등)은 `extra` 에만 둔다(계산에 쓰지
  않는다 — 0012).

종목투자의견(`parse_opinion`) 모양 [확인 필요 #29]
- `output`(배열): 영업일자 `stck_bsop_date`, 회원사(증권사) `mbcr_name`, 의견 `invt_opnn`,
  직전 의견 `rgbf_invt_opnn`, 목표가 `hts_goal_prc`(원). 증권사 이름은 원문 그대로 저장한다.
- 같은 (증권사, 날짜) 가 두 번 오면 앞의 것 하나(응답 순서 — 결정적). 목표가 0·미제시는
  그대로 저장하고 목표가 컨센서스(엔진)가 뺀다.

빈 응답(결과 배열이 비었거나 금액이 모두 0·빈 값)은 `KisEmpty` — 0 을 사실로 내보내지 않는다
(P3 규칙, CLAUDE.md 절대 규칙 2). 수집 처리기는 그 종목을 '커버 없음'으로 세고 넘어간다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

from kbj.core.fin_rows import ConsensusRow, OpinionRow
from kbj.core.quality import Quality
from kbj.data.private.kis.datasets import CONSENSUS_TRS
from kbj.data.private.kis.parse import (
    KisEmpty,
    KisParseError,
    KisShapeError,
    TrSpec,
    kis_float,
    kis_int,
    pick,
    rows_of,
    ymd,
)

__all__ = [
    "AMOUNT_UNIT_KRW",
    "COLUMNS",
    "IND_ROWS",
    "OPINION_FIELDS",
    "OPINION_SCREEN",
    "OUT_HEAD",
    "OUT_IND",
    "OUT_OPINION",
    "OUT_PERIODS",
    "OUT_PL",
    "PL_ROWS",
    "SOURCE",
    "TR_ESTIMATE",
    "TR_OPINION",
    "params_estimate",
    "params_opinion",
    "parse_estimate",
    "parse_fiscal_ym",
    "parse_opinion",
]

SOURCE: Final = "KIS"
TR_ESTIMATE: Final = TrSpec(*CONSENSUS_TRS["consensus_estimate"])
TR_OPINION: Final = TrSpec(*CONSENSUS_TRS["invest_opinion"])

# ── 종목추정실적 모양 [확인 필요 #29] ──────────────────────────────────────────────────────
OUT_HEAD: Final = "output1"
OUT_PL: Final = "output2"  # 추정손익계산서
OUT_IND: Final = "output3"  # 투자지표
OUT_PERIODS: Final = "output4"  # 결산년월(열 머리)
PERIOD_FIELD: Final = "dt"
COLUMNS: Final[tuple[str, ...]] = ("data1", "data2", "data3", "data4", "data5")
# output2 행 순서 — 금액 칸은 ConsensusRow 칸 이름, 비율 칸은 extra 이름
PL_ROWS: Final[tuple[str, ...]] = (
    "revenue",
    "revenue_growth_pct",
    "op",
    "op_growth_pct",
    "ni",
    "ni_growth_pct",
)
# output3 행 순서
IND_ROWS: Final[tuple[str, ...]] = (
    "ebitda",
    "eps",
    "eps_growth_pct",
    "per",
    "ev_ebitda",
    "roe_pct",
    "debt_ratio_pct",
    "interest_coverage",
)
AMOUNT_UNIT_KRW: Final = 100_000_000  # 억원 → 원 [확인 필요 #29]
_AMOUNTS: Final = frozenset({"revenue", "op", "ni", "ebitda"})
_HEAD_CODE: Final = ("sht_cd", "stck_shrn_iscd")
_HEAD_DATE: Final = ("estdate",)
# 결산년월 표기: 202612 · 2026.12 · 2026/12 · 2026-12 + 선택 표지 E·(E)·A·(A)
_FISCAL = re.compile(r"(\d{4})[./-]?(\d{2})\s*(?:\(?\s*([EA])\s*\)?)?", re.IGNORECASE)

# ── 종목투자의견 모양 [확인 필요 #29] ──────────────────────────────────────────────────────
OUT_OPINION: Final = "output"
OPINION_SCREEN: Final = "16633"  # FID_COND_SCR_DIV_CODE(화면 번호) [확인 필요 #29]
OPINION_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "date": ("stck_bsop_date",),
    "broker": ("mbcr_name",),
    "opinion": ("invt_opnn",),
    "opinion_prev": ("rgbf_invt_opnn",),
    "target": ("hts_goal_prc",),
}


def params_estimate(code: str) -> dict[str, str]:
    """종목추정실적 파라미터 — 종목코드 하나 [확인 필요 #29]."""
    return {"SHT_CD": code}


def params_opinion(code: str, start: date, end: date) -> dict[str, str]:
    """종목투자의견 파라미터 — 기간 [start, end](영업일자 기준) [확인 필요 #29]."""
    if start > end:
        raise ValueError(f"기간이 거꾸로다: {start} > {end}")
    return {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": OPINION_SCREEN,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
    }


def parse_fiscal_ym(text: object) -> tuple[str, bool | None] | None:
    """결산년월 표기 → (YYYYMM, 추정 여부 — 표지 없으면 None). 못 읽으면 None."""
    s = str(text or "").strip()
    m = _FISCAL.fullmatch(s)
    if m is None:
        return None
    month = int(m.group(2))
    if not 1 <= month <= 12:
        return None
    mark = (m.group(3) or "").upper()
    flag = {"E": True, "A": False}.get(mark)
    return f"{m.group(1)}{m.group(2)}", flag


def _fiscal_end(ym: str) -> date:
    y, mo = int(ym[:4]), int(ym[4:])
    nxt = date(y + (mo == 12), mo % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _table(body: Mapping[str, Any], key: str, names: Sequence[str], what: str) -> dict[str, Any]:
    """행 순서 표(names)대로 output 배열을 {이름: 행} 으로. 행이 모자라면 `KisShapeError`."""
    rows = rows_of(body, key, what=what)
    if not rows:
        return {}
    if len(rows) < len(names):
        raise KisShapeError(f"{what}: {key} 행이 {len(rows)}개 — {len(names)}개를 기대")
    return dict(zip(names, rows, strict=False))


def _cell(row: Mapping[str, Any] | None, col: str) -> Any:
    if row is None:
        return None
    return pick(row, col)


def parse_estimate(
    body: Mapping[str, Any],
    code: str,
    *,
    snap_date: date,
    source: str = SOURCE,
) -> list[ConsensusRow]:
    """종목추정실적 응답 → 결산년월별 `ConsensusRow`(snap_date = 받은 날). 빈 응답은 `KisEmpty`."""
    what = f"{TR_ESTIMATE.tr_id} {code}"
    head_rows = rows_of(body, OUT_HEAD, what=what) if OUT_HEAD in body else []
    head = head_rows[0] if head_rows else {}
    got = str(pick(head, *_HEAD_CODE) or code).strip()
    if got != code:
        raise KisParseError(f"{what}: 응답 종목코드가 다르다({got})")
    periods = rows_of(body, OUT_PERIODS, what=what)
    pl = _table(body, OUT_PL, PL_ROWS, what)
    ind = _table(body, OUT_IND, IND_ROWS, what) if OUT_IND in body else {}
    if not periods or not pl:
        raise KisEmpty(f"{what}: 결과가 비었다(커버 없음)")
    if len(periods) > len(COLUMNS):
        raise KisShapeError(f"{what}: 결산년월 열이 {len(periods)}개 — 최대 {len(COLUMNS)}")
    cols: list[tuple[str, str, bool | None]] = []
    for col, p in zip(COLUMNS, periods, strict=False):
        parsed = parse_fiscal_ym(pick(p, PERIOD_FIELD))
        if parsed is None:
            continue  # 머리가 빈 열(KIS 가 5열을 다 채우지 않는 날) — 버린다
        cols.append((col, *parsed))
    if not cols:
        raise KisEmpty(f"{what}: 결산년월이 하나도 없다")
    yms = [ym for _c, ym, _f in cols]
    if len(set(yms)) != len(yms):
        raise KisShapeError(f"{what}: 결산년월이 겹친다")
    explicit = any(flag is not None for _c, _ym, flag in cols)
    est_day = ymd(pick(head, *_HEAD_DATE))
    out: list[ConsensusRow] = []
    for col, ym, flag in cols:
        vals: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for name in PL_ROWS:
            vals[name] = _cell(pl.get(name), col)
        for name in IND_ROWS:
            vals[name] = _cell(ind.get(name), col)
        money = {k: kis_int(vals[k], AMOUNT_UNIT_KRW) for k in _AMOUNTS}
        eps = kis_float(vals["eps"])
        for k, v in vals.items():
            if k in _AMOUNTS or k == "eps":
                continue
            f = kis_float(v)
            if f is not None:
                extra[k] = f
        if all(v in (None, 0) for v in money.values()) and eps in (None, 0.0):
            continue  # 값 없는 열 — 0 을 사실로 쓰지 않는다
        if flag is not None or (explicit and _fiscal_end(ym) <= snap_date):
            is_est = bool(flag)
            quality = Quality.OK
        else:  # 표지 없음 — 결산일로 추정(미래 결산은 실적일 수 없다)
            is_est = _fiscal_end(ym) > snap_date
            quality = Quality.ESTIMATED
            extra["is_estimate_inferred"] = True
        extra["period"] = "annual"
        if est_day is not None:
            extra["estdate"] = est_day.isoformat()
        out.append(
            ConsensusRow(
                code=code,
                snap_date=snap_date,
                fiscal_ym=ym,
                is_estimate=is_est,
                revenue=money["revenue"],
                op=money["op"],
                ni=money["ni"],
                ni_owner=None,  # 순이익 기준 미확인 [확인 필요 #29] — 지어내지 않는다
                eps=eps,
                ebitda=money["ebitda"],
                extra=extra,
                source=source,
                quality=quality,
            )
        )
    if not out:
        raise KisEmpty(f"{what}: 값이 모두 비었거나 0 이다")
    return sorted(out, key=lambda r: r.fiscal_ym)


def parse_opinion(body: Mapping[str, Any], code: str, *, source: str = SOURCE) -> list[OpinionRow]:
    """종목투자의견 응답 → 증권사별 `OpinionRow`((날짜, 증권사) 오름차순). 빈 응답은 `KisEmpty`."""
    what = f"{TR_OPINION.tr_id} {code}"
    rows = rows_of(body, OUT_OPINION, what=what)
    if not rows:
        raise KisEmpty(f"{what}: 결과가 비었다(기간 안 의견 없음·커버 없음)")
    f = OPINION_FIELDS
    seen: set[tuple[str, date]] = set()
    out: list[OpinionRow] = []
    bad = 0
    for r in rows:
        day = ymd(pick(r, *f["date"]))
        broker = str(pick(r, *f["broker"]) or "").strip()
        if day is None or not broker:
            bad += 1
            continue
        if (broker, day) in seen:
            continue  # 같은 증권사·같은 날 두 번 — 앞의 것 하나(응답 순서)
        seen.add((broker, day))
        opinion = str(pick(r, *f["opinion"]) or "").strip() or None
        prev = str(pick(r, *f["opinion_prev"]) or "").strip() or None
        out.append(
            OpinionRow(
                code=code,
                broker=broker,
                opinion_date=day,
                opinion=opinion,
                opinion_prev=prev,
                target_price=kis_float(pick(r, *f["target"])),
                source=source,
            )
        )
    if not out:
        raise KisShapeError(f"{what}: 행 {bad}개 모두 날짜·증권사가 없다")
    return sorted(out, key=lambda o: (o.opinion_date, o.broker))
