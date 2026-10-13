"""간밤 미국 신고가 reader — `/api/board/us`(로그인). docs/p5_design.md §1.6·§5, metrics §23.

DB 만 읽는다(계약 ⑩): `prv_board.artifact`(market='US') us_summary — 작업 `us.eod` 가 엔진
(`kbj.engines.us` — 252봉)으로 만든 보드. 리더는 계산하지 않고 배치만 한다.

- 날짜: 오늘(KST) 이하 가장 최근 미국 거래일(뉴욕 날짜)의 산출. 없으면 `NoData`(작업·예정 시각).
- `basis`: 종가(기본)·고가 — 작업이 두 해석을 미리 만들어 둔다(us_summary `alt`).
- 신고가 행: 라벨 우선순위(52주 → 신고가) 다음 거래대금 순, 최대 `NEWHIGH_MAX`(ET
  `config/us.yaml display.max_rows_achieved`).
- 보류(이력 252봉 미만)는 숨기지 않고 수를 싣는다(`pending`), 빠진 데이터는 봉투 notes 에.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from kbj.services.api.models.common import Envelope
from kbj.services.api.models.us import UsBoard, UsBoardRow, UsSectorRow
from kbj.services.api.readers._common import NoData, ReadContext, envelope

__all__ = ["NEWHIGH_MAX", "NO_DATA", "us_board"]

NO_DATA: Final = "아직 없음 — us.eod 06:10(미국 마감 16:10 ET)"
NEWHIGH_MAX: Final = 200  # ET config/us.yaml display.max_rows_achieved
_RANK: Final = {"w52": 0, "d60": 1}


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _row(r: Mapping[str, Any], labels: Mapping[str, str]) -> UsBoardRow:
    lab = r.get("label")
    return UsBoardRow(
        symbol=str(r["ticker"]), name=r.get("name"), sector=r.get("sector"),
        close=_f(r.get("close")), chg_pct=_f(r.get("chg_pct")), market_cap=_f(r.get("mktcap")),
        label=None if lab is None else labels.get(str(lab), str(lab)),
    )  # fmt: skip


def _newhigh(rows: Sequence[Mapping[str, Any]], labels: Mapping[str, str]) -> list[UsBoardRow]:
    hits = [r for r in rows if r.get("label")]
    hits.sort(
        key=lambda r: (_RANK.get(str(r["label"]), 9), -(r.get("turnover") or 0), str(r["ticker"]))
    )
    return [_row(r, labels) for r in hits[:NEWHIGH_MAX]]


def us_board(ctx: ReadContext, *, basis: str) -> Envelope[UsBoard]:
    repos = ctx.repos
    day = repos.board.us_last_day(ctx.today())
    art = repos.board.us_artifact(day, "us_summary") if day is not None else None
    if art is None:
        raise NoData(NO_DATA)
    p = art.payload
    pick: Mapping[str, Any] = p
    if basis != (p.get("board") or {}).get("basis", "close"):
        pick = (p.get("alt") or {}).get(basis) or {}
        if not pick:
            raise NoData(f"{art.trade_date} 산출에 {basis} 기준이 없다 — us.eod 재실행 필요")
    board: Mapping[str, Any] = pick["board"]
    rows: Sequence[Mapping[str, Any]] = pick.get("rows") or []
    labels: Mapping[str, str] = p.get("labels") or {}
    mega = board.get("mega") or {}
    movers = [*(mega.get("up") or []), *(mega.get("down") or [])]
    data = UsBoard(
        us_trade_date=art.trade_date,
        basis="high" if basis == "high" else "close",
        summary_lines=[str(x) for x in pick.get("summary_lines") or []],
        newhigh=_newhigh(rows, labels),
        sectors=[
            UsSectorRow(
                sector=str(s["sector"]),
                n=int(s["n"]),
                newhigh=int(s["newhigh"]),
                ret_pct=_f(s.get("median")),
            )
            for s in board.get("sectors") or []
        ],
        mega_movers=[_row(r, labels) for r in movers],
        pending=int(board.get("pending") or 0),
    )
    notes = [f"미국 거래일 {art.trade_date}(뉴욕) 마감 — 아침 브리핑 ① 과 같은 산출"]
    notes += [f"빠진 데이터: {m}" for m in board.get("missing") or []]
    return envelope(
        data, source=art.source, as_of=art.as_of, quality=art.quality, notes=notes,
        generated_at=ctx.now(),
    )  # fmt: skip
