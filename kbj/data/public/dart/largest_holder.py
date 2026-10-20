"""DART 정기보고서 주요정보 — 최대주주 현황(`hyslrSttus.json`, 공개 등급).

metrics §24.2(지수 유동비율 추정의 전략적 보유). 필드는 실측(2026-10-21 — `docs/probe_results.md`):
`nm`(성명)·`relate`(관계)·`stock_knd`(주식 종류)·`trmend_posesn_stock_co`(기말 소유 주식 수)·
`stlm_dt`(결산기준일)·`rcept_no`. 사람마다·종류마다 한 행, 종류마다 `nm='계'` 합계 행이 붙는다.

- 종류는 주식총수와 같은 규칙(`shares.class_of` — '보통주'·'의결권 있는 주식' → common 등).
- 종류별 보유 = '계' 행 값, 없으면 사람 행의 합. 그 종류 행이 없거나 '-' 뿐이면 None.
- 응답에 접수일이 없어 `rcept_dt` 는 호출자가 넘긴다(없으면 접수번호 앞 8자리 — PIT).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final, cast

from kbj.core.fin_rows import REPRT, LargestHolder, is_rcept_no
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import ParseError, parse_date, parse_int
from kbj.data.public.dart.shares import class_of

__all__ = ["TOTAL_NAMES", "fetch_largest_holder", "parse_largest_holder"]

TOTAL_NAMES: Final = frozenset({"계", "합계", "소계"})


def _name(v: object) -> str:
    return " ".join(str(v or "").split())


def _rcept_day(rcept_no: str) -> date:
    return date(int(rcept_no[:4]), int(rcept_no[4:6]), int(rcept_no[6:8]))


def parse_largest_holder(
    js: Mapping[str, Any], corp_code: str, *, rcept_dt: date | None = None
) -> LargestHolder | None:
    """hyslrSttus 본문 → 최대주주 현황(013·빈 목록이면 None).

    접수번호·결산일이 이상하면 ParseError."""
    raw: object = js.get("list") or []
    if str(js.get("status", "")) == "013" or not isinstance(raw, list) or not raw:
        return None
    rows = [cast(Mapping[str, Any], r) for r in cast(list[object], raw) if isinstance(r, Mapping)]
    if not rows:
        return None
    rc = str(rows[0].get("rcept_no") or "").strip()
    if not is_rcept_no(rc):
        raise ParseError(f"{corp_code}: 최대주주 현황 응답의 접수번호가 이상하다")
    end = parse_date(rows[0].get("stlm_dt"))
    if end is None:
        raise ParseError(f"{corp_code}: 최대주주 현황 응답에 결산기준일이 없다")
    totals: dict[str, int] = {}
    sums: dict[str, int] = {}
    names: list[str] = []
    for r in rows:
        cls = class_of(r.get("stock_knd"))
        nm = _name(r.get("nm"))
        n = parse_int(r.get("trmend_posesn_stock_co"))
        if nm.replace(" ", "") in TOTAL_NAMES:
            if cls is not None and n is not None:
                totals[cls] = n
            continue
        if nm and nm not in names:
            names.append(nm)
        if cls is not None and n is not None:
            sums[cls] = sums.get(cls, 0) + n

    def held(cls: str) -> int | None:
        return totals.get(cls, sums.get(cls))

    return LargestHolder(
        corp_code=corp_code,
        period_end=end,
        rcept_no=rc,
        common_held=held("common"),
        pref_held=held("pref"),
        holders=tuple(names),
        rcept_dt=rcept_dt or _rcept_day(rc),
    )


def fetch_largest_holder(
    client: DartJson, corp_code: str, year: int, reprt: str, *, rcept_dt: date | None = None
) -> LargestHolder | None:
    """회사·사업연도·보고서의 최대주주 현황. 아직 없으면(013) None. DART 오류는 그대로 올린다."""
    if reprt not in REPRT:
        raise ValueError(f"보고서 코드는 {', '.join(REPRT)} 중 하나: {reprt!r}")
    js = client.get_json(
        "hyslrSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt},
        use_cache=False,
    )
    return parse_largest_holder(js, corp_code, rcept_dt=rcept_dt)
