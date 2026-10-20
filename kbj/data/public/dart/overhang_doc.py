"""오버행 공시 원문(`document.xml`) 해석 — 결정 공시 폴백·전환청구·신주인수권 행사·전환가액 조정.

승격 원본: SD `overhang_parser.py`: `_parse_amount`:176, `_parse_date`:186,
`parse_cb_bw_content`:198, `parse_paid_in_content`:242(정규식 — 결정 공시만 읽었다).
docs/p4_design.md §1.3·§4.3, ADR 0013 §2.2.

- 결정 공시(CB·BW·EB·유상)는 구조화 응답이 정답이고 이 모듈은 **빈 칸 폴백**이다
  (`parse_cb_bw`·`parse_paid_in` → 칸 이름이 `OverhangEvent` 와 같은 dict — `structured.supplement`
  가 빈 칸만 채운다). SD 정규식을 그대로 옮기고(태그를 지운 본문에서) 회차·리픽싱 하한·배정
  방식·신주 상장 예정일을 더 읽는다. SD 는 유상증자 상장 예정일을 `exercise_start_date` 에 넣었다 —
  여기는 `listing_date`.
- 신규(SD 에 없음): 전환청구권·신주인수권·교환청구권 **행사**(`parse_exercise` — 청구 금액·발행 주식
  수·청구일)와 **가액 조정**(`parse_price_adjust` — 조정 후 가격·적용일). 구조화 API 가 없다 [확인
  필요 #31] — 표 모양은 합성 세계(`tests/fixtures/synthetic/p4_world.py` `_exercise_doc`·
  '전환가액의조정' 표)와 같은 추정이다. 실측으로 바뀌면 이 파일의 머리 이름 상수만 고친다.
- 대상 결정 공시 잇기(`link_target`): 같은 회사·같은 상품·같은 회차의 결정 공시 중 **가장 늦은
  접수번호**(정정 공시가 원 공시를 대신한다). 못 이으면 처리기가 `quality=invalid` + 사유로
  남긴다(§4.3).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.fin_rows import Instrument, OverhangAdjust, OverhangEvent
from kbj.core.quality import Quality
from kbj.data.public.dart.doctext import (
    ParseError,
    cell_after,
    html_grid,
    html_tables,
    norm_label,
    parse_date,
    parse_float,
    parse_int,
    strip_html,
)

__all__ = [
    "AdjustParse",
    "adjust_instrument",
    "link_target",
    "parse_cb_bw",
    "parse_exercise",
    "parse_paid_in",
    "parse_price_adjust",
    "to_adjust",
]

_DATE = r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}\s*일?)"
_NUM = r"([\d,]+)"
# SD parse_cb_bw_content:198 — 전환가/행사가(+교환가 추가)
_PRICE = (
    re.compile(rf"전환가\s*액?\s*[:：]?\s*{_NUM}\s*원"),
    re.compile(rf"행사가\s*액?\s*[:：]?\s*{_NUM}\s*원"),
    re.compile(rf"교환가\s*액?\s*[:：]?\s*{_NUM}\s*원"),
)
_SHARES = (
    re.compile(
        rf"(?:전환|행사|교환)에?\s*따라\s*(?:발행|교환)할\s*주식\s*[의총수]*\s*[:：]?\s*{_NUM}\s*주"
    ),
    re.compile(rf"교환\s*대상\s*주식\s*수\s*[:：]?\s*{_NUM}\s*주"),
    re.compile(rf"발행\s*주식\s*수\s*[:：]?\s*{_NUM}\s*주"),
)
_FACE = (
    re.compile(rf"사채의?\s*액면\s*총액\s*[:：]?\s*{_NUM}\s*원"),
    re.compile(rf"(?:발행)?총액\s*[:：]?\s*{_NUM}\s*원"),
)
_PERIOD = re.compile(rf"(?:전환|행사|교환)\s*청구\s*기간\s*[:：]?\s*{_DATE}\s*[~∼]\s*{_DATE}")
_PERIOD_DASH = re.compile(
    r"(?:전환|행사|교환)\s*청구\s*기간\s*[:：]?\s*(\d{4}[./]\d{1,2}[./]\d{1,2})\s*-\s*"
    r"(\d{4}[./]\d{1,2}[./]\d{1,2})"
)
_MATURITY = re.compile(rf"사채\s*만기일\s*[:：]?\s*{_DATE}")
_FLOOR = re.compile(rf"최저\s*조정\s*가액\s*[:：]?\s*{_NUM}\s*원")
_SEQ = (re.compile(r"회차\s*[:：]?\s*(\d+)"), re.compile(r"제\s*(\d+)\s*회"))
_BOND_METHOD = re.compile(r"사채\s*발행\s*방법\s*[:：]?\s*([^\s,]+)")
# SD parse_paid_in_content:242
_NEW_SHARES = re.compile(rf"신주\s*의?\s*수\s*[:：]?\s*{_NUM}\s*주")
_ISSUE_PRICE = (
    re.compile(rf"1주당\s*발행가\s*액?\s*[:：]?\s*{_NUM}\s*원"),
    re.compile(rf"발행가\s*액?\s*[:：]?\s*{_NUM}\s*원"),
)
_LISTING = re.compile(rf"신주\s*상장\s*예정일\s*[:：]?\s*{_DATE}")
_ISSUE_METHOD = re.compile(r"증자\s*방식\s*[:：]?\s*([^\s,]+)")

# 조정·행사 표의 머리 이름 [확인 필요 #31]
_EX_DATE_HEADS: Final = ("청구일자", "행사일자", "청구일", "행사일")
_EX_AMOUNT_HEADS: Final = ("청구금액", "행사금액", "청구금액(원)", "행사금액(원)")
_EX_SHARES_HEADS: Final = ("발행주식수", "발행주식수(주)", "발행한주식수", "교부주식수")
_EX_PRICE_HEADS: Final = ("가액", "가액(원)", "전환가액", "행사가액", "교환가액")
_EX_LISTING_HEADS: Final = ("상장(예정)일", "상장일", "상장예정일")
_ADJ_EFFECTIVE: Final = ("조정가액적용일", "조정가액 적용일", "적용일", "조정일")
# 회차 칸('3'·'03'·'제3회'·'3회차') — 합계·소계 행은 숫자가 아니라 내역 끝으로 본다
_SEQ_CELL = re.compile(r"제?(\d+)(?:회차|회)?")


def _first(pats: Iterable[re.Pattern[str]], text: str) -> str | None:
    for p in pats:
        m = p.search(text)
        if m:
            return m.group(1)
    return None


def _positive(v: float | None) -> float | None:
    return v if v is not None and v > 0 else None


def parse_cb_bw(html: str) -> dict[str, object]:
    """CB·BW·EB 결정 원문 → {OverhangEvent 칸 이름: 값}(읽은 칸만)."""
    text = strip_html(html)
    out: dict[str, object] = {}
    price = _positive(parse_float(_first(_PRICE, text)))
    if price is not None:
        out["price"] = price
    shares = parse_int(_first(_SHARES, text))
    if shares is not None:
        out["shares"] = shares
    face = parse_int(_first(_FACE, text))
    if face is not None:
        out["face_value"] = face
    m = _PERIOD.search(text) or _PERIOD_DASH.search(text)
    if m:
        start, end = parse_date(m.group(1)), parse_date(m.group(2))
        if start is not None and end is not None and start <= end:
            out["exercise_start"], out["exercise_end"] = start, end
    mat = _MATURITY.search(text)
    if mat:
        d = parse_date(mat.group(1))
        if d is not None:
            out["maturity"] = d
    floor = _positive(parse_float(_first((_FLOOR,), text)))
    if floor is not None:
        out["refix_floor"] = floor
    seq = _first(_SEQ, text)
    if seq is not None:
        out["seq"] = seq
    method = _first((_BOND_METHOD,), text)
    if method is not None:
        out["alloc_method"] = method
    return out


def parse_paid_in(html: str) -> dict[str, object]:
    """유상증자결정 원문 → {OverhangEvent 칸 이름: 값}(신주 수·발행가·상장 예정일·배정 방식)."""
    text = strip_html(html)
    out: dict[str, object] = {}
    m = _NEW_SHARES.search(text)
    shares = parse_int(m.group(1)) if m else None
    if shares is not None:
        out["shares"] = shares
    price = _positive(parse_float(_first(_ISSUE_PRICE, text)))
    if price is not None:
        out["price"] = price
    lst = _LISTING.search(text)
    if lst:
        d = parse_date(lst.group(1))
        if d is not None:
            out["listing_date"] = d
    method = _first((_ISSUE_METHOD,), text)
    if method is not None:
        out["alloc_method"] = method
    return out


@dataclass(frozen=True)
class AdjustParse:
    """조정·행사 원문 해석 결과(대상 연결 전). 금액은 원, 가격은 원/주."""

    kind: Literal["price_adjust", "exercise"]
    seq: str | None
    new_price: float | None = None
    old_price: float | None = None
    amount: int | None = None
    shares: int | None = None
    effective_date: date | None = None
    listing_date: date | None = None
    rows: int = 1
    other_seqs: tuple[str, ...] = ()  # 같은 공시의 다른 회차(반영 못 함 — parse_exercise)
    notes: tuple[str, ...] = ()


def adjust_instrument(title: str | None) -> Instrument | None:
    """조정·행사 공시 제목 → 상품(전환 → CB, 신주인수권·행사가액 → BW, 교환 → EB)."""
    t = (title or "").replace(" ", "")
    if "교환" in t:
        return Instrument.EB
    if "신주인수권" in t or "행사가액" in t:
        return Instrument.BW
    if "전환" in t:
        return Instrument.CB
    return None


def _col(head: Sequence[str], names: Iterable[str], *, exclude: Iterable[str] = ()) -> int | None:
    wanted = [norm_label(n) for n in names]
    banned = [norm_label(n) for n in exclude]
    for i, cell in enumerate(head):
        c = norm_label(cell)
        if any(w == c for w in wanted):
            return i
    for i, cell in enumerate(head):
        c = norm_label(cell)
        if any(w in c for w in wanted) and not any(b in c for b in banned):
            return i
    return None


def _cell(row: Sequence[str], i: int | None) -> str | None:
    return row[i] if i is not None and i < len(row) else None


def _seq_no(cell: str | None) -> str | None:
    """회차 칸 → 숫자 회차('3'·'제3회'·'3회차' → '3'). 숫자가 아니면 None(합계·소계 행 등)."""
    m = _SEQ_CELL.fullmatch((cell or "").replace(" ", ""))
    return None if m is None else (m.group(1).lstrip("0") or "0")


def parse_exercise(html: str) -> AdjustParse:
    """전환청구권·신주인수권·교환청구권 행사 원문 → 청구 금액·발행 주식 수·청구일(같은 회차 행은
    합한다).

    내역 표(머리 '청구일자 | 회차 | 청구금액 | 가액 | 발행주식수 | 상장일')를 찾는다. 회차 칸이
    숫자가 아닌 행(합계·소계)에서 내역이 끝난 것으로 본다 — 합계를 한 번 더 더하지 않는다. 한
    공시에 회차가 둘 이상이면 **첫 회차의 행만** 합해 그 결정 공시에 잇는다: 조정 표
    (`overhang_adjust`)는 접수번호마다 한 행이라, 다른 회차의 청구까지 첫 회차에 더하면 첫 회차
    잔여가 실제보다 줄어든다. 다른 회차의 청구는 반영하지 못한 채(그 사채 잔여는 상한)
    `other_seqs`·notes 로 남긴다(행은 estimated — `to_adjust`). 청구일은 첫 회차 행 중 가장 늦은
    날(그날까지 다 반영된다).
    """
    for table in html_grid(html):  # 합친 칸을 펼친다('사채의 명칭' → 회차·종류 — 실측 2026-10-20)
        for hi, head in enumerate(table):
            seq_i = _col(head, ("회차",))
            amt_i = _col(head, _EX_AMOUNT_HEADS)
            if seq_i is None or amt_i is None:
                continue
            date_i = _col(head, _EX_DATE_HEADS)
            shares_i = _col(head, _EX_SHARES_HEADS, exclude=("누계",))
            price_i = _col(head, _EX_PRICE_HEADS, exclude=("금액",))
            list_i = _col(head, _EX_LISTING_HEADS)
            seqs: list[str] = []
            amount = 0
            shares = 0
            have_shares = False
            days: list[date] = []
            listings: list[date] = []
            price: float | None = None
            n = 0
            for row in table[hi + 1 :]:
                amt = parse_int(_cell(row, amt_i))
                seq = _seq_no(_cell(row, seq_i))
                if amt is None or seq is None:
                    break  # 내역 끝(합계·소계·빈 행)
                seqs.append(seq)
                if seq != seqs[0]:
                    continue  # 다른 회차 — 첫 회차에 더하지 않는다
                n += 1
                amount += amt
                sh = parse_int(_cell(row, shares_i))
                if sh is not None:
                    shares += sh
                    have_shares = True
                d = parse_date(_cell(row, date_i))
                if d is not None:
                    days.append(d)
                ld = parse_date(_cell(row, list_i))
                if ld is not None:
                    listings.append(ld)
                if price is None:
                    price = _positive(parse_float(_cell(row, price_i)))
            if n == 0:
                continue
            others = tuple(sorted(set(seqs) - {seqs[0]}, key=int))
            notes: list[str] = []
            if others:
                notes.append(
                    f"회차 {len(others) + 1}개가 한 공시에 — 첫 회차({seqs[0]}) 행만 이었다"
                    f"(회차 {'·'.join(others)} 의 청구 {len(seqs) - n}행은 반영 못 함 — 그 사채 "
                    "잔여는 상한)"
                )
            return AdjustParse(
                kind="exercise",
                seq=seqs[0],
                old_price=price,
                amount=amount,
                shares=shares if have_shares else None,
                effective_date=max(days) if days else None,
                listing_date=max(listings) if listings else None,
                rows=n,
                other_seqs=others,
                notes=tuple(notes),
            )
    raise ParseError("행사·청구 내역 표(회차·청구금액)를 찾지 못했다")


def parse_price_adjust(html: str) -> AdjustParse:
    """전환가액·행사가액·교환가액 조정 원문 → 조정 후 가격·적용일(머리 '회차 | 조정전 … | 조정후
    …')."""
    effective = parse_date(cell_after(html_tables(html), _ADJ_EFFECTIVE))
    # 왼쪽 머리 칸이 줄을 합친다(rowspan) — 펼쳐야 값 줄 열이 머리와 맞는다(실측 2026-10-20)
    for table in html_grid(html):
        for hi, head in enumerate(table):
            after_i = _col(head, ("조정후",))
            seq_i = _col(head, ("회차",))
            if after_i is None or seq_i is None:
                continue
            before_i = _col(head, ("조정전",))
            got: list[tuple[str, float, float | None]] = []
            for row in table[hi + 1 :]:
                new = _positive(parse_float(_cell(row, after_i)))
                seq = _seq_no(_cell(row, seq_i))
                if new is None or seq is None:
                    break
                got.append((seq, new, _positive(parse_float(_cell(row, before_i)))))
            if not got:
                continue
            notes: tuple[str, ...] = ()
            others = tuple(dict.fromkeys(g[0] for g in got[1:] if g[0] != got[0][0]))
            if others:
                notes = (
                    f"회차 {len(others) + 1}개가 한 공시에 — 첫 회차({got[0][0]})만 잇는다(회차 "
                    f"{'·'.join(others)} 의 조정은 반영 못 함)",
                )
            seq, new, old = got[0]
            return AdjustParse(
                kind="price_adjust",
                seq=seq,
                new_price=new,
                old_price=old,
                effective_date=effective,
                rows=len(got),
                other_seqs=others,
                notes=notes,
            )
    raise ParseError("가액 조정 표(회차·조정후)를 찾지 못했다")


def _seq_key(seq: str) -> str:
    """회차 비교 열쇠 — 숫자 회차면 앞 0 을 뗀 숫자('03'·'제3회' → '3'), 아니면 공백 지운 글자."""
    return _seq_no(seq) or seq.replace(" ", "")


def link_target(
    events: Iterable[OverhangEvent], instrument: Instrument, seq: str | None
) -> OverhangEvent | None:
    """같은 상품·같은 회차 결정 공시 중 가장 늦은 접수번호. 회차를 모르면 그 상품이 하나일 때만.

    회차 표기는 구조화 응답(`bd_tm`)·원문('제3회')이 다를 수 있어 숫자로 맞춘다."""
    same = [e for e in events if e.instrument is instrument]
    if seq is not None:
        want = _seq_key(seq)
        same = [e for e in same if e.seq is not None and _seq_key(e.seq) == want]
    elif len(same) != 1:
        return None
    if not same:
        return None
    return max(same, key=lambda e: e.rcept_no)


def to_adjust(
    p: AdjustParse,
    *,
    rcept_no: str,
    corp_code: str,
    rcept_dt: date,
    target: OverhangEvent | None,
    why_unlinked: str | None = None,
) -> OverhangAdjust:
    """해석 결과 + 연결 → `OverhangAdjust` 행. 못 이었으면 quality invalid + note(사유 — §4.3)."""
    notes = list(p.notes)
    quality = Quality.OK
    if target is None:
        quality = Quality.INVALID
        notes.insert(0, why_unlinked or f"대상 결정 공시를 찾지 못했다(회차 {p.seq or '모름'})")
    else:
        if p.kind == "exercise" and p.shares is None:
            quality = Quality.ESTIMATED
            notes.append("발행 주식 수를 읽지 못했다")
        if p.other_seqs:
            quality = Quality.ESTIMATED  # 같은 공시의 다른 회차 청구를 반영 못 했다(notes)
    return OverhangAdjust(
        rcept_no=rcept_no,
        corp_code=corp_code,
        rcept_dt=rcept_dt,
        target_rcept_no=None if target is None else target.rcept_no,
        kind=p.kind,
        new_price=p.new_price,
        amount=p.amount,
        shares=p.shares,
        effective_date=p.effective_date,
        note="; ".join(notes) if notes else None,
        quality=quality,
    )
