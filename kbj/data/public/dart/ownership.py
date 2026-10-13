"""DART 지분공시 — 임원·주요주주 특정증권등 소유상황보고(`elestock`)·주식등의 대량보유상황보고
(`majorstock`)(공개 등급).

레포에 KR 내부자 구현이 없었다(SD 는 미국 yfinance 비중뿐 — `server.py`:8745). docs/p4_design.md
§1.3·§3.3·§4.4, ADR 0013.

- 두 엔드포인트는 `corp_code` 하나로 묻고 **회사의 보고 전체**를 준다 [확인 필요 #32] — 처리기가
  이미 저장한 접수번호를 빼고 새 것만 남긴다(여기는 해석만).
- 필드 이름은 `ELESTOCK_FIELDS`·`MAJORSTOCK_FIELDS` 한 곳 [확인 필요 #32 — DART 개발가이드 기준
  추정, 합성 세계 `tests/fixtures/synthetic/p4_world.py` `_build_ownership` 과 같다]. 날짜
  `rcept_dt` 는 `YYYYMMDD`(없으면 접수번호 앞 8자리).
- 등기 여부 `-` 는 None(임원이 아닌 주요주주 법인 등), 주요주주 여부 `-` 는 False — 합성 세계와 같은
  읽기 [확인 필요 #32].
- 변동 **사유**는 구조화 응답에 없다(추정) → 원문 표의 '변동사유'(`parse_reason`)를 처리기가 그
  공시에 한해 붙인다. 못 읽으면 None(화면 '사유 미구분' — §4.4).
- 보고자 이름은 행에 저장하되 로그·알림·오류 문구에는 싣지 않는다 [제안] — 이 모듈의 오류 문구는
  접수번호만 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final, cast

from kbj.core.fin_rows import InsiderReport, MajorHolderReport, is_rcept_no
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import (
    ParseError,
    cell_after,
    html_tables,
    parse_date,
    parse_float,
    parse_int,
)

__all__ = [
    "ELESTOCK_FIELDS",
    "MAJORSTOCK_FIELDS",
    "Ownership",
    "fetch_insider",
    "fetch_major",
    "parse_insider_rows",
    "parse_major_rows",
    "parse_reason",
]

ELESTOCK_FIELDS: Final[Mapping[str, str]] = {
    "rcept_dt": "rcept_dt",
    "reporter": "repror",
    "registered": "isu_exctv_rgist_at",
    "position": "isu_exctv_ofcps",
    "major": "isu_main_shrholdr",
    "shares_after": "sp_stock_lmp_cnt",
    "shares_delta": "sp_stock_lmp_irds_cnt",
    "ratio_after": "sp_stock_lmp_rate",
    "ratio_delta": "sp_stock_lmp_irds_rate",
}
MAJORSTOCK_FIELDS: Final[Mapping[str, str]] = {
    "rcept_dt": "rcept_dt",
    "reporter": "repror",
    "report_type": "report_tp",
    "shares": "stkqy",
    "shares_delta": "stkqy_irds",
    "ratio": "stkrt",
    "ratio_delta": "stkrt_irds",
    "reason": "report_resn",
}
_REASON_HEADS: Final = ("변동사유", "변동 사유", "보고사유", "취득/처분사유", "변동사유(취득/처분)")
_REGISTERED: Final[Mapping[str, bool]] = {"등기임원": True, "비등기임원": False}


@dataclass(frozen=True)
class Ownership[R]:
    """해석한 행과 읽지 못한 접수번호(오류 문구에는 보고자 이름을 싣지 않는다)."""

    rows: list[R] = field(default_factory=list[R])
    bad: list[str] = field(default_factory=list[str])


def _get(row: Mapping[str, Any], fields: Mapping[str, str], name: str) -> object:
    return row.get(fields[name])


def _text(v: object) -> str | None:
    s = " ".join(str(v if v is not None else "").split())
    return None if s in ("", "-") else s


def _ident(row: Mapping[str, Any], fields: Mapping[str, str]) -> tuple[str, str, date]:
    rc = str(row.get("rcept_no") or "").strip()
    if not is_rcept_no(rc):
        raise ParseError(f"지분공시 행의 접수번호가 이상하다: {rc[:20]!r}")
    corp = str(row.get("corp_code") or "").strip()
    day = parse_date(_get(row, fields, "rcept_dt"))
    if day is None:
        day = date(int(rc[:4]), int(rc[4:6]), int(rc[6:8]))
    return rc, corp, day


def _reporter(row: Mapping[str, Any], fields: Mapping[str, str], rc: str) -> str:
    who = _text(_get(row, fields, "reporter"))
    if who is None:
        raise ParseError(f"{rc}: 보고자 칸이 비었다")
    return who


def _rows(js: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw: object = js.get("list") or []
    if not isinstance(raw, list):
        return []
    return [cast(Mapping[str, Any], r) for r in cast(list[object], raw) if isinstance(r, Mapping)]


def parse_insider_rows(rows: Sequence[Mapping[str, Any]]) -> Ownership[InsiderReport]:
    """elestock 응답 행들 → 소유보고. 한 행을 못 읽으면 그 접수번호만 `bad`."""
    out: Ownership[InsiderReport] = Ownership()
    f = ELESTOCK_FIELDS
    for row in rows:
        try:
            rc, corp, day = _ident(row, f)
            reg_raw = (_text(_get(row, f, "registered")) or "").replace(" ", "")
            major_raw = (_text(_get(row, f, "major")) or "").replace(" ", "")
            out.rows.append(
                InsiderReport(
                    rcept_no=rc,
                    corp_code=corp,
                    rcept_dt=day,
                    reporter=_reporter(row, f, rc),
                    position=_text(_get(row, f, "position")),
                    registered=_REGISTERED.get(reg_raw),
                    major_holder=major_raw == "주요주주",
                    shares_after=parse_int(_get(row, f, "shares_after")),
                    shares_delta=parse_int(_get(row, f, "shares_delta")),
                    ratio_after=parse_float(_get(row, f, "ratio_after")),
                    ratio_delta=parse_float(_get(row, f, "ratio_delta")),
                )
            )
        except (ValueError, TypeError):
            out.bad.append(str(row.get("rcept_no") or "?")[:14])
    return out


def parse_major_rows(rows: Sequence[Mapping[str, Any]]) -> Ownership[MajorHolderReport]:
    """majorstock 응답 행들 → 대량보유 보고. 한 행을 못 읽으면 그 접수번호만 `bad`."""
    out: Ownership[MajorHolderReport] = Ownership()
    f = MAJORSTOCK_FIELDS
    for row in rows:
        try:
            rc, corp, day = _ident(row, f)
            out.rows.append(
                MajorHolderReport(
                    rcept_no=rc,
                    corp_code=corp,
                    rcept_dt=day,
                    reporter=_reporter(row, f, rc),
                    report_type=_text(_get(row, f, "report_type")),
                    shares=parse_int(_get(row, f, "shares")),
                    shares_delta=parse_int(_get(row, f, "shares_delta")),
                    ratio=parse_float(_get(row, f, "ratio")),
                    ratio_delta=parse_float(_get(row, f, "ratio_delta")),
                    reason=_text(_get(row, f, "reason")),
                )
            )
        except (ValueError, TypeError):
            out.bad.append(str(row.get("rcept_no") or "?")[:14])
    return out


def fetch_insider(client: DartJson, corp_code: str) -> Ownership[InsiderReport]:
    """회사의 임원·주요주주 소유보고 전체(`elestock.json`). 013 은 빈 결과."""
    js = client.get_json("elestock.json", {"corp_code": corp_code}, use_cache=False)
    return parse_insider_rows(_rows(js))


def fetch_major(client: DartJson, corp_code: str) -> Ownership[MajorHolderReport]:
    """회사의 5% 대량보유 보고 전체(`majorstock.json`). 013 은 빈 결과."""
    js = client.get_json("majorstock.json", {"corp_code": corp_code}, use_cache=False)
    return parse_major_rows(_rows(js))


def parse_reason(html: str) -> str | None:
    """소유상황보고서 원문 → 변동 사유(장내매수·장내매도·증여·신규선임 …). 못 읽으면 None."""
    return cell_after(html_tables(html), _REASON_HEADS)
