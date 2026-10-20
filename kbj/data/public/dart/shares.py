"""DART 정기보고서 주요정보 — 주식의 총수 현황(`stockTotqySttus.json`, 공개 등급).

docs/p4_design.md §1.3·§3.3·§4.3(오버행·내부자 비율의 공개 분모)·§4.5(BPS 분모). 엔드포인트·필드는
**[확인 필요 #33]** — DART 개발가이드 기준 추정(합성 세계 `tests/fixtures/synthetic/p4_world.py`
`_share_row` 와 같은 이름). 실측으로 바뀌면 `FIELDS`·`CLASS_LABELS` 만 고친다.

- 행은 주식 종류(`se` — 보통주·우선주·합계)마다 하나. `istc_totqy` = 발행주식의 총수(자기주식 포함),
  `tesstk_co` = 자기주식수, `stlm_dt` = 결산기준일(분기말). 발행 주식 수 `-` 는 None(그 종류 없음).
  발행 주식 수가 있는 행의 자기주식 `-` 는 **0**(표의 0 표기 [확인 필요 #33] — None 이면 유통 주식
  수·BPS 분모를 모르게 된다).
- 쓰는 곳: `fin.quarterly`(묶음 R — 정기보고서 접수 뒤 `DART:stockTotqySttus @ <corp>:<접수번호>`).
  응답에 접수일이 없어 `rcept_dt` 는 호출자가 공시 행에서 넘긴다(PIT — §4.8).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final, cast

from kbj.core.fin_rows import REPRT, ShareCount, is_rcept_no
from kbj.core.quality import Quality
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import ParseError, parse_date, parse_int

__all__ = ["CLASS_LABELS", "FIELDS", "fetch_share_count", "parse_share_count"]

FIELDS: Final[Mapping[str, str]] = {
    "class": "se",
    "issued": "istc_totqy",
    "treasury": "tesstk_co",
    "period_end": "stlm_dt",
}
# 회사에 따라 '의결권 있는 주식'·'의결권 없는 주식'으로 적는다(실측 2026-10-20 — 줄바꿈 포함)
CLASS_LABELS: Final[Mapping[str, tuple[str, ...]]] = {
    "common": ("보통주", "보통주식", "의결권있는주식"),
    "pref": ("우선주", "우선주식", "종류주식", "의결권없는주식"),
}


def _norm(s: object) -> str:
    return "".join(str(s or "").split())  # 공백·줄바꿈 모두


def class_of(se: object) -> str | None:
    """행 이름 → common·pref(합계·비고 등은 None). 회사마다 표기가 제각각이다(실측 2026-10-20:
    '보통주'·'의결권 있는 주식'·'의결권이 있는주식(보통주)' 등) — 정확히 맞추지 않고
    '우선주·종류주·의결권 없는' → pref, '보통주·의결권 있는' → common 으로 읽는다."""
    t = _norm(se)
    if t in CLASS_LABELS["common"]:
        return "common"
    if t in CLASS_LABELS["pref"]:
        return "pref"
    if "우선주" in t or "종류주" in t or ("의결권" in t and "없는" in t):
        return "pref"
    if "보통주" in t or ("의결권" in t and "있는" in t):
        return "common"
    return None


def _counts(row: Mapping[str, Any] | None) -> tuple[int | None, int | None]:
    """(발행 주식 수, 자기주식 수). 발행 수가 있는 행의 자기주식 '-' 는 0."""
    if row is None:
        return None, None
    issued = parse_int(row.get(FIELDS["issued"]))
    treasury = parse_int(row.get(FIELDS["treasury"]))
    if issued is not None and treasury is None:
        treasury = 0
    return issued, treasury


def parse_share_count(
    js: Mapping[str, Any], corp_code: str, *, rcept_dt: date | None = None
) -> ShareCount | None:
    """stockTotqySttus 본문 → 주식총수(013 이면 None). 보통주 행이 없으면 `ParseError`."""
    raw: object = js.get("list") or []
    if str(js.get("status", "")) == "013" or not isinstance(raw, list) or not raw:
        return None
    rows = [cast(Mapping[str, Any], r) for r in cast(list[object], raw) if isinstance(r, Mapping)]
    by_class: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        cls = class_of(r.get(FIELDS["class"]))
        if cls is not None and cls not in by_class:
            by_class[cls] = r
    common = by_class.get("common")
    if common is None:
        raise ParseError(f"{corp_code}: 주식총수 응답에 보통주 행이 없다")
    rc = str(common.get("rcept_no") or "").strip()
    if not is_rcept_no(rc):
        raise ParseError(f"{corp_code}: 주식총수 응답의 접수번호가 이상하다")
    end = parse_date(common.get(FIELDS["period_end"]))
    if end is None:
        raise ParseError(f"{corp_code}: 주식총수 응답에 결산기준일이 없다")
    common_issued, treasury_common = _counts(common)
    pref_issued, treasury_pref = _counts(by_class.get("pref"))
    return ShareCount(
        corp_code=corp_code,
        period_end=end,
        rcept_no=rc,
        common_issued=common_issued,
        pref_issued=pref_issued,
        treasury_common=treasury_common,
        treasury_pref=treasury_pref,
        rcept_dt=rcept_dt,
        quality=Quality.OK if common_issued is not None else Quality.ESTIMATED,
    )


def fetch_share_count(
    client: DartJson, corp_code: str, year: int, reprt: str, *, rcept_dt: date | None = None
) -> ShareCount | None:
    """회사·사업연도·보고서의 주식총수. 아직 없으면(013) None. DART 오류는 그대로 올린다."""
    if reprt not in REPRT:
        raise ValueError(f"보고서 코드는 {', '.join(REPRT)} 중 하나: {reprt!r}")
    js = client.get_json(
        "stockTotqySttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt},
        use_cache=False,
    )
    return parse_share_count(js, corp_code, rcept_dt=rcept_dt)
