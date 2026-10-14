"""관심종목 reader — `prv_journal.watchlist`(로그인·개인 데이터, D-P4-13) + 종목마다 마지막 시세.

목록은 가져오기 CLI(`python -m kbj.services.watchlist import`)와 쓰기 API(P8 — `PUT·DELETE
/api/watchlist/{list}/{code}`·순서)가 쓴다(쓰기 주체는 `kbj.services.watchlist` 하나). 공개판은
관심종목을 모른다. 목록이 비면 200 + 빈 목록(가져오기 안내는 notes) — 아직 넣지 않은 것이지 수집이
늦은 것이 아니다.
"""

from __future__ import annotations

from typing import Final

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import close_as_of
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.stock import Watchlist, WatchRow
from kbj.services.api.readers._common import ReadContext, envelope, label, worst
from kbj.services.api.readers.stock import Lookup, kind_of

__all__ = ["IMPORT_HINT", "watchlist"]

IMPORT_HINT: Final = (
    "관심종목 없음 — 종목 상세(페이지 8) 별 버튼 또는 "
    "python -m kbj.services.watchlist import --from sd-json <파일> 로 넣는다"
)


def watchlist(ctx: ReadContext, *, list_name: str) -> Envelope[Watchlist]:
    lk = Lookup(ctx)
    items = ctx.repos.journal.watchlist(list_name)
    snaps, sday = lk.snapshot
    rows: list[WatchRow] = []
    for it in items:
        u = lk.universe.get(it.code)
        s = snaps.get(it.code)
        rows.append(
            WatchRow(
                code=it.code,
                name=(u.name if u is not None else None) or it.name_at_add,
                sort=it.sort,
                added_at=it.added_at,
                note=it.note,
                source=it.source,
                market=u.market if u is not None else None,
                kind=kind_of(u.kind) if u is not None else None,
                date=s.date if s is not None else None,
                close=s.close if s is not None else None,
                chg_pct=s.chg_pct if s is not None else None,
                mktcap=s.mktcap if s is not None else None,
                quality=s.quality if s is not None else None,
            )
        )
    notes: list[str] = [] if items else [IMPORT_HINT]
    missing = [r.code for r in rows if r.date is None]
    if missing:
        notes.append(f"시세 없는 종목 {len(missing)}개(유니버스 밖이거나 수집 전)")
    srcs = {s.source for it in items if (s := snaps.get(it.code)) is not None}
    return envelope(
        Watchlist(list_name=list_name, n=len(rows), items=rows),
        source=label(srcs),
        as_of=close_as_of(sday) if sday is not None and srcs else ctx.now(),
        quality=worst(r.quality for r in rows) or Quality.OK,
        notes=notes,
        generated_at=ctx.now(),
    )
