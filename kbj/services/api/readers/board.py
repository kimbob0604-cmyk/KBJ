"""신고가 보드 readers — `prv_board.artifact` 의 산출 JSON(docs/p3_design.md §4.1·§5.3).

- 보드는 `board.daily`(16:20, KIS 마감 — estimated)·`board.confirm`(다음 영업일 08:40,
  KRX 확정 — ok)
  이 계산해 저장한다. 여기서는 읽고 거르기만 한다(계산하지 않는다).
- 신고가 행에는 그날 원장 외국인·기관 순매수(원)를 붙인다(§5.2 — 기간 1일).
- 역사적 신고가 범위(R8·D-P3-11): KRX 백필이 닿는 범위에서만 계산한다 — 범위 시작일
  (`hist_scope.history_from`)을 `history_from` 과 notes 에 싣는다. 네이버 출처 옛 최고가는
  옮기지 않았다.
- 보드 산출의 금액은 ET 그대로 **억원**이다(notes 에 적는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final, Literal

from kbj.core.rows import BoardArtifact, Investor, InvestorDay, pick_best, row_rank
from kbj.engines.flows.ledger import investor_values
from kbj.services.api.models.board import (
    BoardEvents,
    BoardFilter,
    BoardNewhigh,
    BoardRankings,
    BoardSectors,
    RowFlows,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst

__all__ = ["board_events", "board_newhigh", "board_rankings", "board_sectors"]

EOK_NOTE: Final = "보드 금액(turnover·mktcap·*_eok)은 억원(ET 산출 그대로)"
Basis = Literal["close", "high"]
Kind = Literal["hist", "w52", "d60"]


def _artifact(ctx: ReadContext, name: str, day: date | None) -> BoardArtifact:
    d = day if day is not None else ctx.repos.board.last_day(ctx.today())
    if d is None:
        raise NoData("아직 없음 — board.daily 16:20")
    art = ctx.repos.board.artifact(d, name)
    if art is None:
        raise NoData(f"아직 없음 — {d} 보드 산출 없음(board.daily 16:20)")
    return art


def _notes(art: BoardArtifact, extra: list[str]) -> list[str]:
    q = "KRX 확정" if art.quality.value == "ok" else "KIS 마감값(잠정) — 다음 영업일 08:40 KRX 확정"
    return [q, EOK_NOTE, *extra]


def _wrap[T](ctx: ReadContext, art: BoardArtifact, data: T, notes: list[str]) -> Envelope[T]:
    return envelope(
        data,
        source=label([art.source]),
        as_of=art.as_of,
        quality=art.quality,
        notes=_notes(art, notes),
        generated_at=ctx.now(),
    )


def _row_label(row: Mapping[str, Any], basis: Basis) -> str | None:
    b = row.get(f"{basis}_basis")
    if isinstance(b, Mapping):
        v = b.get("label")  # pyright: ignore[reportUnknownMemberType]
        return v if isinstance(v, str) else None
    v = row.get("label")
    return v if isinstance(v, str) else None


def _flows_for(ctx: ReadContext, day: date, codes: list[str]) -> dict[str, RowFlows]:
    if not codes:
        return {}
    rows: list[InvestorDay] = [
        r for rs in ctx.repos.flows.days(codes, day, day).values() for r in rs
    ]
    best = pick_best(
        rows, lambda r: (r.code, r.investor), lambda r: row_rank(r.source, r.quality, r.venue)
    )
    by: dict[str, dict[Investor, InvestorDay]] = {}
    for (code, who), r in best.items():
        by.setdefault(code, {})[who] = r
    out: dict[str, RowFlows] = {}
    for code, inv in sorted(by.items()):
        v = investor_values(inv)
        q = worst(v.qualities)
        out[code] = RowFlows(foreign=v.foreign, inst=v.inst, quality=None if q is None else q.value)
    return out


def board_newhigh(
    ctx: ReadContext,
    *,
    day: date | None,
    basis: Basis,
    kind: Kind | None,
    min_turnover_eok: float | None,
) -> Envelope[BoardNewhigh]:
    art = _artifact(ctx, "newhigh", day)
    p: dict[str, Any] = dict(art.payload)
    achieved = [r for r in p.get("achieved", []) if isinstance(r, Mapping)]
    n_before = len(achieved)
    rows: list[Mapping[str, Any]] = []
    for r in achieved:
        lab = _row_label(r, basis)  # pyright: ignore[reportUnknownArgumentType]
        if lab is None or (kind is not None and lab != kind):
            continue
        tv = r.get("turnover")  # pyright: ignore[reportUnknownMemberType]
        if min_turnover_eok is not None and (
            not isinstance(tv, int | float) or tv < min_turnover_eok
        ):
            continue
        rows.append(r)  # pyright: ignore[reportUnknownArgumentType]
    scope = p.get("hist_scope") if isinstance(p.get("hist_scope"), Mapping) else None
    hf_raw = scope.get("history_from") if scope else None  # pyright: ignore[reportUnknownMemberType]
    history_from = date.fromisoformat(hf_raw) if isinstance(hf_raw, str) else None
    codes = [str(r.get("code")) for r in rows if r.get("code")]
    p.update(
        achieved=rows,
        history_from=history_from,
        flows=_flows_for(ctx, art.trade_date, codes),
        filter=BoardFilter(
            basis=basis, kind=kind, min_turnover_eok=min_turnover_eok, n_before_filter=n_before
        ),
    )
    notes: list[str] = []
    if history_from is not None:
        notes.append(
            f"역사적 신고가는 {history_from.isoformat()} 이후 일봉(KRX 백필 범위) 기준 — "
            "그 전에 상장한 "
            "종목은 역사적 신고가를 계산하지 않는다"
        )
    else:
        notes.append("역사적 신고가 계산 범위(백필 시작일)를 모른다")
    return _wrap(ctx, art, BoardNewhigh.model_validate(p), notes)


def board_sectors(ctx: ReadContext, *, day: date | None) -> Envelope[BoardSectors]:
    art = _artifact(ctx, "sectors", day)
    return _wrap(ctx, art, BoardSectors.model_validate(dict(art.payload)), [])


def board_events(ctx: ReadContext, *, day: date | None) -> Envelope[BoardEvents]:
    art = _artifact(ctx, "events", day)
    return _wrap(ctx, art, BoardEvents.model_validate(dict(art.payload)), [])


def board_rankings(ctx: ReadContext, *, day: date | None) -> Envelope[BoardRankings]:
    art = _artifact(ctx, "rankings", day)
    return _wrap(ctx, art, BoardRankings.model_validate(dict(art.payload)), [])
