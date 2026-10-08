"""신고가 보드 readers — `prv_board.artifact` 의 산출 JSON(docs/p3_design.md §4.1·§5.3).

- 보드는 `board.daily`(16:00 발화 — 마감 수집이 끝나면, 평소 16:00~16:05. KIS 마감 — estimated)·
  `board.confirm`(다음 영업일 08:40, KRX 확정 — ok)이 계산해 저장한다(ADR 0018). 여기서는 읽고
  거르기만 한다(계산하지 않는다).
- 신고가 행에는 그날 원장 외국인·기관 순매수(원)를 붙인다(§5.2 — 기간 1일).
- 역사적 신고가(R8·D-P3-11·ADR 0017)는 '상장 이후 전체' 다 — 특정일 이후로 내세우지 않는다.
  이력이 상장일(또는 원천 가장 이른 봉)까지 닿지 않아 보류한 종목 수와, 원천 바닥 기준으로 판정한
  종목 수만 품질 메모(`hist_notes`)로 싣는다(화면이 기준 줄에 한 번 적는다). 네이버 출처 옛
  최고가는 옮기지 않았다.
- 보드 산출의 금액은 ET 그대로 **억원**이다(notes 에 적는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final, Literal, cast

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

__all__ = ["board_events", "board_newhigh", "board_rankings", "board_sectors", "hist_notes"]

EOK_NOTE: Final = "보드 금액(turnover·mktcap·*_eok)은 억원(ET 산출 그대로)"
# 보드가 아직 없을 때 안내(ADR 0018 — 16:00 발화, 마감 수집이 끝나야 돈다)
BOARD_DAILY_ETA: Final = "board.daily 16:00~16:05"
Basis = Literal["close", "high"]
Kind = Literal["hist", "w52", "d120"]


def hist_notes(scope: Mapping[str, Any] | None) -> list[str]:
    """역사적 신고가 품질 메모(ADR 0017) — 보류 수·원천 바닥 기준 수. 없으면 빈 목록."""
    if not scope:
        return []

    def count(key: str) -> int:
        v = scope.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    out: list[str] = []
    held = count("n_before_listing") + count("n_listing_unknown")
    if held:
        out.append(
            f"{held:,}종목은 상장일까지 일봉이 닿지 않아(또는 상장일을 몰라) 역사적 신고가를 "
            "판정하지 않았다 — KRX 백필로 이력을 채우는 중"
        )
    floor = scope.get("source_floor")
    n_floor = count("n_since_floor")
    if n_floor and isinstance(floor, str):
        out.append(f"{n_floor:,}종목은 {floor} 이후 최고가 기준(원천이 주는 가장 이른 일봉)")
    return out


def _artifact(ctx: ReadContext, name: str, day: date | None) -> BoardArtifact:
    d = day if day is not None else ctx.repos.board.last_day(ctx.today())
    if d is None:
        raise NoData(f"아직 없음 — {BOARD_DAILY_ETA}")
    art = ctx.repos.board.artifact(d, name)
    if art is None:
        raise NoData(f"아직 없음 — {d} 보드 산출 없음({BOARD_DAILY_ETA})")
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
    raw_scope = p.get("hist_scope")
    scope = cast(Mapping[str, Any], raw_scope) if isinstance(raw_scope, Mapping) else None
    notes = hist_notes(scope)
    codes = [str(r.get("code")) for r in rows if r.get("code")]
    p.update(
        achieved=rows,
        hist_notes=notes,
        flows=_flows_for(ctx, art.trade_date, codes),
        filter=BoardFilter(
            basis=basis, kind=kind, min_turnover_eok=min_turnover_eok, n_before_filter=n_before
        ),
    )
    # 품질 메모는 data.hist_notes 한 곳에만 — 화면이 기준 줄에 한 번 적는다(봉투 notes 에 또
    # 싣지 않는다)
    return _wrap(ctx, art, BoardNewhigh.model_validate(p), [])


def board_sectors(ctx: ReadContext, *, day: date | None) -> Envelope[BoardSectors]:
    art = _artifact(ctx, "sectors", day)
    return _wrap(ctx, art, BoardSectors.model_validate(dict(art.payload)), [])


def board_events(ctx: ReadContext, *, day: date | None) -> Envelope[BoardEvents]:
    art = _artifact(ctx, "events", day)
    return _wrap(ctx, art, BoardEvents.model_validate(dict(art.payload)), [])


def board_rankings(ctx: ReadContext, *, day: date | None) -> Envelope[BoardRankings]:
    art = _artifact(ctx, "rankings", day)
    return _wrap(ctx, art, BoardRankings.model_validate(dict(art.payload)), [])
