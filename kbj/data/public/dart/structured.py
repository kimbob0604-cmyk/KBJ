"""DART 주요사항보고서 구조화 응답 — CB·BW·EB·유상·무상증자·자기주식 결정(공개 등급).

승격 원본: SD `overhang_parser.OVERHANG_REPORT_PATTERNS`:46(제목 분류만 — SD 는 원문 정규식으로만
읽었다). docs/p4_design.md D-P4-3·§1.3·§3.3·§4.3, ADR 0013 §2.2(구조화 우선, 원문 폴백).

- 엔드포인트(DS005)
  `cvbdIsDecsn`·`bdwtIsDecsn`·`exbdIsDecsn`·`piicDecsn`·`fricDecsn`·`tsstkAqDecsn`· `tsstkDpDecsn`
  은 `corp_code`·`bgn_de`·`end_de`(접수일 범위)로 묻고 응답에서 **같은 접수번호 행**을 고른다.
  이름·파라미터·필드는 **[확인 필요 #31]** — 필드 이름은 이 파일의 `FIELDS` 한 곳에 모았다 (실측 뒤
  여기만 고친다. 합성 세계 `tests/fixtures/synthetic/p4_world.py` 의 `_build_overhang`·
  `_build_buyback` 이 같은 이름을 쓴다).
- 표기: 금액·주식 수는 쉼표 문자열, 날짜는 `2026년 03월 15일`, 빈 칸은 `-`(`doctext` 가 푼다).
- 빈 칸은 None 으로 둔다 — 처리기가 필수 칸(`missing()`)이 비면 원문(`overhang_doc`)으로 채운다
  (그때 `notes` 에 '원문 보충' 을 남긴다). 둘 다 비면 그 칸은 None + quality estimated.
- 이 모듈은 DART 를 클라이언트(`DartJson` — `DartClient` 가 맞는다)로만 부른다(정본 하나).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from typing import Any, Final, Literal, cast

from kbj.core.fin_rows import BuybackEvent, Instrument, OverhangEvent, is_rcept_no
from kbj.core.quality import Quality
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import ParseError, parse_date, parse_float, parse_int

__all__ = [
    "BUYBACK_TARGETS",
    "ENDPOINT_OF",
    "FIELDS",
    "TITLE_TARGETS",
    "Decision",
    "Target",
    "endpoints",
    "fetch_decision",
    "fetch_decisions",
    "instrument_of_dataset",
    "missing",
    "parse_buyback",
    "parse_bw",
    "parse_cb",
    "parse_decision",
    "parse_eb",
    "parse_paid_in",
    "rcept_day",
    "supplement",
    "targets_of",
]

BuybackKind = Literal["acquire", "dispose"]
Target = Instrument | BuybackKind  # 결정 하나가 묻는 대상(엔드포인트를 고른다)
Decision = OverhangEvent | BuybackEvent

# 대상 → 엔드포인트(데이터셋 이름 = 엔드포인트 이름에서 `.json` 을 뺀 것 —
# kbj/data/public/dart/datasets.py)
ENDPOINT_OF: Final[Mapping[str, str]] = {
    Instrument.CB: "cvbdIsDecsn",
    Instrument.BW: "bdwtIsDecsn",
    Instrument.EB: "exbdIsDecsn",
    Instrument.PAID_IN: "piicDecsn",
    Instrument.BONUS: "fricDecsn",
    "acquire": "tsstkAqDecsn",
    "dispose": "tsstkDpDecsn",
}
BUYBACK_TARGETS: Final[frozenset[str]] = frozenset({"acquire", "dispose"})

# 제목(공백 지움) → 대상. 앞에서부터 처음 걸리는 것 — '유무상증자결정' 은 '무상증자결정' 을 품으니
# 먼저 본다. 자기주식 신탁 계약(체결·해지)은 구조화 데이터셋이 카탈로그에 없어 대상이 없다(종류
# buyback 만)
TITLE_TARGETS: Final[tuple[tuple[str, tuple[Target, ...]], ...]] = (
    ("유무상증자결정", (Instrument.PAID_IN, Instrument.BONUS)),
    ("신주인수권부사채권발행결정", (Instrument.BW,)),
    ("신주인수권부사채발행결정", (Instrument.BW,)),
    ("교환사채권발행결정", (Instrument.EB,)),
    ("교환사채발행결정", (Instrument.EB,)),
    ("전환사채권발행결정", (Instrument.CB,)),
    ("전환사채발행결정", (Instrument.CB,)),
    ("유상증자결정", (Instrument.PAID_IN,)),
    ("무상증자결정", (Instrument.BONUS,)),
    ("자기주식취득결정", ("acquire",)),
    ("자기주식처분결정", ("dispose",)),
)

# 응답 필드 이름 [확인 필요 #31 — DART 개발가이드 기준 추정]. 논리 칸 → 응답 필드
FIELDS: Final[Mapping[str, Mapping[str, str]]] = {
    "cvbdIsDecsn": {
        "seq": "bd_tm",
        "face": "bd_fta",
        "price": "cv_prc",
        "shares": "cvisstk_cnt",
        "start": "cvrqpd_bgd",
        "end": "cvrqpd_edd",
        "maturity": "bd_mtd",
        "floor": "act_mktprcfl_cvprc_lwtrsprc",
        "alloc": "bdis_mthn",
    },
    "bdwtIsDecsn": {
        "seq": "bd_tm",
        "face": "bd_fta",
        "price": "ex_prc",
        "shares": "nstk_isstk_cnt",
        "start": "expd_bgd",
        "end": "expd_edd",
        "maturity": "bd_mtd",
        "floor": "act_mktprcfl_cvprc_lwtrsprc",
        "alloc": "bdis_mthn",
    },
    "exbdIsDecsn": {
        "seq": "bd_tm",
        "face": "bd_fta",
        "price": "ex_prc",
        "shares": "extg_stkcnt",
        "start": "exrqpd_bgd",
        "end": "exrqpd_edd",
        "maturity": "bd_mtd",
        "floor": "act_mktprcfl_cvprc_lwtrsprc",
        "alloc": "bdis_mthn",
    },
    "piicDecsn": {
        "shares": "nstk_ostk_cnt",
        "shares_other": "nstk_estk_cnt",
        "alloc": "ic_mthn",
    },
    "fricDecsn": {
        "shares": "nstk_ostk_cnt",
        "shares_other": "nstk_estk_cnt",
        "listing": "nstk_lstprd",
    },
    "tsstkAqDecsn": {
        "shares": "aqpln_stk_ostk",
        "shares_other": "aqpln_stk_estk",
        "amount": "aqpln_prc_ostk",
        "amount_other": "aqpln_prc_estk",
        "start": "aqexpd_bgd",
        "end": "aqexpd_edd",
        "method": "aq_mth",
    },
    "tsstkDpDecsn": {
        "shares": "dppln_stk_ostk",
        "shares_other": "dppln_stk_estk",
        "amount": "dppln_prc_ostk",
        "amount_other": "dppln_prc_estk",
        "start": "dpprpd_bgd",
        "end": "dpprpd_edd",
        "method": "dp_m_mkt",
    },
}
# 결정 공시의 필수 칸(비면 원문으로 채운다 — 처리기)
_ESSENTIAL: Final[Mapping[Instrument, tuple[str, ...]]] = {
    Instrument.CB: ("face_value", "price", "shares", "exercise_start", "exercise_end"),
    Instrument.BW: ("face_value", "price", "shares", "exercise_start", "exercise_end"),
    Instrument.EB: ("face_value", "price", "shares", "exercise_start", "exercise_end"),
    Instrument.PAID_IN: ("shares", "price", "listing_date"),
    Instrument.BONUS: ("shares", "listing_date"),
}
_DATASET_INSTRUMENT: Final[Mapping[str, Instrument]] = {
    v: Instrument(k) for k, v in ENDPOINT_OF.items() if k not in BUYBACK_TARGETS
}


def targets_of(title: str | None) -> tuple[Target, ...]:
    """결정 공시 제목 → 물을 대상(엔드포인트). 결정 공시가 아니면 빈 튜플."""
    t = (title or "").replace(" ", "")
    for word, targets in TITLE_TARGETS:
        if word in t:
            return targets
    return ()


def rcept_day(rcept_no: str) -> date:
    """접수번호 앞 8자리(YYYYMMDD) = 접수일(DART 관행 [확인 필요 #28])."""
    if not is_rcept_no(rcept_no):
        raise ValueError(f"접수번호(14자리 숫자)가 아니다: {rcept_no!r}")
    return date(int(rcept_no[:4]), int(rcept_no[4:6]), int(rcept_no[6:8]))


def _f(row: Mapping[str, Any], dataset: str, name: str) -> object:
    field = FIELDS[dataset].get(name)
    return None if field is None else row.get(field)


def _text(v: object) -> str | None:
    s = " ".join(str(v if v is not None else "").split())
    return None if s in ("", "-") else s


def _sum(*vals: int | None) -> int | None:
    got = [v for v in vals if v is not None]
    return sum(got) if got else None


def _positive(v: float | None) -> float | None:
    return v if v is not None and v > 0 else None


def _ident(row: Mapping[str, Any]) -> tuple[str, str]:
    rc = str(row.get("rcept_no") or "").strip()
    corp = str(row.get("corp_code") or "").strip()
    if not is_rcept_no(rc):
        raise ParseError(f"구조화 응답 행의 접수번호가 이상하다: {rc[:20]!r}")
    return rc, corp


def _bond(
    dataset: str, row: Mapping[str, Any], instrument: Instrument, rcept_dt: date | None
) -> OverhangEvent:
    rc, corp = _ident(row)
    start = parse_date(_f(row, dataset, "start"))
    end = parse_date(_f(row, dataset, "end"))
    notes: list[str] = []
    if start is not None and end is not None and start > end:
        notes.append("청구기간 시작이 끝보다 뒤 — 기간을 비웠다")
        start = end = None
    return OverhangEvent(
        rcept_no=rc,
        corp_code=corp,
        rcept_dt=rcept_dt or rcept_day(rc),
        instrument=instrument,
        face_value=parse_int(_f(row, dataset, "face")),
        price=_positive(parse_float(_f(row, dataset, "price"))),
        shares=parse_int(_f(row, dataset, "shares")),
        refix_floor=_positive(parse_float(_f(row, dataset, "floor"))),
        exercise_start=start,
        exercise_end=end,
        maturity=parse_date(_f(row, dataset, "maturity")),
        listing_date=None,
        alloc_method=_text(_f(row, dataset, "alloc")),
        parse_source="api",
        seq=_text(_f(row, dataset, "seq")),
        notes=tuple(notes),
    )


def parse_cb(row: Mapping[str, Any], *, rcept_dt: date | None = None) -> OverhangEvent:
    """전환사채권 발행결정(cvbdIsDecsn) 한 행 → 결정 공시."""
    return _bond("cvbdIsDecsn", row, Instrument.CB, rcept_dt)


def parse_bw(row: Mapping[str, Any], *, rcept_dt: date | None = None) -> OverhangEvent:
    """신주인수권부사채권 발행결정(bdwtIsDecsn) — 분리형도 같은 식(§4.3)."""
    return _bond("bdwtIsDecsn", row, Instrument.BW, rcept_dt)


def parse_eb(row: Mapping[str, Any], *, rcept_dt: date | None = None) -> OverhangEvent:
    """교환사채권 발행결정(exbdIsDecsn) — `shares` 는 교환 대상 주식(신주 아님)."""
    return _bond("exbdIsDecsn", row, Instrument.EB, rcept_dt)


def _issue(
    dataset: str, row: Mapping[str, Any], instrument: Instrument, rcept_dt: date | None
) -> OverhangEvent:
    rc, corp = _ident(row)
    other = parse_int(_f(row, dataset, "shares_other"))
    notes = (f"기타주식 신주 {other:,}주는 보통주 신주 수에 넣지 않았다",) if other else ()
    return OverhangEvent(
        rcept_no=rc,
        corp_code=corp,
        rcept_dt=rcept_dt or rcept_day(rc),
        instrument=instrument,
        face_value=None,  # 권면총액은 사채만(fin_rows.OverhangEvent·0010)
        price=None,  # 유상증자 발행가는 구조화 응답에 없다 [확인 필요 #31] — 원문 보충
        shares=parse_int(_f(row, dataset, "shares")),
        refix_floor=None,
        exercise_start=None,
        exercise_end=None,
        maturity=None,
        listing_date=parse_date(_f(row, dataset, "listing")),
        alloc_method=_text(_f(row, dataset, "alloc")),
        parse_source="api",
        notes=notes,
    )


def parse_paid_in(row: Mapping[str, Any], *, rcept_dt: date | None = None) -> OverhangEvent:
    """유상증자결정(piicDecsn) — 신주 수·배정 방식. 발행가·상장 예정일은 원문 보충."""
    return _issue("piicDecsn", row, Instrument.PAID_IN, rcept_dt)


def parse_bonus(row: Mapping[str, Any], *, rcept_dt: date | None = None) -> OverhangEvent:
    """무상증자결정(fricDecsn) — 신주 수·신주 상장 예정일."""
    return _issue("fricDecsn", row, Instrument.BONUS, rcept_dt)


def parse_buyback(
    row: Mapping[str, Any], kind: BuybackKind, *, rcept_dt: date | None = None
) -> BuybackEvent:
    """자기주식 취득(tsstkAqDecsn)·처분(tsstkDpDecsn) 결정 — 예정 주식 수·금액·기간·방법."""
    dataset = ENDPOINT_OF[kind]
    rc, corp = _ident(row)
    return BuybackEvent(
        rcept_no=rc,
        corp_code=corp,
        rcept_dt=rcept_dt or rcept_day(rc),
        kind=kind,
        shares_planned=_sum(
            parse_int(_f(row, dataset, "shares")), parse_int(_f(row, dataset, "shares_other"))
        ),
        amount_planned=_sum(
            parse_int(_f(row, dataset, "amount")), parse_int(_f(row, dataset, "amount_other"))
        ),
        start=parse_date(_f(row, dataset, "start")),
        end=parse_date(_f(row, dataset, "end")),
        method=_text(_f(row, dataset, "method")),
    )


def parse_decision(
    target: Target, row: Mapping[str, Any], *, rcept_dt: date | None = None
) -> Decision:
    """대상에 맞는 해석기로 한 행을 읽는다."""
    if target == "acquire":
        return parse_buyback(row, "acquire", rcept_dt=rcept_dt)
    if target == "dispose":
        return parse_buyback(row, "dispose", rcept_dt=rcept_dt)
    inst = Instrument(target)
    if inst is Instrument.CB:
        return parse_cb(row, rcept_dt=rcept_dt)
    if inst is Instrument.BW:
        return parse_bw(row, rcept_dt=rcept_dt)
    if inst is Instrument.EB:
        return parse_eb(row, rcept_dt=rcept_dt)
    if inst is Instrument.PAID_IN:
        return parse_paid_in(row, rcept_dt=rcept_dt)
    return parse_bonus(row, rcept_dt=rcept_dt)


def missing(ev: OverhangEvent) -> tuple[str, ...]:
    """결정 공시의 비어 있는 필수 칸(원문으로 채울 것)."""
    return tuple(n for n in _ESSENTIAL[ev.instrument] if getattr(ev, n) is None)


def _rows(
    client: DartJson, dataset: str, corp_code: str, bgn: date, end: date
) -> list[Mapping[str, Any]]:
    js = client.get_json(
        f"{dataset}.json",
        {"corp_code": corp_code, "bgn_de": f"{bgn:%Y%m%d}", "end_de": f"{end:%Y%m%d}"},
        use_cache=False,
    )
    raw: object = js.get("list") or []
    if not isinstance(raw, list):
        return []
    items = cast(list[object], raw)
    return [cast(Mapping[str, Any], r) for r in items if isinstance(r, Mapping)]


def fetch_decision(
    client: DartJson, corp_code: str, day: date, rcept_no: str, target: Target
) -> Decision | None:
    """그날 접수된 결정 공시 하나(같은 접수번호 행). 응답에 없으면(013·늦게 반영) None.

    DART 오류(키·한도·전송)는 그대로 올린다 — 처리기가 그 건(또는 그 실행)을 실패로 기록한다.
    """
    dataset = ENDPOINT_OF[target]
    for row in _rows(client, dataset, corp_code, day, day):
        if str(row.get("rcept_no") or "").strip() == rcept_no:
            return parse_decision(target, row, rcept_dt=day)
    return None


def fetch_decisions(
    client: DartJson, corp_code: str, target: Target, bgn: date, end: date
) -> tuple[list[Decision], list[str]]:
    """기간의 결정 공시 전부(조정·청구 공시가 가리키는 옛 결정을 찾을 때 — 처리기의 연결 폴백).

    반환 (행들, 읽지 못한 접수번호들). 접수일은 접수번호 앞 8자리.
    """
    out: list[Decision] = []
    bad: list[str] = []
    for row in _rows(client, ENDPOINT_OF[target], corp_code, bgn, end):
        try:
            out.append(parse_decision(target, row))
        except (ValueError, TypeError):
            bad.append(str(row.get("rcept_no") or "?")[:14])
    return out, bad


def supplement(ev: OverhangEvent, doc: Mapping[str, object], *, source: str) -> OverhangEvent:
    """비어 있는 칸만 원문 해석값으로 채운다(구조화 값은 덮지 않는다). 채운 칸은 notes 에 남긴다."""
    fill: dict[str, object] = {}
    for name in (
        "face_value",
        "price",
        "shares",
        "refix_floor",
        "exercise_start",
        "exercise_end",
        "maturity",
        "listing_date",
        "alloc_method",
        "seq",
    ):
        if getattr(ev, name) is None and doc.get(name) is not None:
            fill[name] = doc[name]
    notes = list(ev.notes)
    if fill:
        notes.append(f"{source} 보충: {'·'.join(sorted(fill))}")
    still = [n for n in _ESSENTIAL[ev.instrument] if getattr(ev, n) is None and n not in fill]
    quality = ev.quality
    if still:
        notes.append(f"필수 칸 비어 있음: {'·'.join(still)}")
        quality = Quality.ESTIMATED
    return replace(ev, **fill, notes=tuple(notes), quality=quality)


def instrument_of_dataset(dataset: str) -> Instrument | None:
    return _DATASET_INSTRUMENT.get(dataset)


def endpoints(targets: Sequence[Target]) -> tuple[str, ...]:
    """대상들 → 데이터셋 이름들(선점 키 — `DART:<데이터셋> @ <접수번호>`)."""
    return tuple(ENDPOINT_OF[t] for t in targets)
